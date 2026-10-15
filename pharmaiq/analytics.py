"""
Analytics for PharmaIQ — pure pandas / numpy, no database access.

* forecast_series()   – transparent demand forecast with an 80 % prediction interval
* spike_anomalies()   – robust (median / MAD) detector for unusual daily sales

Everything here is deliberately simple and explainable: a damped-trend simple
exponential smoothing model with weekday seasonality. Uncertainty is ALWAYS reported.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

Z80 = 1.2816   # z-score for an 80 % two-sided interval


def daily_series(days, values, start, end) -> pd.Series:
    """Build a zero-filled daily Series between start and end (inclusive)."""
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    s = pd.Series(0.0, index=idx)
    if days:
        raw = pd.Series(values, index=pd.to_datetime(list(days)), dtype=float)
        raw = raw.groupby(level=0).sum()
        s.loc[s.index.intersection(raw.index)] = raw.loc[s.index.intersection(raw.index)]
    return s


# ──────────────────────────────────────────────────────────────────────
#  Forecasting
# ──────────────────────────────────────────────────────────────────────
@dataclass
class ForecastResult:
    ok: bool
    reason: str = ""
    frame: pd.DataFrame = None            # columns: date, forecast, lower, upper
    total: float = 0.0
    total_lower: float = 0.0
    total_upper: float = 0.0
    history_days: int = 0
    confidence: str = "low"
    confidence_reason: str = ""
    backtest_mae: float = None
    naive_mae: float = None
    alpha: float = None
    notes: list = field(default_factory=list)


def _fit(vals: np.ndarray, dow: np.ndarray):
    n = len(vals)
    factors = np.ones(7)
    seasonal = n >= 28
    if seasonal:
        overall = vals.mean()
        if overall > 0:
            for k in range(7):
                mask = dow == k
                if mask.any():
                    factors[k] = vals[mask].mean() / overall
            factors = 1 + 0.5 * (factors - 1)          # shrink: avoid over-fitting weekdays
            factors = factors / factors.mean()
    des = vals / np.where(factors[dow] > 0, factors[dow], 1.0)

    best_alpha, best_sse, best_level, best_errs = 0.3, np.inf, des[0], []
    for alpha in np.linspace(0.05, 0.95, 19):
        level, errs = des[0], []
        for t in range(1, n):
            e = des[t] - level
            errs.append(e)
            level += alpha * e
        sse = float(np.sum(np.square(errs)))
        if sse < best_sse:
            best_alpha, best_sse, best_level, best_errs = alpha, sse, level, errs

    tail = des[-min(28, n):]
    slope = float(np.polyfit(np.arange(len(tail)), tail, 1)[0]) if len(tail) >= 7 else 0.0
    sigma = float(np.std(best_errs)) if len(best_errs) > 1 else 0.0
    return dict(factors=factors, alpha=best_alpha, level=best_level, slope=slope, sigma=sigma,
                seasonal=seasonal)


def _predict(model, last_date, horizon):
    phi = 0.9
    rows = []
    for h in range(1, horizon + 1):
        d = last_date + pd.Timedelta(days=h)
        trend = model["slope"] * sum(phi ** i for i in range(1, h + 1))
        base = max(0.0, model["level"] + trend)
        f = base * model["factors"][d.weekday()]
        width = Z80 * model["sigma"] * np.sqrt(1 + (h - 1) * model["alpha"] ** 2) * model["factors"][d.weekday()]
        rows.append((d, f, max(0.0, f - width), f + width))
    return pd.DataFrame(rows, columns=["date", "forecast", "lower", "upper"])


def forecast_series(y: pd.Series, horizon: int = 14) -> ForecastResult:
    """Forecast daily units for `horizon` days after the last date in `y`."""
    horizon = int(max(1, min(horizon, 60)))
    n = len(y)
    if n < 14 or float(y.sum()) <= 0:
        return ForecastResult(
            ok=False, history_days=n,
            reason="Not enough sales history to forecast reliably (need at least 14 days with some sales).")
    vals = y.values.astype(float)
    dow = np.array([d.weekday() for d in y.index])
    model = _fit(vals, dow)
    frame = _predict(model, y.index[-1], horizon)

    # back-test on the last 7 days (only if enough remains to train on)
    mae = naive = None
    if n - 7 >= 14:
        m2 = _fit(vals[:-7], dow[:-7])
        bt = _predict(m2, y.index[-8], 7)
        mae = float(np.mean(np.abs(bt["forecast"].values - vals[-7:])))
        naive = float(np.mean(np.abs(vals[-14:-7].mean() - vals[-7:])))

    mean = float(vals.mean())
    cv = float(vals.std() / mean) if mean > 0 else 9.9
    if n < 28 or cv > 1.0:
        conf = "low"
    elif n >= 56 and cv < 0.6:
        conf = "high"
    else:
        conf = "medium"
    reason = f"{n} days of history, variability (CV) {cv:.2f}"
    notes = []
    if not model["seasonal"]:
        notes.append("Fewer than 28 days of history: weekday seasonality was not modelled.")
    if mae is not None and naive is not None and mae > naive:
        notes.append("In a 7-day back-test this model was not better than a simple average — treat with extra caution.")

    # Total interval: daily errors are neither perfectly independent nor perfectly correlated,
    # so use the midpoint of the two extremes (conservative but not absurdly wide).
    tot = float(frame["forecast"].sum())
    halves = (frame["upper"] - frame["forecast"]).values
    half = 0.5 * (float(np.sum(halves)) + float(np.sqrt(np.sum(np.square(halves)))))
    return ForecastResult(
        ok=True, frame=frame, total=tot, total_lower=max(0.0, tot - half), total_upper=tot + half,
        history_days=n, confidence=conf, confidence_reason=reason,
        backtest_mae=mae, naive_mae=naive, alpha=float(model["alpha"]), notes=notes,
    )


# ──────────────────────────────────────────────────────────────────────
#  Anomaly detection
# ──────────────────────────────────────────────────────────────────────
def spike_anomalies(series: pd.Series, window: int = 28, z_threshold: float = 3.5,
                    min_history: int = 10, min_units: float = 5.0):
    """Flag days that are far from the recent baseline.

    baseline = median of the previous `window` days; scale = 1.4826 × MAD
    (floored so a perfectly flat history does not give infinite z-scores).
    Returns a list of dicts: date, value, expected, z_score, kind.
    """
    out = []
    vals = series.values.astype(float)
    for t in range(min_history, len(vals)):
        prev = vals[max(0, t - window):t]
        med = float(np.median(prev))
        mad = float(np.median(np.abs(prev - med))) * 1.4826
        scale = max(mad, 0.5 * max(med, 1.0), 1.0)
        z = (vals[t] - med) / scale
        if z >= z_threshold and vals[t] >= max(min_units, 1.5 * med):
            out.append(dict(date=series.index[t].date().isoformat(), value=float(vals[t]),
                            expected=round(med, 1), z_score=round(float(z), 1), kind="spike"))
        elif z <= -z_threshold and med >= min_units * 2:
            out.append(dict(date=series.index[t].date().isoformat(), value=float(vals[t]),
                            expected=round(med, 1), z_score=round(float(z), 1), kind="drop"))
    return out
