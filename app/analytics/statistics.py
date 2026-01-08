"""
Rolling statistics calculations for trading signals
"""
import numpy as np
from typing import List, Optional, Tuple
from dataclasses import dataclass
from collections import deque


@dataclass
class RollingStatsResult:
    """Result of rolling statistics calculation"""
    mean: float
    std: float
    zscore: float
    min_value: float
    max_value: float
    count: int


class RollingStats:
    """Efficient rolling statistics calculator using Welford's algorithm"""

    def __init__(self, window_size: int):
        """
        Initialize rolling statistics calculator.

        Args:
            window_size: Number of observations in the rolling window
        """
        self.window_size = window_size
        self._values = deque(maxlen=window_size)
        self._sum = 0.0
        self._sum_sq = 0.0

    def update(self, value: float) -> RollingStatsResult:
        """
        Add a new value and return updated statistics.

        Args:
            value: New observation value

        Returns:
            RollingStatsResult with current statistics
        """
        # If window is full, remove oldest value from sums
        if len(self._values) == self.window_size:
            old_value = self._values[0]
            self._sum -= old_value
            self._sum_sq -= old_value * old_value

        # Add new value
        self._values.append(value)
        self._sum += value
        self._sum_sq += value * value

        return self.get_stats(value)

    def get_stats(self, current_value: float = None) -> RollingStatsResult:
        """
        Get current rolling statistics.

        Args:
            current_value: Value to calculate z-score for (defaults to last value)

        Returns:
            RollingStatsResult with current statistics
        """
        n = len(self._values)
        if n == 0:
            return RollingStatsResult(
                mean=0, std=0, zscore=0, min_value=0, max_value=0, count=0
            )

        mean = self._sum / n

        # Calculate variance using sum of squares
        variance = (self._sum_sq / n) - (mean * mean)
        std = np.sqrt(max(0, variance))  # Protect against floating point errors

        # Calculate z-score
        if current_value is None:
            current_value = self._values[-1]

        zscore = (current_value - mean) / std if std > 0 else 0

        return RollingStatsResult(
            mean=mean,
            std=std,
            zscore=zscore,
            min_value=min(self._values),
            max_value=max(self._values),
            count=n
        )

    def is_ready(self) -> bool:
        """Check if window is fully populated"""
        return len(self._values) >= self.window_size

    def reset(self) -> None:
        """Reset all statistics"""
        self._values.clear()
        self._sum = 0.0
        self._sum_sq = 0.0

    @property
    def values(self) -> List[float]:
        """Get all values in the window"""
        return list(self._values)


def calculate_zscore(values: List[float], current_value: float = None) -> float:
    """
    Calculate z-score for a value given a series.

    Args:
        values: Historical values
        current_value: Value to calculate z-score for (defaults to last value)

    Returns:
        Z-score of the current value
    """
    if len(values) < 2:
        return 0.0

    arr = np.array(values)
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)  # Sample standard deviation

    if std == 0:
        return 0.0

    if current_value is None:
        current_value = values[-1]

    return (current_value - mean) / std


def calculate_percentile(values: List[float], current_value: float) -> float:
    """
    Calculate percentile rank of a value in a series.

    Args:
        values: Historical values
        current_value: Value to calculate percentile for

    Returns:
        Percentile rank (0-100)
    """
    if len(values) == 0:
        return 50.0

    arr = np.array(values)
    return float(np.sum(arr <= current_value) / len(arr) * 100)


def calculate_rolling_correlation(
    series1: List[float],
    series2: List[float],
    window: int = 20
) -> List[float]:
    """
    Calculate rolling correlation between two series.

    Args:
        series1: First data series
        series2: Second data series
        window: Rolling window size

    Returns:
        List of rolling correlation values
    """
    if len(series1) != len(series2):
        raise ValueError("Series must have same length")

    n = len(series1)
    if n < window:
        return []

    correlations = []
    for i in range(window - 1, n):
        s1 = series1[i - window + 1:i + 1]
        s2 = series2[i - window + 1:i + 1]
        corr = np.corrcoef(s1, s2)[0, 1]
        correlations.append(corr if not np.isnan(corr) else 0)

    return correlations


def calculate_half_life(values: List[float]) -> Optional[float]:
    """
    Calculate half-life of mean reversion using OLS regression.

    Args:
        values: Time series values

    Returns:
        Half-life in number of periods, or None if not mean-reverting
    """
    if len(values) < 10:
        return None

    arr = np.array(values)

    # Calculate lagged differences
    lag = arr[:-1]
    diff = np.diff(arr)

    # OLS regression: diff = alpha + beta * lag + epsilon
    X = np.column_stack([np.ones(len(lag)), lag])
    try:
        beta = np.linalg.lstsq(X, diff, rcond=None)[0][1]
    except np.linalg.LinAlgError:
        return None

    # Half-life = -log(2) / beta
    if beta >= 0:
        return None  # Not mean-reverting

    half_life = -np.log(2) / beta
    return float(half_life)
