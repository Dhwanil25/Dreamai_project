"""Local anomaly detectors over opaque numeric feature names.

Call score before update. Neither detector knows the meaning of feature keys;
callers provide independent detector instances or disjoint keys as appropriate.
Missing None/NaN values are skipped; invalid inputs fail before learning.
"""

from __future__ import annotations

from collections import deque
import math
from numbers import Integral, Real
import statistics
import sys
from typing import Protocol

from river import anomaly, preprocessing

from earshot.config import CONFIG

FeatureValues = dict[str, float | int | None]
STD_FLOOR = 1e-9


class Detector(Protocol):
    """A numeric score/update interface with explicit fresh-instance cloning."""

    def score(self, features: FeatureValues) -> float:
        """Return a finite anomaly score without fitting or performing I/O."""
        raise NotImplementedError

    def update(self, features: FeatureValues) -> None:
        """Learn valid observed features locally, returning None; no I/O."""
        raise NotImplementedError

    def clone(self) -> Detector:
        """Return an unfitted instance with the same detector configuration."""
        raise NotImplementedError


def _window(value: int | None) -> int:
    """Resolve and validate a sample window large enough to estimate variation."""
    selected = CONFIG.detector.window if value is None else value
    if isinstance(selected, bool) or not isinstance(selected, Integral) or selected < 2:
        raise ValueError("window must be an integer of at least 2 samples")
    return int(selected)


def _observed(features: FeatureValues) -> dict[str, float]:
    """Validate an entire feature mapping before changing any detector state."""
    if not isinstance(features, dict):
        raise TypeError("features must be a dict of named numeric values")
    result: dict[str, float] = {}
    for key, value in features.items():
        if not isinstance(key, str) or not key.strip():
            raise TypeError("feature names must be nonblank strings")
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"feature {key!r} must be a real number, None or NaN")
        try:
            number = float(value)
        except (OverflowError, ValueError) as error:
            raise ValueError(f"feature {key!r} is outside the finite numeric range") from error
        if math.isnan(number):
            continue
        if not math.isfinite(number):
            raise ValueError(f"feature {key!r} must be finite")
        result[key] = number
    return result


class ZScoreDetector:
    """Independent bounded numeric histories keyed by caller-supplied names."""

    def __init__(self, window: int | None = None) -> None:
        """Initialize empty histories; None uses CONFIG.detector.window.

        A window is at least two observations per feature. Initialization has
        no file/network side effects and does not manufacture training samples.
        """
        self.window = _window(window)
        self._history: dict[str, deque[float]] = {}

    def score(self, features: FeatureValues) -> float:
        """Return the maximum absolute population z-score across mature features.

        Only past observations are used. Features with fewer than two samples,
        unknown keys and missing None/NaN values contribute zero. Divide by
        max(population_std, 1e-9): an unchanged constant scores zero, while a
        changed constant produces a finite large score. Extreme arithmetic is
        capped at the largest finite float. Reject infinities, booleans and
        other non-real values before scoring. Do not learn or perform I/O.
        """
        values = _observed(features)
        highest = 0.0
        for key, value in values.items():
            history = self._history.get(key)
            if history is None or len(history) < 2:
                continue
            mean = statistics.mean(history)
            denominator = max(statistics.pstdev(history), STD_FLOOR)
            distance = abs(value - mean)
            # Scaling first avoids subtraction overflow for opposite extremes.
            score = (distance / denominator if math.isfinite(distance)
                     else abs(value / denominator - mean / denominator))
            if not math.isfinite(score):
                score = sys.float_info.max
            highest = max(highest, score)
        return float(highest)

    def update(self, features: FeatureValues) -> None:
        """Append each valid nonmissing value to its own rolling sample history.

        Validate the whole mapping first; invalid input cannot partially learn.
        None/NaN values do not advance a history. Return None and modify only
        this detector's bounded local histories; no I/O occurs.
        """
        values = _observed(features)
        for key, value in values.items():
            if key not in self._history:
                self._history[key] = deque(maxlen=self.window)
            self._history[key].append(value)

    def clone(self) -> ZScoreDetector:
        """Return a fresh, unfitted detector with this sample-window size."""
        return ZScoreDetector(window=self.window)


class HSTDetector:
    """Seeded River HalfSpaceTrees with explicit read-only min/max scaling.

    The first nonmissing update establishes a fixed feature schema. Later
    updates may omit features, but adding new names requires a fresh detector.
    This prevents River trees from silently ignoring newly introduced keys.
    Use separate instances for independent streams. Scores are in [0, 1],
    unlike z-scores, and are not a calibrated incident probability.
    """

    def __init__(self, window: int | None = None) -> None:
        """Create local scaler/model state with configured sampling and seed 42.

        None resolves CONFIG.detector.window. River's reference mass window
        uses that size; MinMaxScaler tracks the observed range over the entire
        stream. No data is learned and no I/O occurs during initialization.
        """
        self.window = _window(window)
        self.seed = 42
        self.scaler = preprocessing.MinMaxScaler()
        self.model = anomaly.HalfSpaceTrees(window_size=self.window, seed=self.seed)
        self._feature_names: frozenset[str] = frozenset()

    def _scaled(self, values: dict[str, float]) -> dict[str, float]:
        """Transform learned keys only and clamp extrapolation to [0, 1]."""
        known = {key: value for key, value in values.items() if key in self._feature_names}
        transformed = self.scaler.transform_one(known)
        output: dict[str, float] = {}
        for key, value in transformed.items():
            if math.isnan(value):
                raise ValueError(f"feature {key!r} could not be scaled to a finite range")
            output[key] = min(1.0, max(0.0, float(value)))
        return output

    def score(self, features: FeatureValues) -> float:
        """Score observed known features without updating the scaler or trees.

        Validate numeric inputs exactly as ZScoreDetector does. Missing and
        unseen keys are omitted; an empty known vector scores zero. River
        returns zero until one full sample window has been learned. Clamp
        transformed extremes to [0, 1] before scoring; constant ranges follow
        River's zero transform. No learning, new-key allocation or I/O occurs.
        """
        values = _observed(features)
        scaled = self._scaled(values)
        if not scaled:
            return 0.0
        score = float(self.model.score_one(scaled))
        if not math.isfinite(score):
            raise ValueError("HalfSpaceTrees produced a nonfinite anomaly score")
        return min(1.0, max(0.0, score))

    def update(self, features: FeatureValues) -> None:
        """Learn one valid nonempty vector with scaler-first normalization.

        Validate all values and the established feature schema before learning.
        Missing-only vectors do not advance the mass window. On first update,
        observed names establish the schema; a later new name raises ValueError.
        Learn the min/max range, clamp transformed values, then update the trees.
        Return None; only local model/scaler state changes and no I/O occurs.
        """
        values = _observed(features)
        if not values:
            return
        if self._feature_names:
            unknown = set(values) - self._feature_names
            if unknown:
                raise ValueError(f"HST feature schema cannot grow; use a fresh detector for {sorted(unknown)}")
        else:
            self._feature_names = frozenset(values)
        self.scaler.learn_one(values)
        self.model.learn_one(self._scaled(values))

    def clone(self) -> HSTDetector:
        """Return an unfitted detector with the same sample window and seed 42."""
        return HSTDetector(window=self.window)


def create_detector() -> Detector:
    """Instantiate the method selected only by CONFIG.detector.method.

    Supported values are exactly zscore and hst; an unknown method raises
    ValueError. The configured window is used by the selected constructor.
    Return a fresh local detector without learning or performing I/O.
    """
    method = CONFIG.detector.method
    if method == "zscore":
        return ZScoreDetector()
    if method == "hst":
        return HSTDetector()
    raise ValueError(f"Unknown detector method: {method!r}; expected 'zscore' or 'hst'")
