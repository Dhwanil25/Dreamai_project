"""Sensor-independent anomaly detector contracts for later implementation."""

from typing import Protocol


class Detector(Protocol):
    """Structural interface accepted by the policy layer."""

    def score(self, features: dict[str, float]) -> float:
        """Return an anomaly score for named numeric feature values.

        Scoring must not fit the observation or perform I/O. The concrete
        implementation defines its score scale; this scaffold always raises.
        """
        raise NotImplementedError

    def update(self, features: dict[str, float]) -> None:
        """Learn one observation from named numeric feature values.

        Return None. The future implementation mutates local detector state
        without network or file I/O; this scaffold always raises.
        """
        raise NotImplementedError


class ZScoreDetector:
    """Rolling z-score detector shell with no fitted state or computation."""

    def __init__(self, window: int | None = None) -> None:
        """Initialize a detector with the given rolling sample count.

        An omitted window will use detector configuration. Return None; the
        future implementation initializes numeric history without assuming
        domain-specific feature names. This stub allocates no model state.
        """
        raise NotImplementedError

    def score(self, features: dict[str, float]) -> float:
        """Return an absolute z-score for the supplied numeric features.

        Use previously observed history without fitting this observation or
        performing I/O. The later implementation must define warmup and
        zero-variance behavior. This stub performs no scoring.
        """
        raise NotImplementedError

    def update(self, features: dict[str, float]) -> None:
        """Add the supplied numeric feature values to rolling history.

        Return None. The future implementation mutates bounded local detector
        state only; this stub does not retain features or perform I/O.
        """
        raise NotImplementedError
