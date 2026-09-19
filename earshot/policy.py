"""Local scoped corrections, online learning, reversible history and replay counts.

Rules take immediate effect. A learned-pattern rule additionally authorizes
classifier decisions only inside its complete scope and conditions. Classifier
weights are reconstructed from JSON training batches on undo/restart; no pickle
or network services are used. Anomaly baselines remain separate from teaching.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from numbers import Integral, Real
import os
from pathlib import Path
from threading import RLock
from typing import Any

import pandas as pd
from river import linear_model, optim

from earshot.config import CONFIG
from earshot.detector import Detector
from earshot.rules import SuppressionRule


@dataclass(frozen=True)
class Verdict:
    show: bool
    suppressed_by: str | None
    confidence: float
    anomaly_score: float = 0.0


@dataclass
class _Observation:
    event: dict[str, Any]
    features: dict[str, float]
    timestamp: float
    verdict: Verdict
    collapse_after: dict[tuple[str, str], float]
    counted: bool


class PolicyLayer:
    """One site's engine; use one live writer per ledger file.

    ``rules_path`` defaults to the configured processed directory. The append-
    only JSONL journal stores learn/commit/undo operations, with exact validated
    rule payloads and numeric training batches. A complete learn record without
    a commit has no effect on restart. Malformed/truncated JSON raises instead
    of discarding history. Mutations are serialized within this instance.
    """

    def __init__(self, detector: Detector, buffer_size: int, *,
                 rules_path: Path | None = None, nuisance_threshold: float | None = None) -> None:
        if isinstance(buffer_size, bool) or not isinstance(buffer_size, Integral) or buffer_size <= 0:
            raise ValueError("buffer_size must be a positive integer")
        if not callable(getattr(detector, "score", None)) or not callable(getattr(detector, "update", None)):
            raise TypeError("detector must expose score and update")
        self.detector = detector
        self._detectors: dict[str, Detector] = {}
        self._records: deque[_Observation] = deque(maxlen=int(buffer_size))
        self._replay_buffer: deque | None = None
        self.rules_path = Path(rules_path) if rules_path is not None else CONFIG.data.processed_dir / "rules.jsonl"
        self.learning_rate, self.nuisance_threshold, self.collapse_window_s = self._settings(
            CONFIG.policy.learning_rate,
            CONFIG.policy.nuisance_probability if nuisance_threshold is None else nuisance_threshold,
            CONFIG.policy.collapse_window_s,
        )
        self.classifier = self._fresh_classifier()
        self._rules: dict[str, SuppressionRule] = {}
        self._batches: dict[str, list[dict]] = {}
        self._used_ids: set[str] = set()
        self._model_version = 1
        self._shown = self._suppressed = 0
        self._collapse_boundary: dict[tuple[str, str], float] = {}
        self._collapse_live: dict[tuple[str, str], float] = {}
        self._lock = RLock()
        self._restore()

    @staticmethod
    def _settings(learning_rate, nuisance_threshold, collapse_window_s) -> tuple[float, float, int]:
        if (isinstance(nuisance_threshold, bool) or not isinstance(nuisance_threshold, Real)
                or not math.isfinite(nuisance_threshold) or not 0.5 < nuisance_threshold <= 1):
            raise ValueError("nuisance_threshold must be greater than 0.5 and at most 1")
        if (isinstance(learning_rate, bool) or not isinstance(learning_rate, Real)
                or not math.isfinite(learning_rate) or learning_rate <= 0):
            raise ValueError("learning_rate must be finite and positive")
        if isinstance(collapse_window_s, bool) or not isinstance(collapse_window_s, Integral) or collapse_window_s <= 0:
            raise ValueError("collapse_window_s must be a positive integer")
        return float(learning_rate), float(nuisance_threshold), int(collapse_window_s)

    def _fresh_classifier(self):
        return linear_model.LogisticRegression(optimizer=optim.SGD(self.learning_rate))

    @property
    def model_version(self) -> int:
        return self._model_version

    @property
    def active_rules(self) -> tuple[SuppressionRule, ...]:
        return tuple(SuppressionRule.from_json(rule.to_json()) for rule in self._rules.values())

    @property
    def buffer(self) -> list[dict[str, Any]] | deque:
        if self._replay_buffer is not None:
            return self._replay_buffer
        return [deepcopy(record.event) for record in self._records]

    def bind_replay_buffer(self, buffer: deque) -> None:
        """Expose the replayer's live ring while retaining private scoring copies.

        The single replay consumer must score every yielded event exactly once.
        Internal immutable snapshots protect accepted decisions from subsequent
        caller edits to the shared input deque. Sizes must agree.
        """
        if not isinstance(buffer, deque) or buffer.maxlen != self._records.maxlen:
            raise ValueError("Replay and policy buffers must have the same bounded size")
        with self._lock:
            self._replay_buffer = buffer

    def reset_stream(self) -> None:
        """Clear observation history after seek, preserving taught corrections."""
        with self._lock:
            self._records.clear()
            self._detectors.clear()
            self._shown = self._suppressed = 0
            self._collapse_boundary.clear()
            self._collapse_live.clear()

    @property
    def verdicts(self) -> list[Verdict]:
        return [record.verdict for record in self._records]

    def observations(self) -> list[tuple[dict[str, Any], Verdict]]:
        """Return aligned scored copies, independent of the replay producer's ring."""
        with self._lock:
            return [(deepcopy(record.event), record.verdict) for record in self._records]

    def learning_state(self) -> dict[str, Any]:
        """Fingerprint the actual local classifier; expose no feature values."""
        with self._lock:
            state = {"weights": dict(self.classifier.weights), "intercept": self.classifier.intercept}
            encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            return {"model_version": self._model_version,
                    "classifier": "river.linear_model.LogisticRegression",
                    "classifier_state_sha256": sha256(encoded).hexdigest(),
                    "nonzero_weights": sum(value != 0 for value in self.classifier.weights.values()),
                    "training_batches": len(self._batches),
                    "training_examples": sum(len(batch) for batch in self._batches.values()),
                    "active_rules": len(self._rules)}

    @staticmethod
    def _numeric_features(event: dict[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for field in ("signals", "features"):
            values = event.get(field, {})
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ValueError(f"{field} must be a numeric feature mapping")
            for key, value in values.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("Feature names must be nonblank strings")
                if value is None or (isinstance(value, Real) and math.isnan(float(value))):
                    continue
                if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
                    raise ValueError(f"Feature {key} must be finite numeric or missing")
                if key in result and result[key] != float(value):
                    raise ValueError(f"Conflicting signal/feature values for {key}")
                result[key] = float(value)
        if "signal" in event and "value" in event and event["signal"] is not None:
            scalar = PolicyLayer._numeric_features({"features": {event["signal"]: event["value"]}})
            if any(key in result and result[key] != value for key, value in scalar.items()):
                raise ValueError("Conflicting scalar signal value")
            result.update(scalar)
        return result

    @staticmethod
    def _features(event: dict[str, Any], numeric: dict[str, float]) -> dict[str, float]:
        # Only the domain adapter knows these names. The classifier receives a
        # generic sparse dictionary: categorical IDs are never numeric magnitudes.
        entity, code = event["turbine_id"], event.get("alarm_code")
        result = {f"entity={entity}": 1.0}
        if code is not None:
            result[f"code={code}"] = 1.0
            result["entity_code=" + json.dumps([entity, int(code)], separators=(",", ":"))] = 1.0
        for key, value in numeric.items():
            result[f"value={key}"] = value / (1 + abs(value))
            result[f"present={key}"] = 1.0
        if event.get("signal") is not None:
            result[f"channel={event['signal']}"] = 1.0
        return result

    @staticmethod
    def _critical(event: dict[str, Any]) -> bool:
        return event.get("critical") is True or str(event.get("severity", "")).lower() in {"critical", "emergency"}

    def _protected(self, event: dict[str, Any]) -> bool:
        return self._critical(event) or any(rule.action == "escalate" and rule.matches(event) for rule in self._rules.values())

    def _verdict(self, event: dict[str, Any], features: dict[str, float], timestamp: float,
                 anomaly: float, collapsed: dict[tuple[str, str], float]) -> Verdict:
        if self._critical(event):
            return Verdict(True, None, 1.0, anomaly)
        matches = [rule for rule in self._rules.values() if rule.matches(event)]
        escalations = [rule for rule in matches if rule.action == "escalate"]
        if escalations:
            return Verdict(True, None, max(rule.confidence for rule in escalations), anomaly)
        for rule in matches:
            if rule.action == "suppress" and rule.pattern["kind"] != "learned":
                return Verdict(False, rule.rule_id, rule.confidence, anomaly)
        for rule in matches:
            if rule.action == "collapse":
                key = (rule.rule_id, event["turbine_id"])
                width = rule.pattern["window_s"] or self.collapse_window_s
                previous = collapsed.get(key)
                if previous is not None and 0 <= timestamp - previous < width:
                    return Verdict(False, rule.rule_id, rule.confidence, anomaly)
                collapsed[key] = timestamp
                return Verdict(True, None, rule.confidence, anomaly)
        probability = float(self.classifier.predict_proba_one(features).get(True, 0.0))
        eligible = [rule for rule in matches if rule.action == "suppress" and rule.pattern["kind"] == "learned"]
        if eligible and probability >= self.nuisance_threshold:
            return Verdict(False, f"classifier:{eligible[0].rule_id}", probability, anomaly)
        return Verdict(True, None, 1.0 - probability, anomaly)

    def score(self, event: dict[str, Any]) -> Verdict:
        """Score before fitting numeric observations, then buffer one decision.

        Per-entity detector clones keep separate physical baselines. Records
        without sensor measurements receive anomaly_score=0; alarm codes are
        never fabricated as sensor values. Arrival order is preserved.
        """
        with self._lock:
            if not isinstance(event, dict):
                raise TypeError("event must be a dictionary")
            snapshot = deepcopy(event)
            if not isinstance(snapshot.get("turbine_id"), str) or not snapshot["turbine_id"].strip():
                raise ValueError("event requires a string turbine_id")
            code = snapshot.get("alarm_code")
            if code is not None and (isinstance(code, bool) or not isinstance(code, Integral)):
                raise ValueError("alarm_code must be an integer when present")
            timestamp = pd.Timestamp(snapshot.get("ts"))
            if pd.isna(timestamp):
                raise ValueError("event requires a valid ts")
            if timestamp.tzinfo is not None:
                timestamp = timestamp.tz_convert("UTC").tz_localize(None)
            snapshot["ts"] = timestamp
            when = timestamp.value / 1_000_000_000
            numeric = self._numeric_features(snapshot)
            features = self._features(snapshot, numeric)
            entity = snapshot["turbine_id"]
            if entity not in self._detectors:
                self._detectors[entity] = self.detector.clone() if hasattr(self.detector, "clone") else deepcopy(self.detector)
            detector = self._detectors[entity]
            anomaly = float(detector.score(numeric))
            if not math.isfinite(anomaly) or anomaly < 0:
                raise ValueError("Detector must return a finite nonnegative anomaly score")
            collapsed = dict(self._collapse_live)
            verdict = self._verdict(snapshot, features, when, anomaly, collapsed)
            detector.update(numeric)
            if len(self._records) == self._records.maxlen:
                self._collapse_boundary = self._records[0].collapse_after.copy()
            counted = code is not None
            self._records.append(_Observation(snapshot, features, when, verdict, collapsed.copy(), counted))
            self._collapse_live = collapsed
            self._shown += int(counted and verdict.show)
            self._suppressed += int(counted and not verdict.show)
            return verdict

    def _append(self, record: dict) -> None:
        """Append a complete, durable local journal record; repair partial writes."""
        payload = (json.dumps({"schema_version": 1, **record}, sort_keys=True, allow_nan=False) + "\n").encode()
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rules_path.open("ab+") as handle:
            original_size = handle.tell()
            try:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                handle.truncate(original_size)
                handle.flush()
                raise

    def _rebuild(self, excluded: str | None = None):
        model = self._fresh_classifier()
        for rule_id, examples in self._batches.items():
            if rule_id != excluded:
                for example in examples:
                    model.learn_one(example["features"], bool(example["label"]), w=example.get("weight", 1.0))
        return model

    def _examples(self, rule: SuppressionRule) -> list[dict]:
        positives, negatives = [], []
        collapse_seen: dict[tuple[str, str], float] = {}
        for position, record in enumerate(self._records):
            event = record.event
            if rule.scope["alarm_code"] is not None and not record.counted:
                # A normal sensor sample is not a labelled non-nuisance alarm.
                continue
            matching = rule.matches(event)
            positive = matching and rule.action != "escalate" and not self._protected(event)
            if rule.action == "collapse" and positive:
                key = (rule.rule_id, event["turbine_id"])
                previous = collapse_seen.get(key)
                width = rule.pattern["window_s"] or self.collapse_window_s
                positive = previous is not None and 0 <= record.timestamp - previous < width
                if not positive:
                    collapse_seen[key] = record.timestamp
            if not matching and not self._protected(event):
                # Existing authorized positives must not become contradictory
                # negatives or be copied into this new rule's training batch.
                # Excluding them also lets undo remove their owning rule's data.
                if any(other.action == "suppress" and other.matches(event) for other in self._rules.values()):
                    continue
            sample = {"features": dict(record.features), "label": int(positive)}
            (positives if positive else negatives).append((position, sample))
        total = len(positives) + len(negatives)
        # Equal total class weight prevents a rare scoped correction from
        # disappearing under thousands of unrelated negative observations.
        for group in (positives, negatives):
            weight = total / (2 * len(group)) if positives and negatives else 1.0
            for _, sample in group:
                sample["weight"] = weight
        return [sample for _, sample in sorted(positives + negatives, key=lambda item: item[0])]

    def learn(self, rule: SuppressionRule) -> dict[str, Any]:
        """Persist rule, mint real-buffer examples, train, commit, then rescore.

        Two journal records make learning recoverable: a durable learn record
        precedes mining/training; commit stores the exact batch and revision.
        A complete learn record without a commit is ignored by restart replay.
        """
        with self._lock:
            accepted = SuppressionRule.from_json(rule.to_json())
            if accepted.rule_id in self._used_ids or accepted.rule_id in self._rules:
                raise ValueError(f"Rule ID has already been used: {accepted.rule_id}")
            self._append({"op": "learn", "rule": accepted.model_dump(mode="json")})
            self._rules[accepted.rule_id] = accepted
            previous_classifier = self.classifier
            try:
                examples = self._examples(accepted)
                candidate = deepcopy(self.classifier)
                for example in examples:
                    candidate.learn_one(example["features"], bool(example["label"]), w=example["weight"])
                version = self._model_version + 1
                self._append({"op": "commit", "rule_id": accepted.rule_id, "examples": examples,
                              "model_version": version, "learning_rate": self.learning_rate,
                              "nuisance_threshold": self.nuisance_threshold,
                              "collapse_window_s": self.collapse_window_s, "feature_encoding_version": 1})
            except BaseException:
                self._rules.pop(accepted.rule_id)
                self.classifier = previous_classifier
                raise
            self.classifier = candidate
            self._batches[accepted.rule_id] = examples
            self._used_ids.add(accepted.rule_id)
            self._model_version = version
            previously_shown = [record.verdict.show for record in self._records]
            self.rescore_buffer()
            newly_suppressed = sum(before and not record.verdict.show for before, record in zip(previously_shown, self._records))
            return {"rule_id": accepted.rule_id, "examples_learned": len(examples),
                    "model_version": version, "newly_suppressed_count": newly_suppressed}

    def undo(self, rule_id: str) -> bool:
        """Remove a reversible correction and all of its classifier influence."""
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None or not rule.reversible:
                return False
            candidate = self._rebuild(excluded=rule_id)
            version = self._model_version + 1
            self._append({"op": "undo", "rule_id": rule_id, "model_version": version})
            del self._rules[rule_id]
            del self._batches[rule_id]
            self.classifier = candidate
            self._model_version = version
            self.rescore_buffer()
            return True

    def rescore_buffer(self) -> int:
        """Re-evaluate cached features/anomaly scores without refitting detectors."""
        with self._lock:
            collapsed = {key: value for key, value in self._collapse_boundary.items() if key[0] in self._rules}
            changed = 0
            for record in self._records:
                previous = record.verdict
                verdict = self._verdict(record.event, record.features, record.timestamp, previous.anomaly_score, collapsed)
                changed += int(verdict != previous)
                if record.counted:
                    self._shown += int(verdict.show) - int(previous.show)
                    self._suppressed += int(not verdict.show) - int(not previous.show)
                record.verdict = verdict
                record.collapse_after = collapsed.copy()
            self._collapse_live = collapsed
            return changed

    def stats(self) -> dict[str, Any]:
        """Return cumulative decisions and a buffer-bounded trailing visible rate.

        Evicted decisions retain their last recorded visibility. The rate uses
        shown buffered records in (latest_ts-1h, latest_ts], divided by one hour;
        it is buffer-limited during startup/high traffic, not an extrapolation.
        """
        with self._lock:
            total = self._shown + self._suppressed
            end = max((record.timestamp for record in self._records), default=0.0)
            current = sum(record.counted and record.verdict.show and end - 3600 < record.timestamp <= end for record in self._records)
            return {"alarms_shown": self._shown, "alarms_suppressed": self._suppressed,
                    "suppression_rate": self._suppressed / total if total else 0.0,
                    "model_version": self._model_version, "active_rules": len(self._rules),
                    "alarms_per_hour_current": float(current)}

    def _restore(self) -> None:
        if not self.rules_path.exists():
            return
        pending: dict[str, SuppressionRule] = {}
        try:
            lines = self.rules_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Policy journal records must be objects")
                if row.get("schema_version") != 1:
                    raise ValueError("Unsupported policy journal schema")
                operation = row.get("op")
                if operation == "learn":
                    rule = SuppressionRule.model_validate(row["rule"])
                    if rule.rule_id in self._used_ids:
                        raise ValueError("Duplicate committed rule ID")
                    pending[rule.rule_id] = rule
                    continue
                rule_id = row["rule_id"]
                if row["model_version"] != self._model_version + 1:
                    raise ValueError("Nonsequential policy journal revision")
                if operation == "commit":
                    if row["feature_encoding_version"] != 1:
                        raise ValueError("Unsupported classifier feature encoding")
                    rule = pending.pop(rule_id)
                    examples = row["examples"]
                    settings = self._settings(row["learning_rate"], row["nuisance_threshold"], row["collapse_window_s"])
                    if self._model_version == 1:
                        self.learning_rate, self.nuisance_threshold, self.collapse_window_s = settings
                    if settings != (self.learning_rate, self.nuisance_threshold, self.collapse_window_s):
                        raise ValueError("Policy journal settings changed within a history")
                    for example in examples:
                        if type(example["label"]) is not int or example["label"] not in (0, 1):
                            raise ValueError("Invalid persisted training label")
                        weight = example.get("weight", 1.0)
                        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
                            raise ValueError("Invalid persisted training weight")
                        if not isinstance(example["features"], dict) or any(
                            not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                            for key, value in example["features"].items()
                        ):
                            raise ValueError("Invalid persisted numeric training features")
                    self._rules[rule_id] = rule
                    self._batches[rule_id] = examples
                    self._used_ids.add(rule_id)
                elif operation == "undo":
                    if not self._rules[rule_id].reversible:
                        raise ValueError("Journal undoes a non-reversible rule")
                    del self._rules[rule_id]
                    del self._batches[rule_id]
                else:
                    raise ValueError("Unknown policy journal operation")
                self._model_version = row["model_version"]
            self.classifier = self._rebuild()
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid policy journal {self.rules_path.name}: {error}") from error
