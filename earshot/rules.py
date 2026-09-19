"""Serializable operator-correction schema and unimplemented operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SuppressionRule:
    """An operator correction with scope, pattern and provenance.

    Scope contains turbine_id, alarm_code and signal, with None denoting a
    wildcard. Pattern contains kind, window_s and conditions. Action is
    suppress, collapse or escalate; taught_at is an ISO 8601 timestamp.
    Construction stores the supplied fields only: validation, matching,
    serialization and persistence are not implemented in Phase 2.
    """

    rule_id: str
    utterance: str
    scope: dict[str, str | int | None]
    pattern: dict[str, Any]
    action: str
    confidence: float
    taught_by: str
    taught_at: str
    reversible: bool

    def to_json(self) -> str:
        """Return a JSON string preserving every rule field.

        Takes no arguments and will not mutate this rule or write files.
        JSONL persistence belongs to PolicyLayer.learn. This scaffold raises
        without serializing or performing I/O.
        """
        raise NotImplementedError

    @classmethod
    def from_json(cls, payload: str) -> SuppressionRule:
        """Build a rule from a JSON object encoded in payload.

        Return a SuppressionRule after validating its fields; malformed input
        will be rejected by the later implementation. No files or model state
        are changed. This scaffold raises without parsing the supplied text.
        """
        raise NotImplementedError

    def matches(self, event: dict[str, Any]) -> bool:
        """Determine whether a normalized event satisfies this rule.

        Return True only when every non-wildcard scope field and the declared
        pattern match. The future implementation does not mutate the rule or
        event, score a detector, or perform I/O. This stub always raises.
        """
        raise NotImplementedError
