"""
Hurst exponent calculation for regime detection

Hurst Exponent Interpretation:
- H < 0.5: Mean-reverting (good for basis/funding strategies)
- H = 0.5: Random walk (no edge)
- H > 0.5: Trending (avoid mean-reversion strategies)
"""
import numpy as np
from typing import List, Tuple, Optional


def calculate_hurst_exponent(
    values: List[float],
    min_lag: int = 2,
    max_lag: int = None
) -> float:
    """
    Calculate Hurst exponent using R/S (Rescaled Range) method.

    Args:
        values: Time series values
        min_lag: Minimum lag for calculation
        max_lag: Maximum lag (defaults to len(values) // 4)

    Returns:
        Hurst exponent (0 to 1)
    """
    ts = np.array(values)
    n = len(ts)

    if n < 20:
        return 0.5  # Not enough data, assume random walk

    if max_lag is None:
        max_lag = min(n // 4, 100)

    if max_lag <= min_lag:
        max_lag = min_lag + 10

    lags = range(min_lag, max_lag + 1)
    rs_values = []

    for lag in lags:
        rs = _calculate_rs(ts, lag)
        if rs > 0:
            rs_values.append((lag, rs))

    if len(rs_values) < 5:
        return 0.5

    # Linear regression of log(R/S) vs log(lag)
    log_lags = np.log([x[0] for x in rs_values])
    log_rs = np.log([x[1] for x in rs_values])

    # OLS regression
    slope, _ = np.polyfit(log_lags, log_rs, 1)

    # Clamp to valid range
    return float(max(0, min(1, slope)))


def _calculate_rs(ts: np.ndarray, lag: int) -> float:
    """
    Calculate R/S statistic for a given lag.

    Args:
        ts: Time series array
        lag: Window size

    Returns:
        Average R/S value across all windows
    """
    n = len(ts)
    num_windows = n // lag
    rs_sum = 0.0
    valid_windows = 0

    for i in range(num_windows):
        window = ts[i * lag:(i + 1) * lag]

        # Mean-adjusted cumulative deviations
        mean = np.mean(window)
        deviations = window - mean
        cumsum = np.cumsum(deviations)

        # Range
        R = np.max(cumsum) - np.min(cumsum)

        # Standard deviation
        S = np.std(window, ddof=1)

        if S > 0:
            rs_sum += R / S
            valid_windows += 1

    return rs_sum / valid_windows if valid_windows > 0 else 0


def calculate_hurst_dfa(values: List[float], min_window: int = 4, max_window: int = None) -> float:
    """
    Calculate Hurst exponent using Detrended Fluctuation Analysis (DFA).
    More robust than R/S method for noisy data.

    Args:
        values: Time series values
        min_window: Minimum window size
        max_window: Maximum window size

    Returns:
        Hurst exponent (0 to 1)
    """
    ts = np.array(values)
    n = len(ts)

    if n < 50:
        return 0.5

    if max_window is None:
        max_window = n // 4

    # Integrate the series
    mean = np.mean(ts)
    integrated = np.cumsum(ts - mean)

    # Calculate fluctuation for different window sizes
    window_sizes = []
    fluctuations = []

    window = min_window
    while window <= max_window:
        window_sizes.append(window)
        fluctuations.append(_calculate_fluctuation(integrated, window))
        window = int(window * 1.25)  # Logarithmic spacing

    if len(window_sizes) < 5:
        return 0.5

    # Linear regression in log-log space
    log_windows = np.log(window_sizes)
    log_fluct = np.log(fluctuations)

    slope, _ = np.polyfit(log_windows, log_fluct, 1)

    return float(max(0, min(1, slope)))


def _calculate_fluctuation(integrated: np.ndarray, window: int) -> float:
    """Calculate RMS fluctuation for DFA"""
    n = len(integrated)
    num_windows = n // window
    fluct_sum = 0.0

    for i in range(num_windows):
        segment = integrated[i * window:(i + 1) * window]

        # Fit linear trend
        x = np.arange(window)
        slope, intercept = np.polyfit(x, segment, 1)
        trend = slope * x + intercept

        # Calculate RMS of detrended segment
        detrended = segment - trend
        fluct_sum += np.sqrt(np.mean(detrended ** 2))

    return fluct_sum / num_windows if num_windows > 0 else 0


def get_regime(hurst: float) -> str:
    """
    Get market regime description from Hurst exponent.

    Args:
        hurst: Hurst exponent value

    Returns:
        Regime description string
    """
    if hurst < 0.4:
        return "strongly_mean_reverting"
    elif hurst < 0.5:
        return "mean_reverting"
    elif hurst < 0.55:
        return "random_walk"
    elif hurst < 0.65:
        return "trending"
    else:
        return "strongly_trending"


def is_favorable_regime(hurst: float, threshold: float = 0.5) -> bool:
    """
    Check if current regime is favorable for mean-reversion strategies.

    Args:
        hurst: Current Hurst exponent
        threshold: Maximum Hurst value for favorable regime

    Returns:
        True if regime is favorable
    """
    return hurst < threshold
