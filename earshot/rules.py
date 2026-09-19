"""Strict, locally serializable correction rules and pure event matching.

Rules do not persist themselves or decide whether a critical event may be
suppressed. Policy owns persistence, protection, and collapse-window behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
import math
from numbers import Integral, Real
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Scalar = str | int | float | bool | None
_MISSING = object()
_FIELD_NAME = re.compile(r"(?:signals\.|features\.)?[A-Za-z][A-Za-z0-9_]*\Z")


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", allow_inf_nan=False, validate_assignment=True,
    )


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Value must contain non-whitespace characters")
    return value


class _Scope(_StrictModel):
    turbine_id: str | None
    alarm_code: int | None
    signal: str | None

    @field_validator("turbine_id", "signal")
    @classmethod
    def populated_names(cls, value: str | None) -> str | None:
        return _nonblank(value) if value is not None else None


class _Condition(_StrictModel):
    field: str
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte"]
    value: Scalar

    @field_validator("field")
    @classmethod
    def supported_field(cls, value: str) -> str:
        if not _FIELD_NAME.fullmatch(value):
            raise ValueError("Use a direct field name or signals.name / features.name")
        return value

    @model_validator(mode="after")
    def ordered_values_are_numeric(self) -> Self:
        if self.op in {"lt", "lte", "gt", "gte"}:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("Ordered conditions require a finite numeric value")
        return self


class _Pattern(_StrictModel):
    kind: Literal["code_match", "conditions", "learned"]
    window_s: int = Field(ge=0)
    conditions: list[_Condition]

    @model_validator(mode="after")
    def explicit_conditions(self) -> Self:
        if self.kind in {"conditions", "learned"} and not self.conditions:
            raise ValueError("A conditions or learned pattern requires at least one condition")
        return self


def _event_value(event: Mapping[str, Any], field: str) -> Any:
    """Look up a literal mapping key; never evaluate text or traverse objects."""
    if "." in field:
        namespace, name = field.split(".", 1)
        values = event.get(namespace)
        return values.get(name, _MISSING) if isinstance(values, Mapping) else _MISSING
    if field in event:
        return event[field]
    for namespace in ("signals", "features"):
        values = event.get(namespace)
        if isinstance(values, Mapping) and field in values:
            return values[field]
    return _MISSING


def _json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, Integral):
        return True
    return isinstance(value, Real) and math.isfinite(value)


def _condition_matches(condition: _Condition, event: Mapping[str, Any]) -> bool:
    actual = _event_value(event, condition.field)
    if actual is _MISSING or not _json_scalar(actual):
        return False
    expected = condition.value
    if condition.op in {"eq", "ne"}:
        # JSON booleans are distinct from numbers even though Python's True == 1.
        equal = actual == expected
        if isinstance(actual, bool) != isinstance(expected, bool):
            equal = False
        return bool(equal if condition.op == "eq" else not equal)
    if isinstance(actual, bool) or not isinstance(actual, Real):
        return False
    if condition.op == "lt":
        return bool(actual < expected)
    if condition.op == "lte":
        return bool(actual <= expected)
    if condition.op == "gt":
        return bool(actual > expected)
    return bool(actual >= expected)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value: {value}")


class SuppressionRule(_StrictModel):
    """The nine prompt fields, with explicit scope and supported AND predicates.

    Every scope key is required. None is an explicit wildcard. Matching does
    not apply action semantics: suppress/collapse/escalate belong to policy.
    ``window_s`` is nonnegative metadata for policy; no temporal pattern is
    inferred. A learned pattern defines classifier eligibility, not immediate
    deterministic suppression; its use by a future parser is not assumed.
    Construction, matching and serialization perform no I/O.
    """

    rule_id: str
    utterance: str
    scope: dict[str, str | int | None]
    pattern: dict[str, Any]
    action: Literal["suppress", "collapse", "escalate"]
    confidence: float = Field(ge=0, le=1)
    taught_by: str
    taught_at: str
    reversible: bool

    @field_validator("rule_id", "utterance", "taught_by")
    @classmethod
    def populated_text(cls, value: str) -> str:
        return _nonblank(value)

    @field_validator("scope", mode="before")
    @classmethod
    def validate_scope(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("scope must be an object with all three scope keys")
        return _Scope.model_validate(value).model_dump()

    @field_validator("pattern", mode="before")
    @classmethod
    def validate_pattern(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("pattern must be an object with kind, window_s and conditions")
        return _Pattern.model_validate(value).model_dump()

    @field_validator("taught_at")
    @classmethod
    def aware_iso_timestamp(cls, value: str) -> str:
        if "T" not in value:
            raise ValueError("taught_at must be a timezone-aware ISO 8601 timestamp")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("taught_at must be a timezone-aware ISO 8601 timestamp") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("taught_at must include Z or a timezone offset")
        return value

    def to_json(self) -> str:
        """Validate the current fields and return deterministic strict JSON.

        Revalidation catches invalid mutations inside scope/pattern dicts.
        No fields, source events, files or model state are changed.
        """
        validated = type(self).model_validate(self.model_dump())
        return json.dumps(validated.model_dump(), allow_nan=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> Self:
        """Validate a JSON object without coercions, duplicate keys or I/O."""
        if not isinstance(payload, str):
            raise TypeError("Rule JSON must be a string")
        values = json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant)
        return cls.model_validate(values)

    def matches_scope(self, event: dict[str, Any]) -> bool:
        """Match every non-wildcard scope field, independently of conditions.

        A signal can be named directly by event.signal or be an actual key in
        event.signals/event.features. Missing constrained fields never match.
        Invalid nested scope mutations raise validation errors, never broaden.
        """
        if not isinstance(event, Mapping):
            return False
        scope = _Scope.model_validate(self.scope)
        if scope.turbine_id is not None and event.get("turbine_id") != scope.turbine_id:
            return False
        if scope.alarm_code is not None:
            code = event.get("alarm_code")
            if isinstance(code, bool) or not isinstance(code, Integral) or code != scope.alarm_code:
                return False
        if scope.signal is not None:
            direct = event.get("signal") == scope.signal
            included = any(
                isinstance(event.get(namespace), Mapping) and scope.signal in event[namespace]
                for namespace in ("signals", "features")
            )
            if not direct and not included:
                return False
        return True

    def matches(self, event: dict[str, Any]) -> bool:
        """Match scope AND every declared condition without mutating the event.

        Bare condition fields resolve event first, then signals, then features.
        Explicit signals.name/features.name select just that namespace. Missing,
        non-scalar or nonfinite observations fail every predicate, including ne.
        Unsupported patterns are rejected; temporal behavior is never guessed.
        """
        pattern = _Pattern.model_validate(self.pattern)
        return self.matches_scope(event) and all(
            _condition_matches(condition, event) for condition in pattern.conditions
        )
