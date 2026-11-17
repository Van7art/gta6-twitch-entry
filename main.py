"""Forecast GTA VI's launch-day Twitch viewers, hour by hour.

Entry for make.fun competition gta6-twitch-ccu-forecast-01.

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
from the case's own `comparables`:

1.  Keep only usable past launches: tier "comparable" (no early-access
    contamination, no weak/F2P analogue), no simultaneous global unlock,
    and an hour-1 print of at least 5,000 viewers (below that, the print
    is noise from the unlock hour, not a scale).
2.  Split the pool in two by how the target's hour-1 print compares with
    its own pre-release level: x = hour1 / mean(viewers in the 6 hours
    before the release day). Launches whose hour 1 was small next to
    their pre-release evening (x < 0.6) surge much harder through the
    day than launches whose hour 1 was already large. The split is a
    hard threshold with a minimum group size, so a target far outside
    the observed range simply lands in the high group instead of being
    extrapolated.
3.  For each hour h, take the log-ratios ln(v_h / v_1) of the games in
    the target's group, and set the forecast's centre to their mean and
    its log-spread to their (sample) standard deviation, floored at
    0.12. Quantiles are q_tau = hour1 * exp(centre + z_tau * spread),
    with z the standard normal quantiles. The spread is honest: it is
    the disagreement between real past launches, not an assumed number.

If anything unexpected happens (no usable pool, missing history, a
malformed field) the program falls back to a plain persistence forecast
so the case still gets a valid output instead of crashing.

## Local practice result

Scored against the four practice launches' real SullyGnome hourly
counts (Marvel's Wolverine, Red Dead Redemption 2, God of War
Ragnarok, Marvel's Spider-Man 2), this entry scores a mean pinball
loss of 0.172, against 0.387 for the house baseline, with
90% coverage of 0.89. See README.md for the per-case table.
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


def mean_and_sd(values):
    """Mean and sample standard deviation of a list of floats."""
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, SD_FLOOR
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, max(math.sqrt(variance), SD_FLOOR)


def hour_quantiles(hour1, pool, hour):
    """The five quantiles for one hour from the pool's log-ratios."""
    log_ratios = []
    for curve in pool:
        if hour in curve and curve[hour] > 0:
            log_ratios.append(math.log(curve[hour] / curve[1]))
    if not log_ratios:
        log_ratios = [0.0]
    centre, spread = mean_and_sd(log_ratios)
    quantiles = []
    previous = 0.0
    for z in Z:
        value = hour1 * math.exp(centre + z * spread)
        value = min(max(value, 1.0), 1e9)
        value = round2_half_up(value)
        # Rounding can only tie neighbours, never reverse them; the
        # running max keeps the row non-decreasing in every case.
        value = max(value, previous)
        previous = value
        quantiles.append(value)
    return quantiles


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
        # Split the pool by the target's own hour-1 print against its
        # pre-release level: a launch whose hour 1 was small next to
        # its pre-release evening surges much harder through the day.
        # A group smaller than MIN_GROUP is not trusted on its own.
        curves = [entry["curve"] for entry in pool]
        level = target_pre_level(case)
        if level and level > 0:
            x = hour1 / level
            low = [e["curve"] for e in pool if e["curve"][1] / e["pre6"] < SPLIT_X]
            high = [e["curve"] for e in pool if e["curve"][1] / e["pre6"] >= SPLIT_X]
            chosen = low if x < SPLIT_X else high
            if len(chosen) >= MIN_GROUP:
                curves = chosen
        return {"hours": [
            {"hour": hour, "q": hour_quantiles(hour1, curves, hour)}
            for hour in hours_to_forecast
        ]}
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
