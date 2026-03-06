"""
mfm_engine.py
Pure scoring functions for the Multi-Factor Momentum Model.
All functions take pre-computed metrics and return integer scores 0–100.
Composite weighting: Price Mom 30% | RS 25% | Trend 20% | Earnings 15% | Volume 10%
"""

import numpy as np

WEIGHTS = {
    "price_momentum":    0.30,
    "relative_strength": 0.25,
    "trend_quality":     0.20,
    "earnings_momentum": 0.15,
    "volume_momentum":   0.10,
}

FACTOR_LABELS = {
    "price_momentum":    "Price Momentum",
    "relative_strength": "Relative Strength",
    "trend_quality":     "Trend Quality",
    "earnings_momentum": "Earnings Momentum",
    "volume_momentum":   "Volume Momentum",
}


def _clamp(v: float, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, v))


def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Linear interpolation from [x0,x1] → [y0,y1], clamped."""
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return _clamp(y0 + (y1 - y0) * t)


# ──────────────────────────────────────────────────────────────
#  FACTOR 1 — Price Momentum  (weight 30%)
# ──────────────────────────────────────────────────────────────
def score_price_momentum(
    ret_12m: float,   # 12-month return exc. last month  (%)
    ret_6m:  float,   # 6-month return exc. last month   (%)
    ret_3m:  float,   # 3-month return                   (%)
    ret_1m:  float,   # 1-month return                   (%)
) -> dict:
    """
    Classic skip-1 momentum.  12-1M carries the most weight (40%).
    Short-term 1M is muted — small pullbacks are healthy (VCP behaviour).
    """
    s12 = _lerp(ret_12m, -30, 100, 0, 100)
    s6  = _lerp(ret_6m,  -20,  60, 0, 100)
    s3  = _lerp(ret_3m,  -15,  35, 0, 100)
    # 1M: penalise extreme overshoots (>30%) and extreme drops (<-15%)
    # slight positive bias for -5..+10% range (healthy consolidation)
    if ret_1m < -15:
        s1 = _lerp(ret_1m, -40, -15, 0, 25)
    elif ret_1m < 0:
        s1 = _lerp(ret_1m, -15, 0, 35, 65)
    elif ret_1m <= 10:
        s1 = _lerp(ret_1m, 0, 10, 60, 75)
    else:
        s1 = _lerp(ret_1m, 10, 40, 75, 55)   # slight penalty for overextension

    composite = s12*0.40 + s6*0.30 + s3*0.20 + s1*0.10
    score = int(round(_clamp(composite)))
    return {
        "score": score,
        "sub_scores": {
            "12M Return (skip-1)": (ret_12m, s12, "40%"),
            "6M Return (skip-1)":  (ret_6m,  s6,  "30%"),
            "3M Return":           (ret_3m,  s3,  "20%"),
            "1M Return":           (ret_1m,  s1,  "10%"),
        }
    }


# ──────────────────────────────────────────────────────────────
#  FACTOR 2 — Relative Strength vs NIFTY 50  (weight 25%)
# ──────────────────────────────────────────────────────────────
def score_relative_strength(
    rs_12m:  float,   # stock 12M return - NIFTY 12M return  (pp)
    rs_6m:   float,   # stock 6M return  - NIFTY 6M return   (pp)
    rs_rank: float,   # percentile rank vs NIFTY 500 universe (0-100)
) -> dict:
    s12  = _lerp(rs_12m,  -25, 70, 0, 100)
    s6   = _lerp(rs_6m,   -20, 45, 0, 100)
    sRnk = _clamp(rs_rank, 0, 100)

    composite = s12*0.35 + s6*0.25 + sRnk*0.40
    score = int(round(_clamp(composite)))
    return {
        "score": score,
        "sub_scores": {
            "12M RS vs NIFTY (pp)": (rs_12m,  s12,  "35%"),
            "6M RS vs NIFTY (pp)":  (rs_6m,   s6,   "25%"),
            "RS Rank (percentile)": (rs_rank, sRnk, "40%"),
        }
    }


# ──────────────────────────────────────────────────────────────
#  FACTOR 3 — Trend Quality  (weight 20%)
# ──────────────────────────────────────────────────────────────
def score_trend_quality(
    pct_above_200ema: float,  # (CMP / 200EMA - 1) * 100
    pct_above_50ema:  float,  # (CMP / 50EMA  - 1) * 100
    pct_from_52w_high: float, # (CMP / 52wHigh - 1) * 100  [negative = below]
    adx: float,               # ADX(14)
) -> dict:
    # 200 EMA: being above is critical; penalise below
    if pct_above_200ema >= 0:
        s200 = _lerp(pct_above_200ema, 0, 50, 50, 100)
    else:
        s200 = _lerp(pct_above_200ema, -30, 0, 0, 50)

    # 50 EMA: sweet spot is slightly above (0–20%).  Penalise overextension.
    if pct_above_50ema < 0:
        s50 = _lerp(pct_above_50ema, -25, 0, 0, 50)
    elif pct_above_50ema <= 20:
        s50 = _lerp(pct_above_50ema, 0, 20, 55, 95)
    else:
        s50 = _lerp(pct_above_50ema, 20, 60, 95, 50)   # extended = watch out

    # 52-week high proximity: closer = better, within -15% is ideal
    s52h = _lerp(pct_from_52w_high, -50, 5, 0, 100)

    # ADX: >25 = trending, >40 = strongly trending, <20 = choppy
    sadx = _lerp(adx, 10, 50, 10, 100)

    composite = s200*0.30 + s50*0.20 + s52h*0.25 + sadx*0.25
    score = int(round(_clamp(composite)))
    return {
        "score": score,
        "sub_scores": {
            "% above 200 EMA":     (pct_above_200ema,   s200, "30%"),
            "% above 50 EMA":      (pct_above_50ema,    s50,  "20%"),
            "% from 52W High":     (pct_from_52w_high,  s52h, "25%"),
            "ADX (14)":            (adx,                sadx, "25%"),
        }
    }


# ──────────────────────────────────────────────────────────────
#  FACTOR 4 — Volume Momentum  (weight 10%)
# ──────────────────────────────────────────────────────────────
def score_volume_momentum(
    vol_ratio_20_50: float,   # 20D avg vol / 50D avg vol
    up_down_vol_ratio: float, # avg volume on up-days / avg volume on down-days (last 20D)
    breakout_vol_mult: float, # peak daily volume last 20D / 50D avg volume
) -> dict:
    sv   = _lerp(vol_ratio_20_50,   0.5, 3.0, 0, 100)
    sUd  = _lerp(up_down_vol_ratio, 0.5, 3.0, 0, 100)
    sBv  = _lerp(breakout_vol_mult, 1.0, 6.0, 15, 100)

    composite = sv*0.35 + sUd*0.35 + sBv*0.30
    score = int(round(_clamp(composite)))
    return {
        "score": score,
        "sub_scores": {
            "20D/50D Vol Ratio":      (vol_ratio_20_50,   sv,  "35%"),
            "Up/Down Vol Ratio":      (up_down_vol_ratio, sUd, "35%"),
            "Breakout Vol (x avg)":   (breakout_vol_mult, sBv, "30%"),
        }
    }


# ──────────────────────────────────────────────────────────────
#  FACTOR 5 — Earnings Momentum  (weight 15%)
# ──────────────────────────────────────────────────────────────
def score_earnings_momentum(
    eps_growth_yoy: float,    # %
    rev_growth_yoy: float,    # %
    earnings_surprise: float, # (actual - estimate) / |estimate| * 100, or QoQ % if estimate N/A
) -> dict:
    sEps  = _lerp(eps_growth_yoy,    -25, 120, 0, 100)
    sRev  = _lerp(rev_growth_yoy,    -10,  40, 0, 100)
    sSurp = _lerp(earnings_surprise, -20,  25, 0, 100)

    composite = sEps*0.50 + sRev*0.25 + sSurp*0.25
    score = int(round(_clamp(composite)))
    return {
        "score": score,
        "sub_scores": {
            "EPS Growth YoY (%)":       (eps_growth_yoy,    sEps,  "50%"),
            "Revenue Growth YoY (%)":   (rev_growth_yoy,    sRev,  "25%"),
            "Earnings Surprise / QoQ (%)": (earnings_surprise, sSurp, "25%"),
        }
    }


# ──────────────────────────────────────────────────────────────
#  COMPOSITE
# ──────────────────────────────────────────────────────────────
def compute_composite(scores: dict) -> dict:
    """
    scores: dict with keys matching WEIGHTS, each value being a factor result dict
            (containing 'score' key) or a plain int.
    Returns composite score + weighted contributions.
    """
    total = 0.0
    contributions = {}
    for key, weight in WEIGHTS.items():
        s = scores.get(key)
        if s is None:
            continue
        raw_score = s["score"] if isinstance(s, dict) else int(s)
        contrib = raw_score * weight
        total += contrib
        contributions[key] = {
            "score":      raw_score,
            "weight":     weight,
            "contrib":    round(contrib, 1),
            "max_contrib": round(100 * weight, 1),
        }
    composite = int(round(_clamp(total)))
    return {
        "composite": composite,
        "contributions": contributions,
        "signal": _classify_signal(composite),
    }


def _classify_signal(score: int) -> dict:
    if score >= 80:
        return {"label": "STRONG BUY",  "tier": 5, "color": "#00e87a"}
    if score >= 65:
        return {"label": "BUY",          "tier": 4, "color": "#7fff5e"}
    if score >= 50:
        return {"label": "WATCH",        "tier": 3, "color": "#f5c842"}
    if score >= 35:
        return {"label": "CAUTION",      "tier": 2, "color": "#ff8c00"}
    return     {"label": "AVOID",        "tier": 1, "color": "#ff4060"}


# ──────────────────────────────────────────────────────────────
#  INTERPRETATION GENERATOR
# ──────────────────────────────────────────────────────────────
def generate_interpretation(result: dict, factor_results: dict) -> list[str]:
    """Return a list of analytical insight strings."""
    insights = []
    pm = factor_results.get("price_momentum", {})
    rs = factor_results.get("relative_strength", {})
    tq = factor_results.get("trend_quality", {})
    em = factor_results.get("earnings_momentum", {})
    vm = factor_results.get("volume_momentum", {})

    comp = result["composite"]
    pm_s  = pm.get("score", 50) if isinstance(pm, dict) else 50
    rs_s  = rs.get("score", 50) if isinstance(rs, dict) else 50
    tq_s  = tq.get("score", 50) if isinstance(tq, dict) else 50
    em_s  = em.get("score", 50) if isinstance(em, dict) else 50
    vm_s  = vm.get("score", 50) if isinstance(vm, dict) else 50

    if pm_s >= 75:
        insights.append("✦ Strong price momentum across multiple lookback windows — consistent with a Stage 2 uptrend.")
    elif pm_s < 35:
        insights.append("✦ Price momentum is weak or negative. Avoid initiating new positions until trend resumes.")

    if rs_s >= 75:
        insights.append("✦ Outperforming the NIFTY 50 on both 12M and 6M timeframes with a high RS rank — institutional rotation into this name is likely.")
    elif rs_s < 40:
        insights.append("✦ Underperforming the broader market. Capital is rotating away from this stock.")

    if isinstance(tq, dict):
        sub = tq.get("sub_scores", {})
        above_200 = sub.get("% above 200 EMA", (0,))[0]
        if above_200 < 0:
            insights.append("✦ Price is below the 200 EMA — long-term trend is damaged. Highest-conviction momentum setups require price above this level.")
        elif tq_s >= 70:
            insights.append("✦ Clean trend structure: above both 200 and 50 EMAs, with ADX confirming directional strength.")

    if em_s >= 75:
        insights.append("✦ Earnings acceleration is strong — fundamental momentum provides a sound anchor for price action.")
    elif em_s < 35:
        insights.append("✦ Earnings momentum is weak. Price-only momentum without fundamental support tends to be fragile.")

    if vm_s >= 70:
        insights.append("✦ Volume confirms momentum: above-average activity on up-days and a significant breakout volume event — indicative of institutional accumulation.")
    elif vm_s < 35:
        insights.append("✦ Volume is thin or skewed toward down-days. Low conviction in the price move.")

    # Overall
    if comp >= 80:
        insights.append("✦ High-conviction setup. All primary factors are aligned — consistent with a SEPA-qualified breakout candidate. Size per framework rules.")
    elif comp >= 65:
        insights.append("✦ Solid setup with minor friction. Consider partial initial entry and scale in on confirmation.")
    elif comp >= 50:
        insights.append("✦ Mixed signals. Add to watchlist; wait for factor alignment before committing capital.")
    else:
        insights.append("✦ Momentum is broadly negative. Redeploy capital to higher-scoring opportunities.")

    return insights
