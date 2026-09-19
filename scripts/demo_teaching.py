#!/usr/bin/env python3
"""Inspect text → rule → local learning using real events and a temporary ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.detector import create_detector
from earshot.parse import parse_utterance
from earshot.policy import PolicyLayer


def main() -> None:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--text", default="ignore the cable untwist alarm on turbine four")
    args = arguments.parse_args()
    context = json.loads((PROJECT_ROOT / "demo/vocabulary.json").read_text())
    rule = parse_utterance(args.text, context)
    print("Operator:", args.text)
    if rule is None:
        print("I didn't catch a rule in that. No learning was applied.")
        return
    print("Proposed rule:", rule.to_json())
    alarms = pd.read_parquet(CONFIG.data.processed_dir / "alarms.parquet")
    events = alarms.to_dict("records")
    first = next((position for position, event in enumerate(events) if rule.matches(event)), None)
    if first is None:
        print("No real events match this proposal. No learning was applied.")
        return
    # Use an actual contiguous interval containing the first matching event.
    # The default untwist/T04 event occurs beyond the dataset's first 2000 rows.
    end = min(len(events), max(first + 1, 2000))
    recent = events[max(0, end - 2000):end]
    with TemporaryDirectory(prefix="earshot-teaching-") as temporary:
        ledger = Path(temporary) / "rules.jsonl"
        policy = PolicyLayer(create_detector(), buffer_size=2000, rules_path=ledger)
        for event in recent:
            policy.score(event)
        print("Real buffer:", len(recent), "events,", str(recent[0]["ts"]), "to", str(recent[-1]["ts"]))
        print("Before:", json.dumps(policy.stats(), sort_keys=True))
        start = perf_counter()
        learned = policy.learn(rule)
        milliseconds = (perf_counter() - start) * 1000
        print("Learn:", json.dumps(learned, sort_keys=True))
        print("Learn milliseconds:", round(milliseconds, 3))
        print("After:", json.dumps(policy.stats(), sort_keys=True))
        print("Nonzero classifier weights:", sum(value != 0 for value in policy.classifier.weights.values()))
        restored = PolicyLayer(create_detector(), 2000, rules_path=ledger)
        print("Restart preserves classifier:", restored.classifier.weights == policy.classifier.weights
              and restored.classifier.intercept == policy.classifier.intercept)
        later = next((event for event in events[end:] if rule.matches(event)), None)
        if later is not None:
            verdict = restored.score(later)
            print("Later real matching event:", str(later["ts"]), "show =", verdict.show)
        if rule.scope["turbine_id"] is not None:
            other = next((event for event in events[end:]
                          if event["alarm_code"] == rule.scope["alarm_code"]
                          and event["turbine_id"] != rule.scope["turbine_id"]), None)
            if other is not None:
                print("Same code on another turbine:", other["turbine_id"], "show =", restored.score(other).show)
        policy.undo(rule.rule_id)
        print("Undo:", json.dumps(policy.stats(), sort_keys=True))
        print("Temporary demonstration complete; the site's saved corrections were unchanged.")


if __name__ == "__main__":
    main()
