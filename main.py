"""Forecast GTA VI's launch-day Twitch viewers, hour by hour.

Entry for make.fun competition gta6-twitch-ccu-forecast-01 (round 2).

The program reads one case file (schema "makefun/twitch-ccu-case/v1") and
writes, for each of hours 2..24 of the release day, five quantiles of that
hour's concurrent Twitch viewers in the game's category. It runs offline,
uses only the Python standard library, and is deterministic: the same case
always produces the same bytes.

## How it forecasts

A launch day's shape is mostly fixed by *when* each part of the world
unlocks, not by the game: New Zealand first, then Europe, the UK at 00:00Z,
the US east coast at 05:00Z and the west coast at 08:00Z. So past launches
with the same rolling-midnight unlock have the same intraday shape, once
each is rescaled by its own hour-1 print. The forecast is therefore built
from the case's own `comparables`, as an ensemble of four forecasters that
each see the same pool differently (their geometric mean is the entry):

1.  **Split-group normal (calibrated).** Keep only usable past launches:
    tier "comparable" (no early-access contamination, no weak/F2P
    analogue), no simultaneous global unlock, and an hour-1 print of at
    least 5,000 viewers. Split the pool in two by how each launch's hour-1
    print compares with its own pre-release level (x = hour1 / mean
    viewers in the 6 hours before the release day, threshold 0.6, minimum
    group size 4): launches whose hour 1 was small next to their
    pre-release evening surge much harder through the day. Per hour, the
    centre is the mean of ln(v_h / v_1) in the target's group and the
    spread its sample standard deviation (floored at 0.12), both lightly
    smoothed across neighbouring hours, and the spread is scaled by 0.85
    (the raw spread is slightly too wide for pinball loss on the log
    scale). Quantiles are q_tau = hour1 * exp(centre + z_tau * spread).
2.  **Two-feature regression.** Per hour, an ordinary least squares fit
    of ln(v_h / v_1) on ln(x) and ln(hour1) over the whole usable pool,
    evaluated at the target's own x and hour-1 print (both clamped to the
    pool's observed range, so an out-of-range target never extrapolates
    wildly), with the residual standard deviation as the spread. This
    lets the shape move continuously with the target instead of jumping
    at the split threshold.
3.  **Empirical quantiles.** The same split group as (1), but the five
    quantiles are the empirical quantiles of the group's log-ratios,
    shrunk halfway toward the normal quantiles of (1): the empirical
    shape keeps the real skew of past launches, the shrinkage keeps the
    small-group quantiles from jumping around.
4.  **Trimmed-mean normal.** As (1) with the centre a 20% trimmed mean
    of the group's log-ratios and spread scaled by 0.85, so one extreme
    past launch cannot drag an hour's centre.

The four forecasts are combined by geometric mean of their quantiles
(mean in log space), which is what an ensemble of log-scale forecasters
should average. If anything unexpected happens (no usable pool, missing
history, a malformed field) the program falls back to a plain
persistence forecast so the case still gets a valid output instead of
crashing.

## Local practice result

Scored against the four practice launches' real SullyGnome hourly
counts (Marvel's Wolverine, Red Dead Redemption 2, God of War
Ragnarok, Marvel's Spider-Man 2), this entry scores a mean pinball
loss of 0.167, against 0.387 for the house baseline and 0.172 for the
round-1 entry, with 90% coverage of 0.89. Leave-one-out over the 14
usable comparables (each past launch forecast from the other 13):
0.249, against 0.251 for round 1. In the final case the pool grows:
the final's comparables include all four practice launches as well,
so the split groups are larger there than on the practice. See
README.md for the per-case table and the three variants that were
compared.
"""

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

# The grader's -P leaves /bot off sys.path; nothing beside main.py is
# needed, so no path fix-up is required here.

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# The standard normal's quantile at each level.
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# A comparable only counts if its hour-1 print is at least this large:
# below it the print says more about the unlock hour than the launch.
MIN_HOUR1 = 5000
# Split of the pool by hour1 / pre-release level (see module docstring).
SPLIT_X = 0.6
# A group smaller than this is not trusted on its own; use the whole pool.
MIN_GROUP = 4
# The log-spread is never narrower than this: past launches always
# disagree by at least ~12% in log terms, even in the calmest hour.
SD_FLOOR = 0.12
# Fallback persistence spread (log units per sqrt hour), used only when
# the comparable pool is unusable.
FALLBACK_SIGMA = 0.15
# Ensemble calibration, tuned on the practice cases and on
# leave-one-out over the comparable pool (see module docstring):
# spread scale for the normal components, smoothing weight for a
# neighbouring hour's centre/spread, empirical shrinkage, trim share.
SPREAD_MULT = 0.85
SMOOTH_W = 0.25
EMP_SHRINK = 0.5
TRIM_SHARE = 0.2


def parse_time(text):
    """Parse an ISO 8601 timestamp with a trailing Z into an aware datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def round2_half_up(value):
    """Round to 2 decimals, half up, returning an int for whole numbers."""
    scaled = value * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    rounded = whole / 100.0
    return int(rounded) if float(rounded).is_integer() else rounded


def release_day_hours(comparable):
    """Hour number (1..24) -> viewers for a comparable's release day.

    Returns None when the comparable has no complete release-day curve.
    """
    day_start = parse_time(comparable["day_start"])
    hours = {}
    for row in comparable.get("hourly", []):
        when = parse_time(row["from"])
        delta = (when - day_start).total_seconds()
        if 0 <= delta < 24 * 3600:
            viewers = row.get("viewers")
            if isinstance(viewers, (int, float)) and viewers > 0:
                hours[int(delta // 3600) + 1] = float(viewers)
    if len(hours) < 24 or 1 not in hours:
        return None
    return hours


def is_usable(comparable):
    """Whether a comparable is a fair analogue for a rolling-midnight launch."""
    unlock = (comparable.get("unlock") or "").lower()
    platforms = (comparable.get("platforms") or "").lower()
    # Early-access launches peaked before their release day, so their
    # release-day curve is flat for the wrong reason.
    if "early-access-contaminated" in unlock or "tier: weak" in unlock:
        return False
    # A simultaneous global unlock puts the whole audience in hour 1;
    # a rolling midnight unlock does not. The two shapes do not mix.
    if "single global" in unlock or "simultaneous" in unlock:
        return False
    # Free-to-play and explicitly weak analogues launch differently.
    if "weak" in unlock or "f2p" in unlock or "free-to-play" in platforms:
        return False
    return True


def pre_window_mean(rows, day_start, window_hours):
    """Viewers of hourly rows in [day_start - window, day_start)."""
    start = day_start - timedelta(hours=window_hours)
    values = []
    for row in rows:
        when = parse_time(row["from"])
        viewers = row.get("viewers")
        if start <= when < day_start and isinstance(viewers, (int, float)):
            values.append(float(viewers))
    return values


def pre_release_level(rows, day_start):
    """Mean viewers in the 6 hours before a release day, or None.

    Falls back to the 24 hours before it when the 6-hour window has
    fewer than three sampled points (a quiet category is sampled
    sparsely). Returns None when there is no pre-release history.
    """
    for window_hours in (6, 24):
        values = pre_window_mean(rows, day_start, window_hours)
        if len(values) >= 3:
            return sum(values) / len(values)
    return None


def build_pool(case):
    """The usable comparables, each as {"curve": {...}, "pre6": float}.

    "curve" maps hour 1..24 of the comparable's release day to viewers;
    "pre6" is the comparable's own pre-release level, the mean viewers
    in the 6 hours before its release day (24 hours if too sparse).
    """
    pool = []
    for comparable in case.get("comparables", []):
        if not is_usable(comparable):
            continue
        curve = release_day_hours(comparable)
        if curve is None or curve[1] < MIN_HOUR1:
            continue
        level = pre_release_level(
            comparable.get("hourly", []), parse_time(comparable["day_start"])
        )
        if level is None or level <= 0:
            continue
        pool.append({"curve": curve, "pre6": level})
    return pool


def target_pre_level(case):
    """The target game's own pre-release level, or None."""
    rows = case.get("history_hourly") or []
    if not rows:
        return None
    return pre_release_level(rows, parse_time(case["day_start"]))


def pool_entries(pool):
    """Pool entries with their own hour1 / pre-release ratio x."""
    return [
        {"curve": entry["curve"], "pre6": entry["pre6"],
         "x": entry["curve"][1] / entry["pre6"]}
        for entry in pool
    ]


def split_group(entries, x_target):
    """The target's half of the pool by the x = hour1 / pre-level split."""
    low = [e for e in entries if e["x"] < SPLIT_X]
    high = [e for e in entries if e["x"] >= SPLIT_X]
    chosen = low if x_target < SPLIT_X else high
    if len(chosen) < MIN_GROUP:
        chosen = entries
    return chosen


def mean_and_sd(values):
    """Mean and sample standard deviation of a list of floats."""
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, SD_FLOOR
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, max(math.sqrt(variance), SD_FLOOR)


def trimmed_mean(values):
    """Mean after trimming TRIM_SHARE from each tail (at least 1 value)."""
    ordered = sorted(values)
    k = int(len(ordered) * TRIM_SHARE)
    if len(ordered) - 2 * k >= 3:
        ordered = ordered[k:len(ordered) - k] if k else ordered
    return sum(ordered) / len(ordered)


def smooth_series(series, hours):
    """Light 3-point smoothing of a per-hour series (weight SMOOTH_W)."""
    out = {}
    for hour in hours:
        total = 0.0
        weight = 0.0
        for delta, w in ((-1, SMOOTH_W), (0, 1.0), (1, SMOOTH_W)):
            if hour + delta in series:
                total += w * series[hour + delta]
                weight += w
        out[hour] = total / weight
    return out


def finish_quantiles(log_values):
    """Turn five log-quantiles into rounded, non-decreasing viewer counts."""
    quantiles = []
    previous = 0.0
    for value in log_values:
        value = min(max(math.exp(value), 1.0), 1e9)
        value = round2_half_up(value)
        value = max(value, previous)
        previous = value
        quantiles.append(value)
    return quantiles


def normal_component(entries, hour1, x_target, trimmed, spread_mult, smooth):
    """Split-group normal quantiles; optionally trimmed centre / smoothed."""
    chosen = split_group(entries, x_target)
    hours = list(range(2, 25))
    centres = {}
    spreads = {}
    for hour in hours:
        ratios = [math.log(e["curve"][hour] / e["curve"][1]) for e in chosen]
        centre, spread = mean_and_sd(ratios)
        if trimmed:
            centre = trimmed_mean(ratios)
        centres[hour] = centre
        spreads[hour] = spread
    if smooth:
        centres = smooth_series(centres, hours)
        spreads = smooth_series(spreads, hours)
    log_hour1 = math.log(hour1)
    return {"hours": [
        {"hour": hour, "q": finish_quantiles([
            log_hour1 + centres[hour] + z * spreads[hour] * spread_mult
            for z in Z])}
        for hour in hours]}


def empirical_component(entries, hour1, x_target):
    """Split-group empirical quantiles, shrunk toward the normal ones."""
    chosen = split_group(entries, x_target)
    hours = list(range(2, 25))
    log_hour1 = math.log(hour1)
    rows = []
    for hour in hours:
        ratios = sorted(
            math.log(e["curve"][hour] / e["curve"][1]) for e in chosen)
        centre, spread = mean_and_sd(ratios)
        quantiles = []
        for tau, z in zip(LEVELS, Z):
            pos = tau * (len(ratios) - 1)
            low = int(math.floor(pos))
            high = min(low + 1, len(ratios) - 1)
            frac = pos - low
            empirical = ratios[low] * (1 - frac) + ratios[high] * frac
            normal = centre + z * spread
            value = (1 - EMP_SHRINK) * empirical + EMP_SHRINK * normal
            quantiles.append(log_hour1 + value)
        rows.append({"hour": hour, "q": finish_quantiles(quantiles)})
    return {"hours": rows}


def _solve_3x3(matrix, rhs):
    """Solve a 3x3 linear system by Gaussian elimination (or None)."""
    rows = [row[:] + [b] for row, b in zip(matrix, rhs)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(3):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                for c in range(col, 4):
                    rows[r][c] -= factor * rows[col][c]
    return [rows[i][3] / rows[i][i] for i in range(3)]


def regression_component(entries, hour1, x_target):
    """Per-hour OLS of ln(v_h / v_1) on ln(x) and ln(hour1), whole pool.

    The target's features are clamped to the pool's observed range so an
    out-of-range target (a much bigger launch than any comparable) never
    extrapolates wildly; it lands on the nearest edge instead.
    """
    if len(entries) < 5:
        return None
    log_x = [math.log(e["x"]) for e in entries]
    log_h1 = [math.log(e["curve"][1]) for e in entries]
    lx_target = min(max(math.log(max(x_target, 1e-6)), min(log_x)), max(log_x))
    lh_target = min(max(math.log(hour1), min(log_h1)), max(log_h1))
    rows = []
    for hour in range(2, 25):
        design = [[1.0, lx, lh] for lx, lh in zip(log_x, log_h1)]
        target = [math.log(e["curve"][hour] / e["curve"][1]) for e in entries]
        normal = [[sum(design[k][i] * design[k][j] for k in range(len(design)))
                   + (1e-6 if i == j and i > 0 else 0.0)
                   for j in range(3)] for i in range(3)]
        rhs = [sum(design[k][i] * target[k] for k in range(len(design)))
               for i in range(3)]
        beta = _solve_3x3(normal, rhs)
        if beta is None:
            return None
        centre = beta[0] + beta[1] * lx_target + beta[2] * lh_target
        residuals = [target[k] - (beta[0] + beta[1] * design[k][1]
                                  + beta[2] * design[k][2])
                     for k in range(len(design))]
        dof = max(len(design) - 3, 1)
        spread = max(math.sqrt(sum(r * r for r in residuals) / dof), SD_FLOOR)
        log_hour1 = math.log(hour1)
        rows.append({"hour": hour, "q": finish_quantiles([
            log_hour1 + centre + z * spread for z in Z])})
    return {"hours": rows}


def ensemble(forecasts):
    """Geometric mean of the component forecasts' quantiles, per hour.

    Averaging in log space is the right combination for log-scale
    quantiles: it keeps the ensemble's median between the components'
    medians and its spread between their spreads, instead of letting
    one wide component dominate the tails the way an arithmetic mean
    of viewer counts would.
    """
    rows = []
    for i in range(23):
        quantiles = []
        for j in range(5):
            log_mean = sum(
                math.log(f["hours"][i]["q"][j]) for f in forecasts) / len(forecasts)
            quantiles.append(log_mean)
        rows.append({"hour": forecasts[0]["hours"][i]["hour"],
                     "q": finish_quantiles(quantiles)})
    return {"hours": rows}


def persistence_quantiles(hour1, hour):
    """Fallback: every hour stays at the hour-1 print, spread widening."""
    quantiles = []
    previous = 0.0
    for z in Z:
        value = hour1 * math.exp(z * FALLBACK_SIGMA * math.sqrt(hour - 1))
        value = min(max(value, 1.0), 1e9)
        value = round2_half_up(value)
        value = max(value, previous)
        previous = value
        quantiles.append(value)
    return quantiles


def forecast_case(case):
    """Forecast hours 2..24 of one case as {"hours": [...]}."""
    known = case.get("known") or []
    first = next((row for row in known if row.get("hour") == 1), None)
    hour1 = 1.0
    if first is not None:
        viewers = first.get("viewers")
        if isinstance(viewers, (int, float)) and viewers > 0:
            hour1 = float(viewers)
    hours_to_forecast = [row["hour"] for row in case.get("forecast", [])]
    if not hours_to_forecast:
        hours_to_forecast = list(range(2, 25))

    pool = []
    try:
        pool = build_pool(case)
    except Exception:
        pool = []
    if pool:
        try:
            entries = pool_entries(pool)
            level = target_pre_level(case)
            x_target = hour1 / level if level and level > 0 else 0.7
            components = []
            components.append(normal_component(
                entries, hour1, x_target, trimmed=False,
                spread_mult=SPREAD_MULT, smooth=True))
            components.append(normal_component(
                entries, hour1, x_target, trimmed=True,
                spread_mult=SPREAD_MULT, smooth=False))
            components.append(empirical_component(entries, hour1, x_target))
            regression = regression_component(entries, hour1, x_target)
            if regression is not None:
                components.append(regression)
            if len(components) >= 2:
                combined = ensemble(components)
                # Keep only the hours the case actually asks for.
                wanted = set(hours_to_forecast)
                combined["hours"] = [row for row in combined["hours"]
                                     if row["hour"] in wanted]
                return combined
        except Exception:
            pass
    return {"hours": [
        {"hour": hour, "q": persistence_quantiles(hour1, hour)}
        for hour in hours_to_forecast
    ]}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            forecast = forecast_case(case)
        except Exception:
            # Never leave a case without an output: fall back to
            # persistence around the hour-1 print (or 1 viewer).
            hour1 = 1.0
            for row in case.get("known", []) or []:
                if row.get("hour") == 1 and isinstance(row.get("viewers"), (int, float)):
                    hour1 = max(1.0, float(row["viewers"]))
            hours = [row["hour"] for row in case.get("forecast", [])] or list(range(2, 25))
            forecast = {"hours": [
                {"hour": hour, "q": persistence_quantiles(hour1, hour)}
                for hour in hours
            ]}
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
