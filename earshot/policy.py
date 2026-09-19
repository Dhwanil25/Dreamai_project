"""Policy and verdict contracts; no suppression or learning is implemented."""

from dataclasses import dataclass
from typing import Any

from earshot.detector import Detector
from earshot.rules import SuppressionRule


@dataclass
class Verdict:
    """Visibility decision, matching-rule identifier and score evidence.

    show determines event visibility; suppressed_by is a rule identifier or
    None; confidence describes the decision. anomaly_score carries detector
    output for the later engine/API contract. Construction stores fields only.
    """

    show: bool
    suppressed_by: str | None
    confidence: float
    anomaly_score: float = 0.0


class PolicyLayer:
    """Shell for scoped operator rules, online learning and local history."""

    def __init__(self, detector: Detector, buffer_size: int) -> None:
        """Initialize policy around a detector and recent-event capacity.

        Inputs are a score/update-compatible detector and a bounded event
        count. Return None. The future implementation initializes local rules,
        classifier state, recent history and model version 1, with configured
        local persistence. This stub creates no state and performs no I/O.
        """
        raise NotImplementedError

    def score(self, event: dict[str, Any]) -> Verdict:
        """Evaluate one normalized event and return its visibility verdict.

        The future implementation applies scoped rules and local model scores,
        recording the event and its decision in bounded history and updating
        display counters. It does not create an operator correction or contact
        a provider. This stub has no side effects.
        """
        raise NotImplementedError

    def learn(self, rule: SuppressionRule) -> dict[str, Any]:
        """Apply a correction and report its local learning result.

        Return rule_id, examples_learned, model_version and
        newly_suppressed_count. The future implementation persists the rule,
        builds matching examples and nonmatching counter-examples from recent
        events, updates a local classifier and increments the version. This
        stub neither persists a rule nor mutates model state.
        """
        raise NotImplementedError

    def undo(self, rule_id: str) -> bool:
        """Deactivate a correction identified by rule_id.

        Return whether an active reversible rule was undone. The future
        implementation restores the previous visibility behavior and records
        a new model version without deleting audit history. This stub does not
        change rules, scores, versions or stored files.
        """
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        """Return a snapshot of visibility and learning counters.

        Takes no arguments. Output includes alarms_shown, alarms_suppressed,
        suppression_rate, model_version, active_rules and
        alarms_per_hour_current. The future read will not change state or
        perform outbound I/O; this stub raises without reporting invented data.
        """
        raise NotImplementedError

    def rescore_buffer(self) -> int:
        """Re-evaluate buffered events against the current policy.

        Takes no arguments and returns the number of changed verdicts. The
        future implementation updates buffered decisions and corresponding
        counters without duplicating events or teaching the model again. This
        stub has no side effects.
        """
        raise NotImplementedError

    @property
    def model_version(self) -> int:
        """Return the current local policy/model version.

        Takes no arguments. The future value begins at 1 and advances with
        successful learning or undo; reading it does not change state. This
        scaffold raises because version storage is not implemented.
        """
        raise NotImplementedError
