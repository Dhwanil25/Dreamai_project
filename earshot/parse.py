"""Utterance parsing contract; no provider or vocabulary is loaded."""

from typing import Any

from earshot.rules import SuppressionRule


def parse_utterance(
    text: str,
    context: dict[str, Any],
) -> SuppressionRule | None:
    """Interpret operator text using the supplied real-data vocabulary.

    Context supplies known identifiers, alarm descriptions and any selected
    event. Return a validated rule, or None if no supported rule can be found.
    The future implementation honors offline controls before any optional
    provider call and does not persist rules or train a model. This scaffold
    always raises without parsing or performing I/O.
    """
    raise NotImplementedError
