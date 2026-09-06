#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare.py — STAGE 3 of the video-teardown pipeline.

    two derive.py output dirs  ->  "channel rule vs episode choice" table

USAGE
    python3 compare.py --a <episodeA_dir> --b <episodeB_dir> --out <dir>

Each episode dir is a stage-2 (derive.py) output directory, optionally plus the
stage-1 meta.json:

    metrics.json          perMinute[] / chapterRows[] / scenes[] / camera[] / palette
    scenes.json           verified scenes with D / dip / trans
    transitions.json      per-type counts and denominators
    camera.json           per-scene cum_x / cum_y / zoom / label
    silences.json         narration-gap events + narration/loudness aggregates
    derive_manifest.json  runtime, and the PARAMETERS every number was computed with
    meta.json             (stage 1) video identity, duration, fps, resolution
    palette.json          (optional) k-means palette, if a pixel pass was run
    subjects.json         (optional) face / subject budget, if such a pass exists

OUTPUTS
    comparison.json       every metric side by side, with a classification
    comparison.md         the same table, human readable

=============================================================================
THE HONESTY CONTRACT — READ THIS BEFORE QUOTING ANY ROW
=============================================================================
This tool compares TWO episodes.  Two is not a sample.  A channel rule is a
claim about a population of episodes, and n=2 cannot establish one: two
episodes agreeing on a number is exactly what you would expect roughly half
the time from two draws of a wide distribution.

So the emitted `classification` field uses the three requested buckets

    CHANNEL_RULE      values agree within the stated tolerance
    EPISODE_CHOICE    values differ beyond the stated tolerance
    INSUFFICIENT      only one episode has the value, or n is too small,
                      or the two values were computed with different
                      parameters and are therefore not comparable at all

but every CHANNEL_RULE row also carries

    "established": false,
    "n_episodes": 2,
    "verdict_text": "consistent across 2 episodes (n=2, not established)"

and comparison.md prints the verdict_text, never the bare words "channel
rule".  A CHANNEL_RULE classification here means "survived a falsification
attempt on one extra episode", nothing more.  EPISODE_CHOICE is the stronger
finding of the two: a single disagreement genuinely does refute a proposed
invariant, whereas a single agreement confirms nothing.

Every tolerance is declared in TOLERANCES below with a written justification,
is echoed into comparison.json, and can be overridden with --tolerances.

RUNTIME NORMALIZATION
Absolute counts are never compared.  Everything is either a rate (per minute /
per 10 minutes) or a curve/position on normalized time tau = t / runtime.
Raw counts are still reported for context, classified INSUFFICIENT with that
as the reason.

PROVENANCE
Formulas for the underlying quantities were recovered in the recon specs and
are implemented in derive.py; this file only aggregates and compares.  Where a
recon spec bears directly on how a comparison must be guarded, the guard is
tagged [R:<spec>].
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, OrderedDict

import numpy as np

N_EPISODES = 2
CURVE_POINTS = 100        # resample every curve to 100 normalized-time points
DEFAULT_ACTS = 5          # normalized act bins for the pacing profile
HUE_BINS = 36             # 10-degree bins for the palette hue histogram


# =============================================================================
# 1.  TOLERANCES — every one of these is a judgement call, so every one of
#     these carries its justification into the output.
# =============================================================================
# kind:
#   rel      -> |a-b| / mean(|a|,|b|)  <= value          (scale-free quantities)
#   abs      -> |a-b|                  <= value          (already-normalized or log units)
#   corr     -> spearman rho           >= value          (curve shape)
#   pmin     -> test p-value           >= value          (distribution / count tests)
TOLERANCES = OrderedDict([
    # ---- pacing -----------------------------------------------------------
    ("scene_count", dict(kind="none", value=None, unit="scenes", justification=(
        "NO TOLERANCE, and deliberately so: this row is never classified. An "
        "absolute scene count confounds cutting rate with runtime, so two "
        "episodes agreeing or disagreeing on it says nothing either way. It is "
        "reported for context and always classified INSUFFICIENT; the "
        "comparable form is scenes_per_minute."))),
    ("scenes_per_minute", dict(kind="rel", value=0.15, unit="scenes/min", justification=(
        "15% relative. The reference episode's own chapter-level pacing spans "
        "2.2-6.8 scenes/min (a 3.1x internal range), so an episode-level "
        "aggregate that moves by more than 15% is moving by more than "
        "measurement noise and is a real pacing decision."))),
    ("scene_dur_median", dict(kind="rel", value=0.15, unit="s", justification=(
        "15% relative, as specified for scene-duration comparison. With n~72 "
        "scenes the median's own standard error is roughly 1.25*IQR/sqrt(n) ~= "
        "8-10% of the median, so 15% is about 1.5 standard errors: tight enough "
        "to be meaningful, loose enough not to fire on sampling noise."))),
    ("scene_dur_p10", dict(kind="rel", value=0.25, unit="s", justification=(
        "25% relative. Tail quantiles are far noisier than the median at n~72 "
        "(the 10th percentile is pinned by ~7 scenes), so the tolerance is "
        "widened accordingly. Tightening this to 15% would make the row fire "
        "on resampling noise alone."))),
    ("scene_dur_p90", dict(kind="rel", value=0.25, unit="s", justification=(
        "25% relative, same tail-noise argument as p10."))),
    ("scene_dur_shape", dict(kind="abs", value=0.25, unit="IQR/median", justification=(
        "0.25 absolute on the dimensionless ratio IQR/median. This is the one "
        "duration statistic that is invariant under a uniform time-stretch, so "
        "it separates 'the editor cut slower' from 'the editor changed rhythm'. "
        "MEASURED on the reference episode: IQR/median = 1.88 "
        "(p25=2.98 s, p50=11.85 s, p75=25.30 s), so 0.25 is 13% of the "
        "reference value. That is tight for a heavy-tailed ratio and this row "
        "will fire on a modest rhythm change; it is deliberately the strictest "
        "of the duration rows because it is the only scale-free one."))),
    ("scene_dur_distribution", dict(kind="pmin", value=0.05, unit="KS p", justification=(
        "Two-sample Kolmogorov-Smirnov, agree if p >= 0.05. NOTE this is a "
        "failure-to-reject, not evidence of sameness: at n=72 vs n=72 the test "
        "only has power against fairly large distributional shifts (it detects a "
        "~0.23 KS statistic at alpha=0.05). Read a pass here as 'not caught', "
        "not as 'same distribution'."))),
    ("scenes_per_min_by_act", dict(kind="rel", value=0.25, unit="scenes/min", justification=(
        "25% relative, applied to the WORST act bin. With 5 acts and ~72 scenes "
        "there are ~14 scene starts per bin; Poisson counting noise alone is "
        "1/sqrt(14) = 27% of the mean. So 25% is already at the counting-noise "
        "floor and this row cannot be made tighter without more episodes."))),

    # ---- transitions ------------------------------------------------------
    ("transition_mix", dict(kind="pmin", value=0.05, unit="chi-square p", justification=(
        "Chi-square test of homogeneity on the 2 x K transition-type table, "
        "agree if p >= 0.05. Categories with a pooled expected count below 5 in "
        "both episodes are merged into 'other' before the test, and the merge is "
        "reported, because the chi-square approximation fails on sparse cells "
        "(the reference episode has only 3-8 boundaries in classes B/C/D/F)."))),
    ("hard_cut_share", dict(kind="pmin", value=0.05, unit="2-proportion z p", justification=(
        "Two-proportion z-test, agree if p >= 0.05. At n~71 boundaries per "
        "episode the detectable difference is about 16 percentage points, so "
        "this row is weak by construction; the point estimate and its CI are "
        "reported alongside so the reader can see how weak."))),
    ("pass_through_share", dict(kind="pmin", value=0.05, unit="2-proportion z p", justification=(
        "Two-proportion z-test on (whiteout + blackout + occluder wipe) / "
        "interior boundaries, agree if p >= 0.05. Same power caveat as "
        "hard_cut_share."))),

    # ---- colour / brightness curves ---------------------------------------
    ("warm_curve", dict(kind="corr", value=0.70, unit="spearman rho", justification=(
        "Spearman rho >= 0.70 on the warm curve resampled to 100 normalized-time "
        "points, as specified. Spearman rather than Pearson because the claim "
        "being tested is 'same narrative shape', not 'same levels'. NO p-value is "
        "reported for this row: the 100 points are interpolated from ~20 "
        "independent minute bins and are therefore massively autocorrelated, so "
        "any nominal p would be fiction. The honest effective n is the number of "
        "source bins, reported as n_independent_bins."))),
    ("warm_level", dict(kind="abs", value=5.0, unit="percentage points", justification=(
        "5 percentage points absolute on the runtime-weighted mean warm share. "
        "Justified against the 7.06 pp reconstruction noise floor measured for "
        "the warm predicate in recon [R:color-narrative]; 5 pp is inside that "
        "floor, so this row is deliberately near the edge of what the underlying "
        "measurement can support and is flagged low-confidence."))),
    ("warm_dynamic_range", dict(kind="rel", value=0.25, unit="percentage points", justification=(
        "25% relative on (max - min) of the warm curve. The reference episode's "
        "range is 49.1 pp; 25% is 12 pp, about the size of one act-to-act step."))),
    ("V_curve", dict(kind="corr", value=0.70, unit="spearman rho", justification=(
        "Same rule and same autocorrelation caveat as warm_curve."))),
    ("V_level", dict(kind="abs", value=5.0, unit="0-100 V", justification=(
        "5 points absolute on the 0-100 HSV-Value scale. MEASURED on the "
        "reference episode: per-minute V spans 28.0-49.3 (a 21.3-point range; "
        "the coarser per-chapter series reaches 50.4), so 5 points is just "
        "under a quarter of the within-episode per-minute range."))),
    ("luma_curve", dict(kind="corr", value=0.70, unit="spearman rho", justification=(
        "Same rule as warm_curve, applied to mean luma if a stage-1 timeseries "
        "is present in the episode dir."))),
    ("db_curve", dict(kind="corr", value=0.70, unit="spearman rho", justification=(
        "Same rule as warm_curve, applied to the per-minute mean dBFS."))),
    ("db_level", dict(kind="abs", value=3.0, unit="dB", justification=(
        "3 dB absolute on the runtime-weighted mean of a per-0.1s dBFS series. "
        "Absolute, not relative, because dBFS is already logarithmic. 3 dB is "
        "one doubling of power and is the smallest level change a mix engineer "
        "would call deliberate. Caveat carried from recon [R:perminute]: this "
        "quantity is a mean of logs, so it is dominated by quiet passages and by "
        "any digital-silence tail."))),

    # ---- chapters ---------------------------------------------------------
    ("chapters_per_10min", dict(kind="rel", value=0.20, unit="chapters/10min", justification=(
        "20% relative. Chapter boundaries are editorial, not measured, so this "
        "row tests a production habit rather than a signal; 20% is about one "
        "chapter either way on a 20-minute episode."))),
    ("chapter_len_shape", dict(kind="abs", value=0.15, unit="CV of normalized length", justification=(
        "0.15 absolute on the coefficient of variation of chapter lengths "
        "expressed as a fraction of runtime, computed EXCLUDING the final "
        "chapter. The exclusion is not cosmetic: MEASURED on the reference "
        "episode the ending chapter is 3.13x the mean of the others and takes "
        "the CV from 0.116 (ex-ending) to 0.509 (all 12), a factor of 4.4. "
        "Reported both ways."))),
    ("chapter_ending_ratio", dict(kind="rel", value=0.30, unit="x body-chapter mean", justification=(
        "30% relative on (final chapter length) / (mean of the others). "
        "MEASURED on the reference episode: 259 s / 80.9 s = 3.13x. This is "
        "the structural signature worth porting; the absolute 259 s is not."))),

    # ---- silence ----------------------------------------------------------
    ("silences_per_10min", dict(kind="pmin", value=0.05, unit="binomial p", justification=(
        "Exact conditional binomial test: given the total number of events "
        "across both episodes, is the split consistent with the runtime split? "
        "Agree if p >= 0.05. An exact test rather than a ratio tolerance because "
        "these are small counts (14 events on the reference episode) where "
        "Poisson noise alone is +/-27%."))),
    ("silence_positions", dict(kind="pmin", value=0.05, unit="KS p", justification=(
        "Two-sample KS on the silence start times expressed as tau = t/runtime, "
        "agree if p >= 0.05. Deliberately runtime-free: a pure time-stretch of an "
        "episode must NOT move this row. Very low power at n~14 per episode "
        "(needs a ~0.5 KS statistic to fire), so treat a pass as uninformative."))),
    ("narration_coverage", dict(kind="abs", value=5.0, unit="percentage points", justification=(
        "5 percentage points absolute on the share of runtime inside a caption "
        "cue. The reference value is 64.0%; 5 pp is about 1 minute of speech in "
        "20 and is well above the ~0.2 pp spread between the two mask "
        "conventions the reference report itself mixed up [R:audio-silence]."))),
    ("duck_median_db", dict(kind="abs", value=1.5, unit="dB", justification=(
        "1.5 dB absolute on median(narration dBFS) - median(non-narration dBFS). "
        "Absolute because dB is logarithmic. 1.5 dB is below the 1.7 dB gap "
        "between the median and mean formulations of this same statistic, which "
        "is the ambiguity the reference report left open, so this row is "
        "flagged as sitting near its own definitional noise."))),
    ("centroid_ratio", dict(kind="rel", value=0.15, unit="ratio", justification=(
        "15% relative on centroid(narration) / centroid(non-narration). A ratio "
        "rather than the two absolute frequencies, because the ratio survives a "
        "different music bed while the absolute values do not. Reference ~1.42x."))),

    # ---- camera -----------------------------------------------------------
    ("camera_speed_median", dict(kind="rel", value=0.30, unit="% frame width / s", justification=(
        "30% relative on the median per-scene net displacement rate, expressed "
        "as percent of FRAME WIDTH per second so it survives a different proxy "
        "resolution. 30% and not 15% because pan_dx/pan_dy are quantised to whole "
        "pixels on a 96-px-wide proxy and 70-74% of samples are exactly zero "
        "[R:camera], which makes the median extremely coarse."))),
    ("camera_speed_p90", dict(kind="rel", value=0.30, unit="% frame width / s", justification=(
        "30% relative, same quantisation argument as the median."))),
    ("camera_speed_distribution", dict(kind="pmin", value=0.05, unit="KS p", justification=(
        "Two-sample KS on the per-scene displacement rates, agree if p >= 0.05. "
        "n~70 per episode."))),
    ("camera_move_mix", dict(kind="pmin", value=0.05, unit="chi-square p", justification=(
        "Chi-square homogeneity on the move taxonomy. If either episode lacks a "
        "pixel pass its labels carry no zoom clause, so the comparison is "
        "automatically restricted to the pan/tilt/static axes and that "
        "restriction is reported."))),
    ("camera_static_share", dict(kind="pmin", value=0.05, unit="2-proportion z p", justification=(
        "Two-proportion z-test on the share of scenes labelled near-static."))),

    # ---- palette ----------------------------------------------------------
    ("palette_hue_distance", dict(kind="abs", value=15.0, unit="degrees (circular EMD)", justification=(
        "15 degrees of circular earth-mover distance between the share-weighted "
        "hue histograms (36 x 10-degree bins). Circular EMD rather than "
        "chi-square or JS because hue is a circle and bin-wise divergences call "
        "a 5-degree shift as different as a 180-degree shift. 15 degrees is one "
        "and a half bins, and about 10% of the 148-degree separation between the "
        "reference episode's own two colour lobes."))),
    ("palette_warm_share", dict(kind="abs", value=10.0, unit="percentage points", justification=(
        "10 percentage points absolute on the share of palette mass at warm "
        "hues. Loose on purpose: the warm/cool hue boundary is NOT recoverable "
        "from the reference artifact [R:color-narrative], and any episode whose "
        "palette lands in the hue band where the boundary is ambiguous can move "
        "this number by several points on a threshold choice alone."))),

    # ---- subjects ---------------------------------------------------------
    ("subject_budget", dict(kind="rel", value=0.20, unit="varies", justification=(
        "20% relative on each numeric field of an optional subjects.json. No "
        "stage of this pipeline produces such a file, so this row is normally "
        "INSUFFICIENT; the comparator exists so that a future face/subject pass "
        "drops in without a schema change."))),
])


# =============================================================================
# 2.  STATISTICS — implemented locally because scipy is not available here.
#     Each function states its source formula so it can be checked by hand.
# =============================================================================

def _gammap_series(a, x):
    """Regularized lower incomplete gamma P(a,x) by series expansion (NR 6.2)."""
    ap, s, d = a, 1.0 / a, 1.0 / a
    for _ in range(1000):
        ap += 1.0
        d *= x / ap
        s += d
        if abs(d) < abs(s) * 1e-15:
            break
    return s * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gammaq_cf(a, x):
    """Regularized upper incomplete gamma Q(a,x) by continued fraction (NR 6.2)."""
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x, df):
    """P(chi2_df > x). = Q(df/2, x/2)."""
    if df <= 0:
        return float("nan")
    if x <= 0:
        return 1.0
    a, xx = df / 2.0, x / 2.0
    return 1.0 - _gammap_series(a, xx) if xx < a + 1.0 else _gammaq_cf(a, xx)


def norm_sf(z):
    """P(Z > z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def ks_2samp(a, b):
    """Two-sample Kolmogorov-Smirnov.

    Returns (D, p).  D is the max gap between the two empirical CDFs; p uses the
    asymptotic Kolmogorov distribution  Q(l) = 2 * sum_{k>=1} (-1)^(k-1) e^(-2k^2 l^2)
    with the Stephens small-sample correction to the effective n.  Exact only in
    the large-n limit; both episodes here have n of order 70, where the
    approximation is good to ~1% on p.
    """
    a = np.sort(np.asarray(a, dtype=float))
    b = np.sort(np.asarray(b, dtype=float))
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return float("nan"), float("nan")
    allv = np.sort(np.concatenate([a, b]))
    cdfa = np.searchsorted(a, allv, side="right") / na
    cdfb = np.searchsorted(b, allv, side="right") / nb
    d = float(np.max(np.abs(cdfa - cdfb)))
    ne = math.sqrt(na * nb / float(na + nb))
    lam = (ne + 0.12 + 0.11 / ne) * d
    if lam <= 0:
        return d, 1.0
    s, sign = 0.0, 1.0
    for k in range(1, 101):
        s += sign * math.exp(-2.0 * k * k * lam * lam)
        sign = -sign
    return d, float(min(1.0, max(0.0, 2.0 * s)))


def spearman(x, y):
    """Spearman rank correlation (Pearson on average ranks). No p-value: see the
    warm_curve tolerance justification for why one would be meaningless here."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3:
        return float("nan")
    return pearson(_rankdata(x), _rankdata(y))


def _rankdata(v):
    """Average ranks, ties shared."""
    v = np.asarray(v, dtype=float)
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(len(v), dtype=float)
    ranks[order] = np.arange(1, len(v) + 1, dtype=float)
    # average tied groups
    sv = v[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def pearson(x, y):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3:
        return float("nan")
    xc, yc = x - x.mean(), y - y.mean()
    d = math.sqrt(float((xc * xc).sum()) * float((yc * yc).sum()))
    return float((xc * yc).sum() / d) if d > 0 else float("nan")


def chi2_homogeneity(counts_a, counts_b, min_expected=5.0):
    """Chi-square test of homogeneity on a 2 x K contingency table.

    Categories whose POOLED expected count is below `min_expected` in BOTH rows
    are merged into a single 'other' column first, because the chi-square
    approximation is not valid on sparse cells and the transition taxonomy is
    genuinely sparse (3-9 boundaries in four of six classes).

    Returns dict(chi2, df, p, merged, used_categories).
    """
    keys = sorted(set(counts_a) | set(counts_b))
    a = np.array([counts_a.get(k, 0) for k in keys], dtype=float)
    b = np.array([counts_b.get(k, 0) for k in keys], dtype=float)
    na, nb = a.sum(), b.sum()
    if na == 0 or nb == 0:
        return dict(chi2=float("nan"), df=0, p=float("nan"), merged=[], used_categories=[])
    tot = a + b
    n = na + nb
    exp_a, exp_b = tot * na / n, tot * nb / n
    sparse = [i for i in range(len(keys)) if exp_a[i] < min_expected and exp_b[i] < min_expected]
    merged = [keys[i] for i in sparse]
    keep = [i for i in range(len(keys)) if i not in sparse]
    used = [keys[i] for i in keep]
    av = list(a[keep]) + ([a[sparse].sum()] if sparse else [])
    bv = list(b[keep]) + ([b[sparse].sum()] if sparse else [])
    if sparse:
        used = used + ["(other: %s)" % ", ".join(merged)]
    a2, b2 = np.array(av, dtype=float), np.array(bv, dtype=float)
    # drop any column that is still all-zero after merging
    nz = (a2 + b2) > 0
    a2, b2 = a2[nz], b2[nz]
    used = [u for u, k in zip(used, nz) if k]
    k = len(a2)
    if k < 2:
        return dict(chi2=float("nan"), df=0, p=float("nan"), merged=merged, used_categories=used)
    tot2 = a2 + b2
    ea, eb = tot2 * na / n, tot2 * nb / n
    chi2 = float(((a2 - ea) ** 2 / ea).sum() + ((b2 - eb) ** 2 / eb).sum())
    df = k - 1
    return dict(chi2=chi2, df=df, p=float(chi2_sf(chi2, df)), merged=merged,
                used_categories=used)


def two_proportion_z(k1, n1, k2, n2):
    """Pooled two-proportion z-test. Returns dict(p1,p2,diff,z,p,ci95_diff)."""
    if n1 == 0 or n2 == 0:
        return dict(p1=None, p2=None, diff=None, z=None, p=float("nan"), ci95_diff=None)
    p1, p2 = k1 / n1, k2 / n2
    pp = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1.0 / n1 + 1.0 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0
    # unpooled SE for the confidence interval on the difference
    seu = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    return dict(p1=p1, p2=p2, diff=p1 - p2, z=z, p=float(2 * norm_sf(abs(z))),
                ci95_diff=[p1 - p2 - 1.96 * seu, p1 - p2 + 1.96 * seu])


def binom_test_two_sided(k, n, p):
    """Exact two-sided binomial test (sum of all outcomes no more likely than the
    observed one).  Used for count-rate comparison: conditional on the total
    number of events across both episodes, the count in episode A is Binomial(n,
    p) with p = runtime share of A, if the two rates are equal."""
    if n == 0:
        return float("nan")
    def pmf(i):
        return math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                        + i * math.log(p) + (n - i) * math.log1p(-p)) if 0 < p < 1 else \
               float(i == (0 if p == 0 else n))
    obs = pmf(k)
    tot = sum(pmf(i) for i in range(n + 1) if pmf(i) <= obs * (1 + 1e-9))
    return float(min(1.0, tot))


def circular_emd(h1, h2, n_bins):
    """Earth-mover distance between two circular histograms, in bins.

    For a ring, the optimal transport cost is  min over a constant shift c of
    sum_i |F1(i) - F2(i) - c|, and the minimiser is the median of (F1 - F2)
    (Werman et al.).  Returned in the caller's units via bin_width.
    """
    h1 = np.asarray(h1, dtype=float)
    h2 = np.asarray(h2, dtype=float)
    s1, s2 = h1.sum(), h2.sum()
    if s1 <= 0 or s2 <= 0:
        return float("nan")
    d = np.cumsum(h1 / s1 - h2 / s2)
    return float(np.abs(d - np.median(d)).sum())


# =============================================================================
# 3.  EPISODE LOADING
# =============================================================================

def _load_json(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


class Episode(object):
    """Everything compare.py needs from one derive.py output dir.

    Nothing here fabricates a value: a quantity absent from the stage-2 output
    stays None and every metric that depends on it classifies INSUFFICIENT.
    """

    def __init__(self, path, name=None):
        self.path = os.path.abspath(path)
        self.metrics = _load_json(path, "metrics.json")
        self.scenes = _load_json(path, "scenes.json")
        self.camera = _load_json(path, "camera.json")
        self.transitions = _load_json(path, "transitions.json")
        self.silences = _load_json(path, "silences.json")
        self.manifest = _load_json(path, "derive_manifest.json") or {}
        self.meta = _load_json(path, "meta.json") or {}
        self.subjects = _load_json(path, "subjects.json") or _load_json(path, "faces.json")
        # palette may live standalone or inside metrics.json
        self.palette = _load_json(path, "palette.json")
        if self.palette is None and self.metrics:
            self.palette = self.metrics.get("palette")
        # scenes also live inside metrics.json if scenes.json was not written
        if self.scenes is None and self.metrics:
            self.scenes = self.metrics.get("scenes")
        if self.camera is None and self.metrics:
            self.camera = self.metrics.get("camera")

        if self.metrics is None and self.scenes is None:
            sys.exit("compare.py: %s has neither metrics.json nor scenes.json — "
                     "it does not look like a derive.py output dir." % path)

        self.name = name or self._name()
        self.runtime = self._runtime()
        self.notes = []
        self.ts = self._load_timeseries()

    # -- identity ----------------------------------------------------------
    def _name(self):
        v = self.meta.get("video") or {}
        return v.get("id") or v.get("title") or os.path.basename(self.path.rstrip("/"))

    def _runtime(self):
        """Runtime in seconds, in order of trustworthiness:
        stage-1 timeseries span -> stage-1 container duration -> stage-2 manifest
        -> last scene end.  All four agree on a well-formed pair of dirs."""
        tsm = self.meta.get("timeseries") or {}
        if tsm.get("t_last") is not None and tsm.get("sample_rate_fps"):
            # t_last is the LAST SAMPLE's time; the covered span is one sample longer
            return float(tsm["t_last"]) + 1.0 / float(tsm["sample_rate_fps"])
        v = self.meta.get("video") or {}
        if v.get("duration_sec"):
            return float(v["duration_sec"])
        if self.manifest.get("runtime_s"):
            return float(self.manifest["runtime_s"])
        if self.scenes:
            return float(max(s["e"] for s in self.scenes))
        return float("nan")

    def _load_timeseries(self):
        """Optional: the stage-1 CSV, if it has been staged into the episode dir.
        Only used for the full-resolution luma curve; everything else works from
        the stage-2 JSONs."""
        for rel in ("timeseries/timeseries_10fps.csv", "timeseries_10fps.csv"):
            p = os.path.join(self.path, rel)
            if os.path.exists(p):
                try:
                    import csv as _csv
                    with open(p, encoding="utf-8") as fh:
                        rd = _csv.DictReader(fh)
                        cols = {k: [] for k in (rd.fieldnames or [])}
                        for row in rd:
                            for k, v in row.items():
                                cols[k].append(float(v) if v not in ("", None) else float("nan"))
                    return {k: np.array(v, dtype=float) for k, v in cols.items()}
                except Exception as e:                      # pragma: no cover
                    self.notes.append("timeseries at %s unreadable: %s" % (rel, e))
        return None

    # -- parameters that decide whether a comparison is legitimate at all ---
    def param(self, *keys):
        cur = self.manifest.get("parameters") or {}
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

    # -- derived views -----------------------------------------------------
    def scene_durations(self):
        if not self.scenes:
            return None
        return np.array([float(s["dur"]) for s in self.scenes], dtype=float)

    def scene_starts_tau(self):
        """Scene start times as a fraction of runtime."""
        if not self.scenes or not math.isfinite(self.runtime) or self.runtime <= 0:
            return None
        return np.array([float(s["s"]) / self.runtime for s in self.scenes], dtype=float)

    def per_minute(self):
        return (self.metrics or {}).get("perMinute") or []

    def minute_curve(self, key):
        """(tau_centers, values) for a per-minute column, dropping nulls.

        tau is the NORMALIZED-TIME centre of each minute window, computed from
        the window's true row span so that a truncated final minute lands at its
        real centre rather than at the centre of a full minute.  [R:perminute]
        """
        pm = self.per_minute()
        if not pm or not math.isfinite(self.runtime) or self.runtime <= 0:
            return None, None
        taus, vals = [], []
        for row in pm:
            v = row.get(key)
            if v is None:
                continue
            m = int(row.get("m", len(vals)))
            t0 = m * 60.0
            # honour the real window length when the writer recorded it
            if row.get("rows"):
                t1 = t0 + float(row["rows"]) / 10.0
            else:
                t1 = min((m + 1) * 60.0, self.runtime)
            if t1 <= t0:
                continue
            taus.append(((t0 + t1) / 2.0) / self.runtime)
            vals.append(float(v))
        if len(vals) < 3:
            return None, None
        return np.array(taus), np.array(vals)

    def minute_weights(self):
        """Row counts per minute, for runtime-weighted level means."""
        pm = self.per_minute()
        w = []
        for row in pm:
            if row.get("rows"):
                w.append(float(row["rows"]))
            else:
                m = int(row.get("m", len(w)))
                w.append(max(0.0, min((m + 1) * 60.0, self.runtime) - m * 60.0) * 10.0)
        return np.array(w, dtype=float)

    def chapter_lengths(self):
        cr = (self.metrics or {}).get("chapterRows") or []
        if not cr:
            return None
        return np.array([float(r["t1"]) - float(r["t0"]) for r in cr], dtype=float)

    def transition_counts(self):
        """Counter over transition type CODES (A..F) on interior boundaries.

        Codes rather than the full Korean labels so two episodes whose label
        strings differ in wording still line up.  The first scene's 'OPEN' is
        the video start, not a transition, and is excluded — that is the
        denominator convention the reference report uses. [R:transitions]
        """
        if self.transitions and self.transitions.get("by_type"):
            c = Counter()
            for row in self.transitions["by_type"]:
                code = row.get("code") or (row.get("label") or "?")[:1]
                c[code] += int(row.get("n", 0))
            return c
        if self.scenes:
            c = Counter()
            for s in self.scenes:
                t = s.get("trans")
                if not t or t == "OPEN":
                    continue
                c[t[:1]] += 1
            return c
        return None

    def camera_rates(self):
        """Per-scene net displacement rate in PERCENT OF FRAME WIDTH PER SECOND.

        cum_x / cum_y are integer pixel sums on the stage-1 proxy, so they must
        be divided by that proxy's width before two episodes can be compared;
        the width is read from the manifest and defaults to the 96 px the
        reference pipeline used. [R:camera]
        """
        if not self.camera:
            return None
        w = self.param("camera", "proxy_width_px") or 96.0
        out = []
        for c in self.camera:
            dur = float(c.get("dur") or 0.0)
            if dur <= 0:
                continue
            d = math.hypot(float(c.get("cum_x") or 0.0), float(c.get("cum_y") or 0.0))
            out.append(100.0 * (d / float(w)) / dur)
        return np.array(out, dtype=float) if out else None

    def camera_axes(self):
        """Move taxonomy as axis flags parsed out of the label string.

        Parsed rather than string-compared so that a label missing its zoom
        clause (because no pixel pass ran) still contributes its pan and tilt
        information. Returns (Counter, has_zoom_info).
        """
        if not self.camera:
            return None, False
        has_zoom = any(c.get("zoom") is not None for c in self.camera)
        c = Counter()
        for cam in self.camera:
            lab = cam.get("label") or ""
            parts = []
            if "좌→우" in lab:
                parts.append("pan_LR")
            if "우→좌" in lab:
                parts.append("pan_RL")
            if "하강" in lab:
                parts.append("tilt_down")
            if "상승" in lab:
                parts.append("tilt_up")
            if has_zoom:
                if "줌아웃" in lab or "풀백" in lab:
                    parts.append("zoom_out")
                if "줌인" in lab or "푸시인" in lab:
                    parts.append("zoom_in")
            key = "+".join(parts) if parts else "static"
            c[key] += 1
        return c, has_zoom

    def palette_hue_hist(self, n_bins=HUE_BINS):
        """Share-weighted hue histogram from a k-means palette."""
        if not self.palette:
            return None
        h = np.zeros(n_bins, dtype=float)
        for p in self.palette:
            if p.get("H") is None or p.get("share") is None:
                return None
            b = int(float(p["H"]) % 360.0 // (360.0 / n_bins))
            h[min(b, n_bins - 1)] += float(p["share"])
        return h if h.sum() > 0 else None

    def palette_warm_share(self, hue_lo=330.0, hue_hi=90.0):
        """Share of palette mass on the warm half of the hue circle.

        The hue boundary is a FREE PARAMETER that the reference artifact never
        published [R:color-narrative]; it is defaulted here and stamped into the
        output so nobody mistakes it for a recovered value.
        """
        if not self.palette:
            return None
        tot = warm = 0.0
        for p in self.palette:
            if p.get("H") is None or p.get("share") is None:
                return None
            hh, sh = float(p["H"]) % 360.0, float(p["share"])
            tot += sh
            warm += sh if ((hh >= hue_lo) or (hh < hue_hi)) else 0.0
        return 100.0 * warm / tot if tot > 0 else None


# =============================================================================
# 4.  CURVE MACHINERY — normalized time
# =============================================================================

def resample_curve(tau, vals, n=CURVE_POINTS):
    """Linear interpolation of (tau, vals) onto n evenly spaced normalized-time
    points at tau = (i+0.5)/n.  Ends are held flat (np.interp's default), which
    is the right choice for a level series: extrapolating a trend past the first
    and last measured bin would invent data."""
    if tau is None or vals is None or len(vals) < 2:
        return None
    grid = (np.arange(n) + 0.5) / float(n)
    order = np.argsort(tau)
    return np.interp(grid, np.asarray(tau)[order], np.asarray(vals)[order])


def act_profile(starts_tau, runtime, n_acts=DEFAULT_ACTS):
    """Scene-start rate (scenes per minute) inside each normalized act bin."""
    if starts_tau is None or not math.isfinite(runtime) or runtime <= 0:
        return None, None
    edges = np.linspace(0.0, 1.0, n_acts + 1)
    counts, _ = np.histogram(np.clip(starts_tau, 0.0, 1.0 - 1e-12), bins=edges)
    minutes_per_act = (runtime / n_acts) / 60.0
    return counts.astype(float) / minutes_per_act, counts


# =============================================================================
# 5.  THE COMPARATOR — one Result per metric
# =============================================================================

CHANNEL_RULE = "CHANNEL_RULE"
EPISODE_CHOICE = "EPISODE_CHOICE"
INSUFFICIENT = "INSUFFICIENT"

VERDICT_TEXT = {
    CHANNEL_RULE: "consistent across 2 episodes (n=2, not established)",
    EPISODE_CHOICE: "differs beyond tolerance -> per-episode decision",
    INSUFFICIENT: "insufficient data to classify",
}


class Comparison(object):
    def __init__(self, tolerances):
        self.tol = tolerances
        self.rows = []

    # -- low-level -------------------------------------------------------
    def _tol(self, key):
        t = self.tol.get(key)
        if t is None:
            raise KeyError("no tolerance declared for metric %r — every metric "
                           "must declare one" % key)
        return t

    def add(self, key, family, label, a, b, classification, detail=None,
            reason=None, unit=None, confidence="normal"):
        t = self.tol.get(key, {})
        row = OrderedDict([
            ("metric", key),
            ("family", family),
            ("label", label),
            ("a", _clean(a)),
            ("b", _clean(b)),
            ("unit", unit if unit is not None else t.get("unit")),
            ("classification", classification),
            ("verdict_text", VERDICT_TEXT[classification]),
            ("n_episodes", N_EPISODES),
            ("established", False),
            ("confidence", confidence),
            ("tolerance", OrderedDict([
                ("kind", t.get("kind")),
                ("value", t.get("value")),
                ("justification", t.get("justification")),
            ]) if t else None),
            ("detail", _clean(detail or {})),
        ])
        if reason:
            row["reason"] = reason
        self.rows.append(row)
        return row

    def insufficient(self, key, family, label, a, b, reason, unit=None):
        return self.add(key, family, label, a, b, INSUFFICIENT, reason=reason, unit=unit)

    # -- the four tolerance kinds ----------------------------------------
    def scalar(self, key, family, label, a, b, detail=None, confidence="normal",
               unit=None):
        """Classify a pair of scalars by this metric's declared tolerance."""
        if a is None or b is None or not _fin(a) or not _fin(b):
            who = ("A" if (a is None or not _fin(a)) else "") + ("B" if (b is None or not _fin(b)) else "")
            return self.insufficient(key, family, label, a, b,
                                     "value missing in episode %s" % (who or "?"))
        t = self._tol(key)
        d = dict(detail or {})
        if t["kind"] == "rel":
            denom = (abs(a) + abs(b)) / 2.0
            rel = abs(a - b) / denom if denom > 0 else (0.0 if a == b else float("inf"))
            d.update(abs_diff=abs(a - b), rel_diff=rel, threshold=t["value"])
            ok = rel <= t["value"]
        elif t["kind"] == "abs":
            d.update(abs_diff=abs(a - b), threshold=t["value"])
            ok = abs(a - b) <= t["value"]
        else:
            raise ValueError("scalar() used on a %r tolerance" % t["kind"])
        return self.add(key, family, label, a, b,
                        CHANNEL_RULE if ok else EPISODE_CHOICE, detail=d,
                        unit=unit, confidence=confidence)

    def by_test(self, key, family, label, a, b, p, detail=None, confidence="normal",
                unit=None):
        """Classify by a p-value: agree = failed to reject equality."""
        if p is None or not _fin(p):
            return self.insufficient(key, family, label, a, b, "test not computable")
        t = self._tol(key)
        d = dict(detail or {})
        d.update(p_value=p, alpha=t["value"])
        return self.add(key, family, label, a, b,
                        CHANNEL_RULE if p >= t["value"] else EPISODE_CHOICE,
                        detail=d, unit=unit, confidence=confidence)

    def by_corr(self, key, family, label, rho, detail=None, confidence="normal",
                unit=None):
        """Classify a curve by its shape correlation."""
        if rho is None or not _fin(rho):
            return self.insufficient(key, family, label, None, None,
                                     "curve missing or too short in at least one episode")
        t = self._tol(key)
        d = dict(detail or {})
        d.update(spearman=rho, threshold=t["value"])
        return self.add(key, family, label, None, None,
                        CHANNEL_RULE if rho >= t["value"] else EPISODE_CHOICE,
                        detail=d, unit=unit or t.get("unit"), confidence=confidence)


def _fin(x):
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _clean(o):
    """JSON-safe: numpy scalars -> python, NaN/inf -> None, arrays -> lists."""
    if isinstance(o, dict):
        return OrderedDict((k, _clean(v)) for k, v in o.items())
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return [_clean(v) for v in o.tolist()]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if not math.isfinite(f) else round(f, 6)
    if isinstance(o, (np.integer, int)) and not isinstance(o, bool):
        return int(o)
    return o


# =============================================================================
# 6.  METRIC FAMILIES
#     Each function appends rows to `C`.  Every one of them either produces a
#     comparable, runtime-normalized quantity or explains why it cannot.
# =============================================================================

def fam_pacing(C, A, B, n_acts):
    F = "1. pacing / scene structure"

    # --- raw counts: reported, never classified as agreement -------------
    na = len(A.scenes) if A.scenes else None
    nb = len(B.scenes) if B.scenes else None
    C.insufficient("scene_count", F, "scene count (raw)", na, nb,
                   "an absolute count scales with runtime (%s vs %s s) and cannot "
                   "distinguish 'cuts faster' from 'is longer'. The comparable form "
                   "is scenes_per_minute, on the next row."
                   % (_r(A.runtime, 1), _r(B.runtime, 1)), unit="scenes")

    da, db = A.scene_durations(), B.scene_durations()
    if da is None or db is None or len(da) < 5 or len(db) < 5:
        C.insufficient("scenes_per_minute", F, "scenes per minute", None, None,
                       "scenes.json missing or too few scenes in at least one episode")
        for k, lab in (("scene_dur_median", "scene duration median"),
                       ("scene_dur_p10", "scene duration p10"),
                       ("scene_dur_p90", "scene duration p90"),
                       ("scene_dur_shape", "scene duration shape (IQR/median)"),
                       ("scene_dur_distribution", "scene duration distribution (KS)")):
            C.insufficient(k, F, lab, None, None, "no scene list")
        C.insufficient("scenes_per_min_by_act", F, "scenes/min by normalized act",
                       None, None, "no scene list")
        return

    # --- rate ------------------------------------------------------------
    ra = len(da) / (A.runtime / 60.0)
    rb = len(db) / (B.runtime / 60.0)
    C.scalar("scenes_per_minute", F, "scenes per minute", ra, rb,
             detail=dict(n_scenes_a=len(da), n_scenes_b=len(db),
                         runtime_s_a=A.runtime, runtime_s_b=B.runtime))

    # --- duration quantiles ----------------------------------------------
    qa = np.percentile(da, [10, 25, 50, 75, 90])
    qb = np.percentile(db, [10, 25, 50, 75, 90])
    C.scalar("scene_dur_median", F, "scene duration median", qa[2], qb[2],
             detail=dict(mean_a=float(da.mean()), mean_b=float(db.mean())))
    C.scalar("scene_dur_p10", F, "scene duration p10", qa[0], qb[0])
    C.scalar("scene_dur_p90", F, "scene duration p90", qa[4], qb[4])
    # dimensionless shape: invariant under a uniform time-stretch, so it
    # separates "slower cutting" from "different rhythm"
    sa = (qa[3] - qa[1]) / qa[2] if qa[2] > 0 else float("nan")
    sb = (qb[3] - qb[1]) / qb[2] if qb[2] > 0 else float("nan")
    C.scalar("scene_dur_shape", F, "scene duration shape (IQR/median)", sa, sb,
             detail=dict(iqr_a=float(qa[3] - qa[1]), iqr_b=float(qb[3] - qb[1]),
                         note="dimensionless; unchanged by a uniform time-stretch"))
    d, p = ks_2samp(da, db)
    C.by_test("scene_dur_distribution", F, "scene duration distribution (KS)",
              float(qa[2]), float(qb[2]), p,
              detail=dict(ks_statistic=d, n_a=len(da), n_b=len(db),
                          power_note="two-sided KS at n=%d/%d rejects only above "
                                     "D~%.2f at alpha=0.05" % (
                                         len(da), len(db),
                                         1.36 * math.sqrt(1.0 / len(da) + 1.0 / len(db)))),
              confidence="low")

    # --- pacing profile over normalized acts ------------------------------
    pa, ca = act_profile(A.scene_starts_tau(), A.runtime, n_acts)
    pb, cb = act_profile(B.scene_starts_tau(), B.runtime, n_acts)
    if pa is None or pb is None:
        C.insufficient("scenes_per_min_by_act", F, "scenes/min by normalized act",
                       None, None, "runtime unknown")
        return
    denom = (np.abs(pa) + np.abs(pb)) / 2.0
    rel = np.where(denom > 0, np.abs(pa - pb) / np.where(denom > 0, denom, 1.0),
                   np.where(pa == pb, 0.0, np.inf))
    worst = int(np.argmax(rel))
    t = C._tol("scenes_per_min_by_act")
    ok = bool(np.max(rel) <= t["value"])
    C.add("scenes_per_min_by_act", F,
          "scenes/min by normalized act (%d bins, worst bin)" % n_acts,
          _r(pa[worst], 2), _r(pb[worst], 2),
          CHANNEL_RULE if ok else EPISODE_CHOICE,
          detail=dict(acts_a=[round(float(v), 2) for v in pa],
                      acts_b=[round(float(v), 2) for v in pb],
                      counts_a=[int(v) for v in ca], counts_b=[int(v) for v in cb],
                      per_act_rel_diff=[round(float(v), 3) for v in rel],
                      worst_act_index=worst, worst_rel_diff=float(np.max(rel)),
                      threshold=t["value"],
                      sparse_bins_note=(
                          "the worst bin holds %d and %d scene starts; below ~5 per "
                          "bin the relative difference is dominated by counting "
                          "noise and this row should not be read as a pacing "
                          "finding" % (int(ca[worst]), int(cb[worst]))
                          if min(int(ca[worst]), int(cb[worst])) < 5 else None),
                      shape_spearman=spearman(pa, pb),
                      shape_note="shape_spearman is a diagnostic only: with %d act "
                                 "bins a rank correlation carries almost no "
                                 "information and is not used to classify." % n_acts),
          confidence="low")


def fam_transitions(C, A, B):
    F = "2. transitions"
    ta, tb = A.transition_counts(), B.transition_counts()
    if not ta or not tb:
        for k, lab in (("transition_mix", "transition-type mix (chi-square)"),
                       ("hard_cut_share", "hard-cut share"),
                       ("pass_through_share", "pass-through share (B+C+D)")):
            C.insufficient(k, F, lab, None, None,
                           "no transition labels in at least one episode")
        return

    # A threshold mismatch does not invalidate the comparison, but it does
    # confound it: the luma thresholds are grade-dependent. [R:transitions]
    pa, pb = A.param("transition_thresholds"), B.param("transition_thresholds")
    caveat = None
    if pa and pb and pa != pb:
        caveat = ("the two episodes were classified with DIFFERENT transition "
                  "thresholds (%s vs %s); part of any difference below is a "
                  "threshold artifact, not an editing difference" % (pa, pb))

    res = chi2_homogeneity(dict(ta), dict(tb))
    na, nb = sum(ta.values()), sum(tb.values())
    det = dict(counts_a=dict(ta), counts_b=dict(tb),
               share_a={k: round(100.0 * v / na, 1) for k, v in sorted(ta.items())},
               share_b={k: round(100.0 * v / nb, 1) for k, v in sorted(tb.items())},
               interior_boundaries_a=na, interior_boundaries_b=nb,
               chi2=res["chi2"], df=res["df"],
               merged_sparse_categories=res["merged"],
               categories_tested=res["used_categories"])
    if caveat:
        det["threshold_caveat"] = caveat
    C.by_test("transition_mix", F, "transition-type mix (chi-square)", None, None,
              res["p"], detail=det, confidence="low" if caveat else "normal")

    for key, lab, codes in (("hard_cut_share", "hard-cut share (A)", ("A",)),
                            ("pass_through_share",
                             "pass-through share (B+C+D)", ("B", "C", "D"))):
        ka = sum(ta.get(c, 0) for c in codes)
        kb = sum(tb.get(c, 0) for c in codes)
        z = two_proportion_z(ka, na, kb, nb)
        C.by_test(key, F, lab, _r(100.0 * ka / na, 1), _r(100.0 * kb / nb, 1), z["p"],
                  detail=dict(k_a=ka, n_a=na, k_b=kb, n_b=nb, z=z["z"],
                              diff_pp=None if z["diff"] is None else 100.0 * z["diff"],
                              ci95_diff_pp=None if z["ci95_diff"] is None else
                              [100.0 * v for v in z["ci95_diff"]],
                              power_note="at these n the smallest detectable "
                                         "difference is about %.0f pp"
                                         % (100 * 1.96 * math.sqrt(
                                             0.25 * (1.0 / na + 1.0 / nb)))),
                  unit="% of interior boundaries", confidence="low")


def _curve_row(C, key, family, label, A, B, col, unit, level_key=None,
               level_label=None, level_unit=None, guard=None):
    """Shared machinery for warm / V / db / luma: resample both episodes'
    per-minute series to CURVE_POINTS normalized-time points, correlate the
    shapes, and separately compare the runtime-weighted level."""
    if guard:
        C.insufficient(key, family, label, None, None, guard, unit=unit)
        if level_key:
            C.insufficient(level_key, family, level_label, None, None, guard,
                           unit=level_unit)
        return

    ta, va = A.minute_curve(col)
    tb, vb = B.minute_curve(col)
    if va is None or vb is None:
        who = "A" if va is None else ""
        who += "B" if vb is None else ""
        reason = ("per-minute '%s' is null in episode %s. In this pipeline that "
                  "means no pixel pass was run (--rgb): the quantity requires the "
                  "source video and is never synthesised." % (col, who or "?")) \
            if col in ("warm", "V") else \
            "per-minute '%s' missing in episode %s" % (col, who or "?")
        C.insufficient(key, family, label, None, None, reason, unit=unit)
        if level_key:
            C.insufficient(level_key, family, level_label, None, None, reason,
                           unit=level_unit)
        return

    ca = resample_curve(ta, va)
    cb = resample_curve(tb, vb)
    rho = spearman(ca, cb)
    rmse = float(np.sqrt(np.mean((ca - cb) ** 2)))
    C.by_corr(key, family, label, rho,
              detail=dict(n_points=CURVE_POINTS,
                          n_independent_bins=min(len(va), len(vb)),
                          pearson=pearson(ca, cb), rmse=rmse,
                          mean_a=float(np.mean(ca)), mean_b=float(np.mean(cb)),
                          curve_a=[round(float(v), 3) for v in ca],
                          curve_b=[round(float(v), 3) for v in cb],
                          autocorrelation_note="the %d points are interpolated from "
                                               "%d and %d source bins; no p-value is "
                                               "reported because the points are not "
                                               "independent"
                                               % (CURVE_POINTS, len(va), len(vb))),
              confidence="low")

    if level_key:
        # runtime-weighted level: weight each minute by the rows it actually
        # covers, so a truncated final minute cannot dominate. [R:perminute]
        la = _weighted_level(A, col)
        lb = _weighted_level(B, col)
        C.scalar(level_key, family, level_label, la, lb,
                 detail=dict(unweighted_mean_a=float(np.mean(va)),
                             unweighted_mean_b=float(np.mean(vb)),
                             note="runtime-weighted by per-minute row counts"))


def _weighted_level(E, col):
    pm = E.per_minute()
    w = E.minute_weights()
    vals, wts = [], []
    for row, wi in zip(pm, w):
        if row.get(col) is None:
            continue
        vals.append(float(row[col]))
        wts.append(float(wi))
    if not vals or sum(wts) <= 0:
        return None
    return float(np.average(vals, weights=wts))


def fam_color(C, A, B):
    F = "3. colour / brightness"

    # ---- the warm guard, straight out of recon --------------------------
    # The warm predicate (hue window, S floor, V floor) was never published and
    # is not recoverable.  Two episodes measured with different predicates are
    # not comparable at all, and saying so is the whole point. [R:color-narrative]
    wa, wb = A.param("warm_predicate"), B.param("warm_predicate")
    guard = None
    KEYS = ("hue_lo", "hue_hi", "s_min", "v_min")
    ka = {k: wa.get(k) for k in KEYS} if isinstance(wa, dict) else None
    kb = {k: wb.get(k) for k in KEYS} if isinstance(wb, dict) else None
    # An UNKNOWN predicate is exactly as incomparable as a DIFFERENT one: if a
    # dir does not stamp what its warm numbers mean, we cannot know they mean
    # the same thing as the other side's.  Refusing only on a stamped mismatch
    # would let the commonest real case (a hand-assembled or older episode dir)
    # through silently, which is the failure this guard exists to prevent.
    if ka is None or kb is None:
        who = "A" if ka is None else ""
        who += "B" if kb is None else ""
        guard = ("episode %s does not declare a warm_predicate in its "
                 "derive_manifest.json, so what its warm numbers MEAN is unknown. "
                 "The predicate (hue window, saturation and value floors) is a free "
                 "parameter that the reference artifact never published and that "
                 "recon could not recover; an undeclared predicate is exactly as "
                 "incomparable as a different one, so these numbers are not "
                 "compared." % (who or "?"))
    elif ka != kb:
        guard = ("the two episodes' warm shares were computed with DIFFERENT "
                 "warm predicates (%s vs %s). The predicate is a free "
                 "parameter that the reference artifact never published and "
                 "that recon could not recover, so these two numbers measure "
                 "different things and must not be compared." % (ka, kb))
    _curve_row(C, "warm_curve", F, "warm-ratio curve (100 normalized points)",
               A, B, "warm", "spearman rho",
               level_key="warm_level", level_label="warm level (runtime-weighted mean)",
               level_unit="percentage points", guard=guard)

    # warm dynamic range
    _, va = A.minute_curve("warm")
    _, vb = B.minute_curve("warm")
    if guard:
        C.insufficient("warm_dynamic_range", F, "warm dynamic range (max-min)",
                       None, None, guard, unit="percentage points")
    elif va is None or vb is None:
        C.insufficient("warm_dynamic_range", F, "warm dynamic range (max-min)",
                       None, None, "warm is null (needs the source video)",
                       unit="percentage points")
    else:
        C.scalar("warm_dynamic_range", F, "warm dynamic range (max-min)",
                 float(va.max() - va.min()), float(vb.max() - vb.min()),
                 detail=dict(min_a=float(va.min()), max_a=float(va.max()),
                             min_b=float(vb.min()), max_b=float(vb.max())))

    _curve_row(C, "V_curve", F, "V (HSV Value) curve", A, B, "V", "spearman rho",
               level_key="V_level", level_label="V level (runtime-weighted mean)",
               level_unit="0-100 V")
    _curve_row(C, "db_curve", F, "loudness curve (per-minute dBFS)", A, B, "db",
               "spearman rho", level_key="db_level",
               level_label="loudness level (runtime-weighted mean dBFS)",
               level_unit="dBFS")

    # ---- full-resolution luma, only if a stage-1 CSV was staged in -------
    def _ts_coverage(E):
        """Fraction of the episode's runtime the staged CSV actually covers."""
        if E.ts is None or "luma_mean" not in E.ts or not _fin(E.runtime) or E.runtime <= 0:
            return None
        return len(E.ts["luma_mean"]) / (E.runtime * 10.0)

    cov_a, cov_b = _ts_coverage(A), _ts_coverage(B)
    if (cov_a is not None and cov_b is not None
            and (cov_a < 0.98 or cov_b < 0.98)):
        # binned() below normalizes by the CSV's own length, so a short CSV would
        # be silently STRETCHED to fill normalized time and compared as if it
        # covered the whole episode. Refuse instead of emitting that.
        C.insufficient("luma_curve", F, "mean-luma curve (100 normalized points)",
                       None, None,
                       "the staged timeseries does not cover the episode: CSV spans "
                       "%.1f%% of runtime in A and %.1f%% in B. The curve is "
                       "normalized by the CSV's own length, so a short CSV would be "
                       "stretched to fill normalized time and silently compared as "
                       "if it covered the whole episode."
                       % (100 * cov_a, 100 * cov_b), unit="spearman rho")
    elif A.ts is not None and B.ts is not None and "luma_mean" in A.ts and "luma_mean" in B.ts:
        def binned(E):
            y = E.ts["luma_mean"]
            k = min(len(y), int(round(E.runtime * 10)))
            y = y[:k]
            idx = np.minimum((np.arange(k) / float(k) * CURVE_POINTS).astype(int),
                             CURVE_POINTS - 1)
            out = np.array([y[idx == i].mean() if np.any(idx == i) else np.nan
                            for i in range(CURVE_POINTS)])
            return out
        ca, cb = binned(A), binned(B)
        C.by_corr("luma_curve", F, "mean-luma curve (100 normalized points)",
                  spearman(ca, cb),
                  detail=dict(source="stage-1 timeseries luma_mean, binned to %d "
                                     "equal normalized-time bins" % CURVE_POINTS,
                              csv_coverage_of_runtime_a=round(cov_a, 4),
                              csv_coverage_of_runtime_b=round(cov_b, 4),
                              pearson=pearson(ca, cb),
                              rmse=float(np.sqrt(np.nanmean((ca - cb) ** 2))),
                              mean_a=float(np.nanmean(ca)), mean_b=float(np.nanmean(cb))),
                  confidence="normal")
    else:
        C.insufficient("luma_curve", F, "mean-luma curve (100 normalized points)",
                       None, None,
                       "no stage-1 timeseries_10fps.csv staged in the episode dir; "
                       "luma is not carried in derive.py's outputs",
                       unit="spearman rho")


def fam_chapters(C, A, B):
    F = "4. chapters"
    la, lb = A.chapter_lengths(), B.chapter_lengths()
    if la is None or lb is None or len(la) < 2 or len(lb) < 2:
        for k, lab in (("chapters_per_10min", "chapters per 10 min"),
                       ("chapter_len_shape", "chapter length shape (CV, ex-ending)"),
                       ("chapter_ending_ratio", "ending chapter / body mean")):
            C.insufficient(k, F, lab, None, None,
                           "chapterRows missing or too short in at least one "
                           "episode (chapters are editorial input, not measured)")
        return
    C.scalar("chapters_per_10min", F, "chapters per 10 min",
             len(la) / (A.runtime / 600.0), len(lb) / (B.runtime / 600.0),
             detail=dict(n_a=len(la), n_b=len(lb)))

    # normalized lengths; the ending chapter is a known structural outlier
    # (3.13x the body mean on the reference episode: 259 s vs 80.9 s) so it is
    # excluded from the shape statistic and compared separately. [R:chapters]
    def shape(l, runtime):
        nl = l / runtime
        body = nl[:-1] if len(nl) > 2 else nl
        cv = float(body.std() / body.mean()) if body.mean() > 0 else float("nan")
        cv_all = float(nl.std() / nl.mean()) if nl.mean() > 0 else float("nan")
        ratio = float(l[-1] / l[:-1].mean()) if len(l) > 1 and l[:-1].mean() > 0 else float("nan")
        return cv, cv_all, ratio, nl
    ca, caa, ra, nla = shape(la, A.runtime)
    cb, cba, rb, nlb = shape(lb, B.runtime)
    C.scalar("chapter_len_shape", F, "chapter length shape (CV, ex-ending)", ca, cb,
             detail=dict(cv_including_ending_a=caa, cv_including_ending_b=cba,
                         normalized_lengths_a=[round(float(v), 4) for v in nla],
                         normalized_lengths_b=[round(float(v), 4) for v in nlb],
                         note="population CV of chapter length / runtime, final "
                              "chapter excluded"))
    C.scalar("chapter_ending_ratio", F, "ending chapter / body mean", ra, rb,
             detail=dict(ending_s_a=float(la[-1]), body_mean_s_a=float(la[:-1].mean()),
                         ending_s_b=float(lb[-1]), body_mean_s_b=float(lb[:-1].mean())))


def fam_silence(C, A, B):
    F = "5. silence / narration"
    sa, sb = A.silences, B.silences
    if not sa or not sb:
        for k, lab in (("silences_per_10min", "silences >=thr per 10 min"),
                       ("silence_positions", "silence positions (normalized, KS)"),
                       ("narration_coverage", "narration coverage"),
                       ("duck_median_db", "ducking (median dB)"),
                       ("centroid_ratio", "centroid ratio narration/bed")):
            C.insufficient(k, F, lab, None, None,
                           "silences.json missing in at least one episode "
                           "(needs --transcript at stage 2)")
        return

    # A different silence threshold changes the count by design, so the rate
    # comparison is void rather than merely noisy. [R:audio-silence]
    tha, thb = sa.get("threshold_s"), sb.get("threshold_s")
    if tha is None or thb is None:
        # An undeclared threshold is as incomparable as a different one: the
        # event count is razor-thin in it (recon: n=14 holds only for a 0.02 s
        # window around 3.0 s), so two counts gathered at unknown thresholds are
        # not the same measurement and must not be tested against each other.
        who = "A" if tha is None else ""
        who += "B" if thb is None else ""
        C.insufficient("silences_per_10min", F, "silence rate (threshold undeclared)",
                       len(sa.get("events") or []), len(sb.get("events") or []),
                       "episode %s's silences.json does not declare threshold_s, so "
                       "its event count is not a defined quantity. Recon found the "
                       "count is razor-thin in the threshold — n=14 holds only for a "
                       "0.02 s window around 3.0 s — so an undeclared threshold makes "
                       "the two counts incomparable." % (who or "?"), unit="events")
    elif abs(float(tha) - float(thb)) > 1e-9:
        C.insufficient("silences_per_10min", F, "silences >=thr per 10 min",
                       len(sa.get("events") or []), len(sb.get("events") or []),
                       "different silence thresholds (%s s vs %s s): the counts are "
                       "not the same measurement. Recon also found the count is "
                       "razor-thin in the threshold — n=14 holds only for a 0.02 s "
                       "window around 3.0 s." % (tha, thb))
    else:
        ea = sa.get("events") or []
        eb = sb.get("events") or []
        na, nb = len(ea), len(eb)
        rate_a = na / (A.runtime / 600.0)
        rate_b = nb / (B.runtime / 600.0)
        # exact conditional binomial: given na+nb events, is the split consistent
        # with the runtime split?
        share_a = A.runtime / (A.runtime + B.runtime)
        p = binom_test_two_sided(na, na + nb, share_a) if (na + nb) > 0 else float("nan")
        C.by_test("silences_per_10min", F,
                  "silences >=%.1f s per 10 min" % float(tha),
                  _r(rate_a, 2), _r(rate_b, 2), p,
                  detail=dict(n_a=na, n_b=nb, threshold_s=tha,
                              runtime_share_a=share_a,
                              test="exact conditional binomial on the event split",
                              power_note="with %d total events this test cannot "
                                         "resolve rate ratios below roughly %.2fx"
                                         % (na + nb,
                                            1.0 + 2.0 / math.sqrt(max(1, na + nb)))),
                  confidence="low")

    # normalized positions: deliberately runtime-free, so a pure time-stretch
    # must NOT move this row.
    def taus(E, s):
        ev = s.get("events") or []
        if not ev or not math.isfinite(E.runtime) or E.runtime <= 0:
            return None
        return np.array([float(e["t_in"]) / E.runtime for e in ev], dtype=float)
    xa, xb = taus(A, sa), taus(B, sb)
    if xa is None or xb is None or len(xa) < 3 or len(xb) < 3:
        C.insufficient("silence_positions", F, "silence positions (normalized, KS)",
                       None, None, "fewer than 3 silence events in at least one episode")
    else:
        d, p = ks_2samp(xa, xb)
        C.by_test("silence_positions", F, "silence positions (normalized, KS)",
                  _r(float(np.median(xa)), 3), _r(float(np.median(xb)), 3), p,
                  detail=dict(ks_statistic=d, n_a=len(xa), n_b=len(xb),
                              tau_a=[round(float(v), 4) for v in np.sort(xa)],
                              tau_b=[round(float(v), 4) for v in np.sort(xb)],
                              power_note="at n=%d/%d this KS only rejects above "
                                         "D~%.2f; a pass is close to uninformative"
                                         % (len(xa), len(xb),
                                            1.36 * math.sqrt(1.0 / len(xa) + 1.0 / len(xb))),
                              invariance_note="normalized by runtime on purpose: a "
                                              "uniform time-stretch must leave this "
                                              "row unchanged"),
                  unit="tau (median)", confidence="low")

    # narration aggregates. The reference report mixed two mask conventions in
    # one table [R:audio-silence]; prefer the same convention on both sides.
    def narr(s):
        n = s.get("narration") or {}
        for conv in ("sample_time", "center_of_frame"):
            if isinstance(n.get(conv), dict):
                return n[conv], conv
        return (n if n else None), "flat"
    na_, conv_a = narr(sa)
    nb_, conv_b = narr(sb)
    if not na_ or not nb_:
        for k, lab in (("narration_coverage", "narration coverage"),
                       ("duck_median_db", "ducking (median dB)"),
                       ("centroid_ratio", "centroid ratio narration/bed")):
            C.insufficient(k, F, lab, None, None, "no narration block in silences.json")
        return
    if conv_a != conv_b:
        # Same doctrine as the warm guard: two numbers computed under different
        # mask conventions are different quantities.  The reference report itself
        # mixed the two conventions in one table [R:audio-silence]; that is the
        # mistake this refusal exists to prevent, so it is a refusal and not a
        # footnote on a classified row.
        why = ("the two episodes' narration aggregates were computed with "
               "DIFFERENT caption-mask conventions (A: '%s', B: '%s'). The two "
               "conventions disagree on the reference episode, and mixing them in "
               "one table is the exact error the reference report made "
               "[R:audio-silence], so these numbers are not compared." % (conv_a, conv_b))
        for k, lab in (("narration_coverage", "narration coverage"),
                       ("duck_median_db", "ducking (median dB)"),
                       ("centroid_ratio", "centroid ratio narration/bed")):
            C.insufficient(k, F, lab, None, None, why)
        return
    conv_note = "both episodes read with the '%s' narration mask" % conv_a
    C.scalar("narration_coverage", F, "narration coverage",
             na_.get("coverage_pct"), nb_.get("coverage_pct"),
             detail=dict(mask_convention=conv_note))
    C.scalar("duck_median_db", F, "ducking (median dB)",
             na_.get("duck_median_db"), nb_.get("duck_median_db"),
             detail=dict(mask_convention=conv_note,
                         mean_variant_a=na_.get("duck_mean_db"),
                         mean_variant_b=nb_.get("duck_mean_db"),
                         definition_note="median, not mean. On the reference "
                                         "episode the two differ by 1.7 dB, which "
                                         "is larger than this row's tolerance."),
             confidence="low")
    def ratio(d):
        x, y = d.get("centroid_narration_hz"), d.get("centroid_non_narration_hz")
        return (float(x) / float(y)) if (x and y) else None
    C.scalar("centroid_ratio", F, "centroid ratio narration/bed",
             ratio(na_), ratio(nb_),
             detail=dict(narration_hz_a=na_.get("centroid_narration_hz"),
                         bed_hz_a=na_.get("centroid_non_narration_hz"),
                         narration_hz_b=nb_.get("centroid_narration_hz"),
                         bed_hz_b=nb_.get("centroid_non_narration_hz"),
                         mask_convention=conv_note))


def fam_camera(C, A, B):
    F = "6. camera"
    ra, rb = A.camera_rates(), B.camera_rates()
    if ra is None or rb is None or len(ra) < 5 or len(rb) < 5:
        for k, lab in (("camera_speed_median", "camera displacement rate median"),
                       ("camera_speed_p90", "camera displacement rate p90"),
                       ("camera_speed_distribution", "camera displacement distribution (KS)"),
                       ("camera_move_mix", "camera move mix (chi-square)"),
                       ("camera_static_share", "near-static share")):
            C.insufficient(k, F, lab, None, None, "camera.json missing or too short")
        return
    wa = A.param("camera", "proxy_width_px") or 96
    wb = B.param("camera", "proxy_width_px") or 96
    note = dict(proxy_width_px_a=wa, proxy_width_px_b=wb,
                unit_note="net trimmed displacement per scene, hypot(cum_x,cum_y) "
                          "/ proxy width / duration, in % of frame width per second "
                          "— resolution-free so two different proxies still compare")
    C.scalar("camera_speed_median", F, "camera displacement rate median",
             float(np.median(ra)), float(np.median(rb)),
             detail=dict(n_a=len(ra), n_b=len(rb), **note))
    C.scalar("camera_speed_p90", F, "camera displacement rate p90",
             float(np.percentile(ra, 90)), float(np.percentile(rb, 90)), detail=note)
    d, p = ks_2samp(ra, rb)
    C.by_test("camera_speed_distribution", F, "camera displacement distribution (KS)",
              _r(float(np.median(ra)), 3), _r(float(np.median(rb)), 3), p,
              detail=dict(ks_statistic=d, n_a=len(ra), n_b=len(rb),
                          quantisation_note="pan_dx/pan_dy are whole pixels on a "
                                            "%s/%s px proxy and ~70%% of samples are "
                                            "exactly zero, so these distributions "
                                            "are heavily tied" % (wa, wb), **note),
              confidence="low")

    ma, hza = A.camera_axes()
    mb, hzb = B.camera_axes()
    if not ma or not mb:
        C.insufficient("camera_move_mix", F, "camera move mix (chi-square)", None, None,
                       "no camera labels")
        C.insufficient("camera_static_share", F, "near-static share", None, None,
                       "no camera labels")
        return
    zoom_note = ("zoom clauses included in both episodes" if (hza and hzb) else
                 "zoom axis EXCLUDED from the taxonomy: at least one episode has no "
                 "pixel pass, so its labels carry no zoom clause. The comparison is "
                 "restricted to pan/tilt/static.")
    res = chi2_homogeneity(dict(ma), dict(mb))
    C.by_test("camera_move_mix", F, "camera move mix (chi-square)", None, None,
              res["p"],
              detail=dict(mix_a=dict(ma), mix_b=dict(mb), chi2=res["chi2"],
                          df=res["df"], merged_sparse_categories=res["merged"],
                          categories_tested=res["used_categories"],
                          zoom_note=zoom_note))
    ka, kb = ma.get("static", 0), mb.get("static", 0)
    z = two_proportion_z(ka, sum(ma.values()), kb, sum(mb.values()))
    C.by_test("camera_static_share", F, "near-static share",
              _r(100.0 * ka / sum(ma.values()), 1), _r(100.0 * kb / sum(mb.values()), 1),
              z["p"], detail=dict(k_a=ka, n_a=sum(ma.values()), k_b=kb,
                                  n_b=sum(mb.values()), z=z["z"],
                                  ci95_diff_pp=None if z["ci95_diff"] is None else
                                  [100.0 * v for v in z["ci95_diff"]]),
              unit="% of measured scenes", confidence="low")


def fam_palette(C, A, B, hue_lo, hue_hi):
    F = "7. palette"
    ha, hb = A.palette_hue_hist(), B.palette_hue_hist()
    if ha is None or hb is None:
        missing = [n for n, E, h in (("A", A, ha), ("B", B, hb))
                   if h is None and not E.palette]
        broken = [n for n, E, h in (("A", A, ha), ("B", B, hb))
                  if h is None and E.palette]
        parts = []
        if missing:
            parts.append("no palette in episode %s: derive.py emits "
                         "metrics.palette = null without a pixel pass, because the "
                         "k-means palette requires the source video"
                         % "/".join(missing))
        if broken:
            parts.append("episode %s HAS a palette but at least one entry is "
                         "missing H or share, so no share-weighted hue histogram "
                         "can be built from it" % "/".join(broken))
        r = "; ".join(parts) + "."
        C.insufficient("palette_hue_distance", F, "palette hue distance (circular EMD)",
                       None, None, r)
        C.insufficient("palette_warm_share", F, "palette warm share", None, None, r)
        return
    bin_deg = 360.0 / HUE_BINS
    emd = circular_emd(ha, hb, HUE_BINS) * bin_deg
    t = C._tol("palette_hue_distance")
    C.add("palette_hue_distance", F, "palette hue distance (circular EMD)",
          None, None, CHANNEL_RULE if emd <= t["value"] else EPISODE_CHOICE,
          detail=dict(circular_emd_degrees=emd, threshold=t["value"],
                      bins=HUE_BINS, bin_width_deg=bin_deg,
                      hist_a=[round(float(v), 2) for v in ha],
                      hist_b=[round(float(v), 2) for v in hb],
                      note="optimal circular transport cost. It is NOT a "
                           "rotation detector: rigidly rotating a MULTI-LOBED "
                           "palette costs less than the rotation angle, because "
                           "optimal transport re-pairs the lobes (measured on "
                           "this repo's fixtures: a +120 deg rotation of the "
                           "reference two-lobe palette costs 72.9 deg; the same "
                           "rotation of a single-lobe palette costs exactly "
                           "120 deg). Read it as 'how far the colour mass moved', "
                           "not as an angle of rotation."))
    C.scalar("palette_warm_share", F, "palette warm share",
             A.palette_warm_share(hue_lo, hue_hi), B.palette_warm_share(hue_lo, hue_hi),
             detail=dict(hue_boundary=dict(warm_if_H_ge=hue_lo, or_H_lt=hue_hi),
                         boundary_status="NOT RECOVERED from the reference artifact; "
                                         "this is a stated default. Two palettes whose "
                                         "hues straddle the boundary can differ here "
                                         "on the threshold choice alone."),
             confidence="low")


def fam_subjects(C, A, B):
    F = "8. subject / face budget"
    sa, sb = A.subjects, B.subjects
    if not isinstance(sa, dict) or not isinstance(sb, dict):
        C.insufficient("subject_budget", F, "subject / face budget", None, None,
                       "no subjects.json / faces.json in at least one episode. No "
                       "stage of this pipeline produces one; a face pass would need "
                       "the source video.")
        return
    keys = sorted(set(k for k in sa if _fin(sa.get(k))) &
                  set(k for k in sb if _fin(sb.get(k))))
    if not keys:
        C.insufficient("subject_budget", F, "subject / face budget", None, None,
                       "subjects files present but share no numeric field")
        return
    t = C._tol("subject_budget")
    worst, wk = -1.0, None
    per = {}
    for k in keys:
        a, b = float(sa[k]), float(sb[k])
        den = (abs(a) + abs(b)) / 2.0
        rel = abs(a - b) / den if den > 0 else (0.0 if a == b else float("inf"))
        per[k] = dict(a=a, b=b, rel_diff=round(rel, 4))
        if rel > worst:
            worst, wk = rel, k
    C.add("subject_budget", F, "subject / face budget (worst field: %s)" % wk,
          per[wk]["a"], per[wk]["b"],
          CHANNEL_RULE if worst <= t["value"] else EPISODE_CHOICE,
          detail=dict(fields=per, worst_field=wk, worst_rel_diff=worst,
                      threshold=t["value"]))


def _r(x, nd):
    try:
        f = float(x)
        return round(f, nd) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# =============================================================================
# 7.  RENDERING
# =============================================================================

BANNER = (
    "n = 2. Two episodes cannot establish a channel rule. A CHANNEL_RULE row "
    "below means only that the proposed invariant SURVIVED one falsification "
    "attempt; an EPISODE_CHOICE row means it was actually refuted. "
    "The refutations are the load-bearing findings; the agreements are not "
    "evidence of a rule and are labelled accordingly."
)

MD_VERDICT = {
    CHANNEL_RULE: "consistent (n=2, **not established**)",
    EPISODE_CHOICE: "**differs** -> episode choice",
    INSUFFICIENT: "insufficient",
}


def fmt(v, nd=2):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(f):
        return "—"
    return ("%.*f" % (nd, f)).rstrip("0").rstrip(".") if nd else "%d" % round(f)


def render_md(report):
    A, B = report["episodes"]["a"], report["episodes"]["b"]
    L = []
    L.append("# Episode comparison — channel rule vs episode choice\n")
    L.append("| | episode A | episode B |")
    L.append("|---|---|---|")
    L.append("| name | `%s` | `%s` |" % (A["name"], B["name"]))
    L.append("| dir | `%s` | `%s` |" % (A["path"], B["path"]))
    L.append("| runtime | %s s (%s) | %s s (%s) |"
             % (fmt(A["runtime_s"], 1), A["runtime_mmss"],
                fmt(B["runtime_s"], 1), B["runtime_mmss"]))
    L.append("| scenes | %s | %s |" % (fmt(A["n_scenes"]), fmt(B["n_scenes"])))
    L.append("| chapters | %s | %s |" % (fmt(A["n_chapters"]), fmt(B["n_chapters"])))
    L.append("")
    L.append("> **%s**\n" % BANNER)

    s = report["summary"]
    L.append("## Summary\n")
    L.append("| verdict | metrics | meaning |")
    L.append("|---|---:|---|")
    L.append("| consistent (n=2, not established) | %d | survived one falsification "
             "attempt — NOT a demonstrated channel rule |" % s["CHANNEL_RULE"])
    L.append("| differs -> episode choice | %d | refuted as an invariant by this "
             "one extra episode |" % s["EPISODE_CHOICE"])
    L.append("| insufficient | %d | absent in one episode, or not comparable |"
             % s["INSUFFICIENT"])
    L.append("| **total** | **%d** | |" % s["total"])
    L.append("")
    if report["blocking_notes"]:
        L.append("### Comparability warnings\n")
        for n in report["blocking_notes"]:
            L.append("- %s" % n)
        L.append("")

    fams = []
    for row in report["metrics"]:
        if row["family"] not in fams:
            fams.append(row["family"])
    for fam in fams:
        L.append("## %s\n" % fam)
        L.append("| metric | A | B | unit | verdict | tolerance | key numbers |")
        L.append("|---|---:|---:|---|---|---|---|")
        for row in report["metrics"]:
            if row["family"] != fam:
                continue
            tol = row.get("tolerance") or {}
            tt = "—"
            if tol.get("kind") == "rel":
                tt = "within %.0f%%" % (100 * tol["value"])
            elif tol.get("kind") == "abs":
                tt = "within %s %s" % (fmt(tol["value"]), row.get("unit") or "")
            elif tol.get("kind") == "corr":
                tt = "rho >= %.2f" % tol["value"]
            elif tol.get("kind") == "pmin":
                tt = "p >= %.2f" % tol["value"]
            d = row.get("detail") or {}
            bits = []
            for k in ("spearman", "p_value", "ks_statistic", "chi2",
                      "rel_diff", "abs_diff", "circular_emd_degrees",
                      "worst_rel_diff", "rmse"):
                if k in d and d[k] is not None:
                    bits.append("%s=%s" % (k, fmt(d[k], 3)))
            if row.get("reason"):
                bits = [row["reason"][:150] + ("…" if len(row["reason"]) > 150 else "")]
            flag = " ⚠" if row.get("confidence") == "low" else ""
            L.append("| `%s` %s%s | %s | %s | %s | %s | %s | %s |" % (
                row["metric"], row["label"], flag,
                fmt(row["a"], 3), fmt(row["b"], 3), row.get("unit") or "",
                MD_VERDICT[row["classification"]], tt,
                "; ".join(bits) if bits else ""))
        L.append("")

    L.append("## Tolerances and why each one is what it is\n")
    seen = set()
    for row in report["metrics"]:
        k = row["metric"]
        if k in seen:
            continue
        seen.add(k)
        tol = row.get("tolerance") or {}
        if not tol.get("justification"):
            continue
        L.append("**`%s`** — %s `%s` %s\n" % (
            k, {"rel": "within", "abs": "within", "corr": "rho >=",
                "pmin": "p >="}.get(tol["kind"], ""), fmt(tol["value"], 3),
            "(relative)" if tol["kind"] == "rel" else ""))
        L.append("> %s\n" % tol["justification"])

    L.append("## Caveats that apply to the whole table\n")
    for c in report["caveats"]:
        L.append("- %s" % c)
    L.append("")
    L.append("⚠ marks a row whose confidence is `low` — either the underlying "
             "statistic has very little power at these sample sizes, or the "
             "quantity itself rests on a parameter that was never recovered.\n")
    return "\n".join(L)


def mmss(t):
    if not _fin(t):
        return "—"
    t = float(t)
    return "%d:%05.2f" % (int(t // 60), t - 60 * int(t // 60))


# =============================================================================
# 8.  MAIN
# =============================================================================

CAVEATS = [
    "n=2. Nothing here establishes a channel rule. Agreement between two "
    "episodes is weak evidence at best; disagreement is strong evidence "
    "against an invariant. Read the EPISODE_CHOICE rows first.",
    "Curve rows (warm / V / luma / db) report a Spearman rho and NO p-value. "
    "The 100 comparison points are interpolated from ~20 independent minute "
    "bins, so they are strongly autocorrelated and any nominal p-value would "
    "be fiction. `n_independent_bins` in the detail block is the honest n.",
    "Every count-based row is under-powered. At ~70 boundaries and ~14 silence "
    "events per episode, the smallest detectable differences are roughly 16 "
    "percentage points and a 1.5x rate ratio respectively. A 'consistent' "
    "verdict on those rows mostly means the test could not see anything.",
    "warm and V require the source video. derive.py emits null for them unless "
    "a pixel pass was run, and the warm predicate (hue window, saturation and "
    "value floors) was never published by the reference artifact and could not "
    "be recovered. compare.py refuses to compare warm figures computed with "
    "different predicates rather than silently comparing different quantities.",
    "camera[].zoom also requires the source video. Without it the move "
    "taxonomy is restricted to pan/tilt/static and the restriction is stated "
    "on the row.",
    "The per-minute grid truncates its final window. Level means here are "
    "weighted by each window's true row count so a 48%-full final minute "
    "cannot dominate, but the curve rows still carry that bin at its true "
    "normalized centre.",
    "The loudness rows use a plain arithmetic mean of dBFS — a mean of logs. "
    "That is what the reference pipeline did, and it is dominated by quiet "
    "passages and by any digital-silence tail; it is not energy-correct "
    "loudness and should not be quoted as a mix spec.",
    "Chapter rows describe editorial input, not a measurement. No formula "
    "recovers chapter boundaries from the signal; they are supplied by hand at "
    "stage 2.",
]


def build_report(A, B, n_acts, hue_lo, hue_hi, tolerances):
    C = Comparison(tolerances)
    fam_pacing(C, A, B, n_acts)
    fam_transitions(C, A, B)
    fam_color(C, A, B)
    fam_chapters(C, A, B)
    fam_silence(C, A, B)
    fam_camera(C, A, B)
    fam_palette(C, A, B, hue_lo, hue_hi)
    fam_subjects(C, A, B)

    counts = Counter(r["classification"] for r in C.rows)
    # one line per distinct reason, not one per affected row
    blocking, _seen = [], set()
    for r in C.rows:
        why = r.get("reason") or ""
        if r["classification"] == INSUFFICIENT and "different" in why.lower() \
                and why not in _seen:
            _seen.add(why)
            blocking.append(why)
    # runtime mismatch is worth stating once, loudly
    if _fin(A.runtime) and _fin(B.runtime):
        ratio = max(A.runtime, B.runtime) / min(A.runtime, B.runtime)
        if ratio > 1.15:
            blocking.insert(0, "runtimes differ by %.2fx (%.1f s vs %.1f s). Every "
                               "row below is either a rate or a normalized-time "
                               "quantity, so this is handled — but raw counts in "
                               "the header are not comparable."
                            % (ratio, A.runtime, B.runtime))

    def epi(E):
        return OrderedDict([
            ("name", E.name), ("path", E.path),
            ("runtime_s", _r(E.runtime, 3)), ("runtime_mmss", mmss(E.runtime)),
            ("n_scenes", len(E.scenes) if E.scenes else None),
            ("n_chapters", len((E.metrics or {}).get("chapterRows") or []) or None),
            ("n_camera_scenes", len(E.camera) if E.camera else None),
            ("has_pixel_pass", bool(E.palette) or any(
                r.get("warm") is not None for r in E.per_minute())),
            ("parameters", (E.manifest.get("parameters") or None)),
            ("stage1_meta_present", bool(E.meta)),
            ("loader_notes", E.notes),
        ])

    return OrderedDict([
        ("tool", "compare.py (stage 3)"),
        ("n_episodes", N_EPISODES),
        ("statistical_disclaimer", BANNER),
        ("episodes", OrderedDict([("a", epi(A)), ("b", epi(B))])),
        ("settings", OrderedDict([
            ("curve_points", CURVE_POINTS), ("act_bins", n_acts),
            ("hue_bins", HUE_BINS),
            ("palette_warm_boundary", dict(hue_lo=hue_lo, hue_hi=hue_hi,
                                           status="stated default, NOT recovered")),
        ])),
        ("summary", OrderedDict([
            (CHANNEL_RULE, counts.get(CHANNEL_RULE, 0)),
            (EPISODE_CHOICE, counts.get(EPISODE_CHOICE, 0)),
            (INSUFFICIENT, counts.get(INSUFFICIENT, 0)),
            ("total", len(C.rows)),
        ])),
        ("blocking_notes", blocking),
        ("caveats", CAVEATS),
        ("metrics", C.rows),
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Stage 3: compare two derive.py episode dirs and classify each "
                    "metric as CHANNEL_RULE / EPISODE_CHOICE / INSUFFICIENT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="n=2 cannot establish a channel rule. See the honesty contract at "
               "the top of this file.")
    ap.add_argument("--a", required=True, help="episode A directory")
    ap.add_argument("--b", required=True, help="episode B directory")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--name-a", help="override episode A's display name")
    ap.add_argument("--name-b", help="override episode B's display name")
    ap.add_argument("--acts", type=int, default=DEFAULT_ACTS,
                    help="number of normalized act bins for the pacing profile "
                         "(default %d)" % DEFAULT_ACTS)
    ap.add_argument("--warm-hue-lo", type=float, default=330.0,
                    help="palette warm boundary, low edge (NOT a recovered value)")
    ap.add_argument("--warm-hue-hi", type=float, default=90.0,
                    help="palette warm boundary, high edge (NOT a recovered value)")
    ap.add_argument("--tolerances", help="JSON file overriding any of the declared "
                                         "tolerances, keyed by metric name")
    a = ap.parse_args(argv)

    # --acts: a "profile" needs at least two bins, and more bins than there are
    # scenes turns the worst-bin comparison into a 1-vs-0 count artifact.
    if a.acts < 2:
        sys.exit("compare.py: --acts must be at least 2 (a one-bin 'profile' is "
                 "just scenes_per_minute, which is already its own row); got %d"
                 % a.acts)
    if a.acts > 100:
        sys.exit("compare.py: --acts %d is far more bins than an episode has "
                 "scenes; the worst-bin statistic would be pure counting noise. "
                 "Use at most 100." % a.acts)

    tol = OrderedDict((k, dict(v)) for k, v in TOLERANCES.items())
    if a.tolerances:
        with open(a.tolerances, encoding="utf-8") as fh:
            over = json.load(fh)
        if not isinstance(over, dict):
            sys.exit("compare.py: --tolerances file must be a JSON object keyed by "
                     "metric name")
        for k, v in over.items():
            if k not in tol:
                sys.exit("compare.py: --tolerances names unknown metric %r" % k)
            if not isinstance(v, dict):
                sys.exit("compare.py: --tolerances[%r] must be an object, got %r"
                         % (k, type(v).__name__))
            if "kind" in v and v["kind"] != tol[k]["kind"]:
                sys.exit("compare.py: --tolerances may not change the KIND of a "
                         "metric (%r is %r, override asked for %r). The kind "
                         "selects which statistic is computed, not how strict it "
                         "is." % (k, tol[k]["kind"], v["kind"]))
            if "value" in v:
                if isinstance(v["value"], bool) or not isinstance(v["value"], (int, float)):
                    sys.exit("compare.py: --tolerances[%r]['value'] must be a "
                             "number, got %r" % (k, v["value"]))
                if tol[k]["kind"] == "none":
                    sys.exit("compare.py: %r is never classified and has no "
                             "tolerance to override" % k)
                if v["value"] < 0:
                    sys.exit("compare.py: --tolerances[%r]['value'] must be >= 0, "
                             "got %r" % (k, v["value"]))
                if tol[k]["kind"] == "pmin" and v["value"] > 1:
                    sys.exit("compare.py: --tolerances[%r] is a p-value threshold "
                             "and must be <= 1, got %r" % (k, v["value"]))
                if tol[k]["kind"] == "corr" and v["value"] > 1:
                    sys.exit("compare.py: --tolerances[%r] is a correlation "
                             "threshold and must be <= 1, got %r" % (k, v["value"]))
            tol[k].update(v)
            tol[k]["justification"] = (tol[k].get("justification", "") +
                                       "  [OVERRIDDEN via %s]" % a.tolerances)

    A = Episode(a.a, a.name_a)
    B = Episode(a.b, a.name_b)
    os.makedirs(a.out, exist_ok=True)
    report = build_report(A, B, a.acts, a.warm_hue_lo, a.warm_hue_hi, tol)

    jp = os.path.join(a.out, "comparison.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    mp = os.path.join(a.out, "comparison.md")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write(render_md(report) + "\n")

    s = report["summary"]
    print("A: %s  (%.1f s, %s scenes)" % (A.name, A.runtime, len(A.scenes or [])))
    print("B: %s  (%.1f s, %s scenes)" % (B.name, B.runtime, len(B.scenes or [])))
    print("%-16s %d  (consistent across 2 episodes — NOT established)"
          % (CHANNEL_RULE, s[CHANNEL_RULE]))
    print("%-16s %d  (refuted as an invariant)" % (EPISODE_CHOICE, s[EPISODE_CHOICE]))
    print("%-16s %d" % (INSUFFICIENT, s[INSUFFICIENT]))
    print("wrote %s" % jp)
    print("wrote %s" % mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
