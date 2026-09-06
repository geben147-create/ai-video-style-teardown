#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
derive.py — STAGE 2 of the video-teardown pipeline.

    timeseries (10 fps CSV)  ->  cut candidates
                             ->  transitions / camera / metrics / silences

USAGE
    # candidate pass (no human input yet): proposes boundaries + a worksheet
    python3 derive.py --ts timeseries_10fps.csv --out OUTDIR

    # full pass (after a human has verified the boundary list)
    python3 derive.py --ts timeseries_10fps.csv --scenes scenes_verified.json \
                      [--chapters chapters.json] [--transcript transcript.json] \
                      [--rgb rgb.npy] --out OUTDIR

PROVENANCE OF EVERY FORMULA
    Each rule below is tagged [R:<spec>] with the recon spec it was recovered in
    (perminute / chapters / camera / scene-confirm / transitions / cuts-raw /
     color-narrative / audio-silence).  Nothing here is invented: anything that
     could not be recovered from published data is emitted as null together with
     an explicit "requires_video" / "requires_human" marker.  See --help and
     derive_manifest.json in the output directory.

WHAT NEEDS PIXELS (never faked, always null + marker)
    metrics.perMinute[].V      HSV Value mean  -> needs RGB frames (--rgb)
    metrics.perMinute[].warm   warm-pixel share-> needs RGB frames AND a threshold
                               choice that is NOT recoverable from the artifact
    metrics.palette            k-means palette -> needs RGB frames
    camera[].zoom              radial-energy ratio -> needs RGB frames, and its
                               definition is undocumented even then
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter, OrderedDict

import numpy as np

# ----------------------------------------------------------------------------
# constants recovered in recon.  Grouped so a port to another episode can see
# exactly which numbers are channel-level and which are episode-level.
# ----------------------------------------------------------------------------

FPS = 10.0                     # timeseries sample rate (rows per second)

# --- cut-candidate detector -------------------------------------------- [R:cuts-raw]
# Two OR-ed arms.  Arm A sees hard cuts / angle changes (histogram-distance
# peaks), arm B sees dissolves / wipes / fades (sharpness collapses).  Neither
# arm alone exceeds ~66% recall on this genre; the union is required.
DET_TS = 0.395   # straddle_distance threshold (EPISODE-LEVEL: = p93.98 of the column)
DET_RS = 2       # local-max radius, rows, for arm A
DET_TD = 0.610   # sharpness_dip_ratio threshold (CHANNEL-LEVEL: dimensionless ratio)
DET_RD = 5       # local-min radius, rows, for arm B
DET_G  = 9       # minimum separation between accepted boundaries, rows (0.9 s)
# Measured sensitivity on the reference episode (recovered/98 published rows,
# gt/71 verified boundaries, candidates emitted):
#   TS 0.30-0.42 -> 97/98, 71/71 (138 .. 103 emitted); TS >= 0.45 collapses
#   TD 0.61-0.80 -> 97/98, 71/71 ( 105 .. 154 emitted); TD <= 0.60 loses rows
#   G  1-9       -> 97-98/98, 71/71;  G >= 11 loses rows
#   rD 7 instead of 5 -> 98/98 and 71/71 for ONE extra candidate (106).
# So 97/98 is the score of THIS operating point, not a ceiling: the single miss
# at t=170.9 s is recoverable at rD=7. rD=5 is kept because it is the setting
# that reproduces the published candidate COUNT behaviour most closely; the
# 97/98 figure must not be quoted as the detector's limit.

# --- per-boundary descriptors ------------------------------------- [R:scene-confirm]
DIP_WIN = (-6, 6)   # python slice [r-6 : r+6] == rows r-6..r+5 == t in [s-0.6, s+0.5]
LUMA_WIN = (-6, 6)  # same 12 rows, used for the whiteout / blackout tests [R:transitions]

# --- transition classifier (ordered decision list) ------------------ [R:transitions]
THR_LMAX_B = 200.0   # EPISODE-LEVEL (absolute 0-255 luma; depends on the grade)
THR_LMIN_C = 30.0    # EPISODE-LEVEL
THR_DIP_D  = 0.35    # CHANNEL-LEVEL-ish (self-normalised ratio), re-tune per episode
THR_DIP_E  = 0.62
THR_DIP_F  = 0.72

TRANS_LABEL = OrderedDict([
    ("A", "A. 하드컷"),
    ("B", "B. 화이트아웃 통과 (구름·안개)"),
    ("C", "C. 블랙아웃 통과 (아치·터널·야간)"),
    ("D", "D. 차폐물 와이프 (벽·인물·연기)"),
    ("E", "E. 크로스 디졸브"),
    ("F", "F. 앵글 전환 (동일 장소)"),
])
OPEN_LABEL = "OPEN"

# --- camera ---------------------------------------------------------------- [R:camera]
CAM_TRIM = 3          # rows dropped at EACH end of a scene (0.3 s) before summing pan
CAM_MIN_DUR = 1.0     # keep a scene iff dur > 1.0 s (feasible band 1.0 < T <= 1.2)
CAM_TX = 14.0         # |cum_x| px on a 96-px-wide proxy = 14.6% of frame width
CAM_TY = 10.0         # |cum_y| px = 10.4% of frame height-equivalent
CAM_ZLO = 0.955       # strict <  -> pull back
CAM_ZHI = 1.05        # strict >  -> push in
CAM_PROXY_W = 96      # the proxy width the pan series was measured on

LBL_PAN_R  = "좌→우 팬"
LBL_PAN_L  = "우→좌 팬"
LBL_TILT_D = "하강 틸트"
LBL_TILT_U = "상승 틸트"
LBL_ZOUT   = "풀백/줌아웃"
LBL_ZIN    = "푸시인/줌인"
LBL_STATIC = "고정에 가까움(미세 드리프트)"

# --- metrics windows ------------------------------------------ [R:perminute][R:chapters]
MINUTE_ROWS = int(round(60 * FPS))   # 600 rows per minute window

# --- silence ------------------------------------------------------ [R:audio-silence]
SILENCE_MAIN = 3.0    # the published "3초 이상 침묵 14회" threshold
SILENCE_ALSO = (2.0,) # also reported: "2초 이상 39회"
PAUSE_MIN    = 0.05   # "대사 사이 정지 460회 · 중앙값 0.43초"


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def r1(x):
    """Round to 1 decimal with Python's round (banker's).  Load-bearing: chapter 3
    of the reference episode is exactly 2.25 scenes/min and publishes 2.2, which
    half-up rounding would render 2.3.  [R:chapters]"""
    return round(float(x), 1)


def r3(x):
    return round(float(x), 3)


def row_of(t, n_rows=None):
    """Time (s) -> timeseries row.  round(), never int(): 45.4*10 == 453.99999...
    [R:camera][R:scene-confirm]"""
    r = int(round(float(t) * FPS))
    if n_rows is not None:
        r = max(0, min(n_rows - 1, r))
    return r


def timecode(t):
    """m:ss with FLOOR to the whole second (verified on all 14 published silences,
    e.g. 818.816 -> '13:38').  [R:audio-silence]"""
    s = int(math.floor(float(t)))
    return "%d:%02d" % (s // 60, s % 60)


def localmax(x, radius):
    """Strict on the left, non-strict on the right; out of range counts as -inf.
    Exactly the convention that reproduces the published boundary rows. [R:cuts-raw]"""
    out = np.ones(len(x), dtype=bool)
    for k in range(1, radius + 1):
        out &= x > np.r_[np.full(k, -np.inf), x[:-k]]
        out &= x >= np.r_[x[k:], np.full(k, -np.inf)]
    return out


def localmin(x, radius):
    return localmax(-x, radius)


def win(arr, r, lo_hi, n=None):
    """Python-slice window arr[r+lo : r+hi] clipped to the array."""
    lo, hi = lo_hi
    n = len(arr) if n is None else n
    a = max(0, r + lo)
    b = min(n, r + hi)
    return arr[a:b]


# ----------------------------------------------------------------------------
# input
# ----------------------------------------------------------------------------

REQUIRED_COLS = ["t_sec", "luma_mean", "sharpness_dip_ratio", "ssm_novelty",
                 "straddle_distance", "pan_dx", "pan_dy", "audio_rms_dbfs"]


def load_timeseries(path):
    """Read the 10 fps CSV into a dict of float arrays.  Blank cells -> NaN
    (frame_delta has one blank at row 0 in the reference artifact)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit("derive.py: %s has no rows" % path)
    cols = {}
    for c in rows[0].keys():
        vals = np.empty(len(rows), dtype=float)
        for i, r in enumerate(rows):
            v = r.get(c, "")
            vals[i] = float(v) if v not in ("", None, "nan", "NaN") else np.nan
        cols[c] = vals
    missing = [c for c in REQUIRED_COLS if c not in cols]
    if missing:
        sys.exit("derive.py: timeseries is missing required columns: %s" % ", ".join(missing))
    # the row grid must be the 0.1 s grid every recovered formula indexes into
    t = cols["t_sec"]
    step = np.diff(t)
    nonuniform = bool(len(step)) and not np.allclose(step, 1.0 / FPS, atol=1e-6)
    return cols, len(rows), nonuniform


def load_scenes(path):
    scenes = json.load(open(path, encoding="utf-8"))
    for s in scenes:
        for k in ("s", "e", "dur"):
            if k not in s:
                sys.exit("derive.py: scene %r lacks '%s'" % (s.get("i"), k))
    return scenes


def load_chapters(path):
    """Accepts either [[t0, name], ...] or [{"t0":..,"name":..}, ...] or
    {"chapters": [...]}.  Chapters are EDITORIAL INPUT: no formula recovers them
    from the timeseries.  [R:chapters]"""
    raw = json.load(open(path, encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("chapters", raw.get("chapterRows"))
    out = []
    for c in raw:
        if isinstance(c, (list, tuple)):
            out.append((float(c[0]), str(c[1])))
        else:
            out.append((float(c["t0"]), str(c.get("name", ""))))
    out.sort(key=lambda x: x[0])
    return out


# ----------------------------------------------------------------------------
# boundary descriptors + transition classifier
# ----------------------------------------------------------------------------

def boundary_fields(cols, n, row):
    """The per-boundary scalars, recovered 342/342 across scenes_verified.json and
    detection_pass2_99.json.  [R:scene-confirm][R:cuts-raw]

        D    = straddle_distance at the boundary row (a single sample, no window)
        dip  = MIN of sharpness_dip_ratio over rows [r-6 .. r+5]
        lmax/lmin = MAX/MIN of luma_mean over the same 12 rows  [R:transitions]
        novelty = ssm_novelty at the boundary row (arm-C signal, informational)
    """
    dipw = win(cols["sharpness_dip_ratio"], row, DIP_WIN, n)
    lumw = win(cols["luma_mean"], row, LUMA_WIN, n)
    # A window with no finite sample cannot produce a descriptor.  Falling through
    # would put NaN in D/dip and silently classify the boundary "A" (every NaN
    # comparison is False), i.e. invent a hard cut out of a hole in the input.
    for nm, w in (("sharpness_dip_ratio", dipw), ("luma_mean", lumw)):
        if len(w) == 0 or not np.isfinite(w).any():
            sys.exit("derive.py: no finite %s sample in rows [%d,%d) around t=%.1f s. "
                     "Cannot describe this boundary; fix the timeseries."
                     % (nm, max(0, row + DIP_WIN[0]), min(n, row + DIP_WIN[1]), row / FPS))
    if not np.isfinite(cols["straddle_distance"][row]):
        sys.exit("derive.py: straddle_distance is not finite at row %d (t=%.1f s)."
                 % (row, row / FPS))
    return {
        "D": r3(cols["straddle_distance"][row]),
        "dip": r3(np.nanmin(dipw)),
        "_dip_raw": float(np.nanmin(dipw)),
        "lmax": float(np.nanmax(lumw)),
        "lmin": float(np.nanmin(lumw)),
        "novelty": round(float(cols["ssm_novelty"][row]), 4),
    }


def classify(f):
    """Ordered decision list, first hit wins.  Reproduces 98/98 published labels
    (71 verified + 27 the human later deleted, never fitted).  [R:transitions]

    NOTE the published §9-1 table states extra conjuncts (a histogram-distance
    term for A, a sharpness term for B and C).  All three are INERT: dropping
    them changes zero predictions.  The rule has 5 load-bearing parameters."""
    if f["lmax"] >= THR_LMAX_B:
        return "B"
    if f["lmin"] <= THR_LMIN_C:
        return "C"
    d = f["_dip_raw"]
    if d < THR_DIP_D:
        return "D"
    if d < THR_DIP_E:
        return "E"
    if d < THR_DIP_F:
        return "F"
    return "A"


# ----------------------------------------------------------------------------
# 1. candidate detector
# ----------------------------------------------------------------------------

def detect_candidates(cols, n, ts=DET_TS, rs=DET_RS, td=DET_TD, rd=DET_RD, g=DET_G):
    """Two-arm union + min-gap merge.  Returns accepted rows, ascending.

    Arm A  straddle_distance local max (radius rs) with value >= ts
    Arm B  sharpness_dip_ratio local min (radius rd) with value <= td
    salience = max(straddle, 1 - clip(dip,0,1)) over whichever arms fired
    merge    = walk ascending; if within g rows of the last accepted boundary,
               keep whichever has the higher salience.
    [R:cuts-raw]"""
    strad = np.nan_to_num(cols["straddle_distance"], nan=-np.inf)
    dip = np.nan_to_num(cols["sharpness_dip_ratio"], nan=np.inf)
    mS = localmax(strad, rs) & (strad >= ts)
    mD = localmin(dip, rd) & (dip <= td)
    sal = np.zeros(n)
    sal[mS] = np.maximum(sal[mS], strad[mS])
    sal[mD] = np.maximum(sal[mD], 1.0 - np.clip(dip[mD], 0, 1))
    accepted = []
    for i in np.nonzero(mS | mD)[0]:
        i = int(i)
        if accepted and i - accepted[-1] < g:
            if sal[i] > sal[accepted[-1]]:
                accepted[-1] = i
        else:
            accepted.append(i)
    arms = {int(i): ("A" if mS[i] else "") + ("B" if mD[i] else "") for i in accepted}
    return accepted, arms


def build_candidates(cols, n, rows_accepted, arms):
    out = []
    for k, r in enumerate(rows_accepted):
        f = boundary_fields(cols, n, r)
        code = classify(f)
        out.append(OrderedDict([
            ("k", k + 1),
            ("row", r),
            ("t", round(r / FPS, 1)),
            ("timecode", timecode(r / FPS)),
            ("D", f["D"]),
            ("dip", f["dip"]),
            ("novelty", f["novelty"]),
            ("luma_max", r1(f["lmax"])),
            ("luma_min", r1(f["lmin"])),
            ("arm", arms.get(r, "")),
            ("trans_code", code),
            ("trans", TRANS_LABEL[code]),
        ]))
    return out


def worksheet(cands, duration, path):
    """Human-verification worksheet.  The verified set is always a SUBSET of the
    candidates (0 added, 0 moved, 27 deleted on the reference episode), so the
    only decision a reviewer makes per row is KEEP / DROP.  [R:scene-confirm]"""
    lines = []
    lines.append("# Boundary verification worksheet")
    lines.append("")
    lines.append("%d candidate boundaries over %.1f s (%s). " %
                 (len(cands), duration, timecode(duration)))
    lines.append("")
    lines.append("The detector is a recall-limited screen, not a decision. On the reference")
    lines.append("episode it found 100% of the true boundaries while emitting ~1.4 candidates")
    lines.append("per true boundary, so expect to DROP roughly 1 in 3. Never ADD or MOVE a row")
    lines.append("here without re-running the detector: the recovered workflow is delete-only.")
    lines.append("")
    lines.append("Mark each row K (keep) or D (drop). Then build scenes.json as the contiguous")
    lines.append("segments between the kept boundaries and re-run derive.py with --scenes.")
    lines.append("")
    lines.append("| K/D | # | time | timecode | D (hist dist) | dip (sharpness) | novelty | arm | proposed transition |")
    lines.append("|-----|---|------|----------|---------------|-----------------|---------|-----|---------------------|")
    for c in cands:
        lines.append("|     | %d | %.1f | %s | %.3f | %.3f | %.4f | %s | %s |" %
                     (c["k"], c["t"], c["timecode"], c["D"], c["dip"],
                      c["novelty"], c["arm"] or "-", c["trans"]))
    lines.append("")
    lines.append("arm A = histogram-distance peak (hard cut / angle change); "
                 "arm B = sharpness collapse (dissolve / wipe / fade). "
                 "AB = both fired.")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


# ----------------------------------------------------------------------------
# 2. scenes (verified list enriched with D / dip / trans)
# ----------------------------------------------------------------------------

def check_scene_span(scenes_in, n, strict=True):
    """A scene whose start/end lies outside the timeseries used to be CLAMPED
    silently by row_of()/build_camera(), which produced a fully populated but
    fabricated row: D/dip/trans read off the first or last sample and cum_x =
    cum_y = 0.0 from an empty window ("near-static").  Refuse instead."""
    end_t = n / FPS
    bad = []
    for s in scenes_in:
        i = s.get("i")
        st, en = float(s["s"]), float(s["e"])
        if not (math.isfinite(st) and math.isfinite(en)):
            bad.append("i=%s s/e not finite" % i)
            continue
        if en <= st:
            bad.append("i=%s e (%.1f) <= s (%.1f)" % (i, en, st))
        if st < 0 or int(round(st * FPS)) > n - 1:
            bad.append("i=%s s=%.1f outside [0, %.1f]" % (i, st, (n - 1) / FPS))
        if en < 0 or int(round(en * FPS)) > n:
            bad.append("i=%s e=%.1f outside [0, %.1f]" % (i, en, end_t))
    if bad and strict:
        sys.exit("derive.py: %d scene(s) do not lie inside the timeseries "
                 "(%d rows = %.1f s):\n  %s\nUse --allow-out-of-range-scenes to "
                 "clamp them anyway (the clamped rows are fabricated)."
                 % (len(bad), n, end_t, "\n  ".join(bad)))
    for b in bad:
        print("  WARNING: clamped out-of-range scene: %s" % b, file=sys.stderr)
    return bad


def build_scenes(cols, n, scenes_in):
    """Schema-identical to scenes_verified.json: {i, s, e, dur, trans, D, dip}.

    D and dip are recomputed from the timeseries (not copied).  trans is
    recomputed with the recovered classifier: the human pass on the reference
    episode relabelled ZERO boundaries (71/71 identical to the pre-verification
    file), so the taxonomy is machine-assigned and reproducible. [R:transitions]"""
    out = []
    for j, s in enumerate(scenes_in):
        r = row_of(s["s"], n)
        f = boundary_fields(cols, n, r)
        trans = OPEN_LABEL if j == 0 else TRANS_LABEL[classify(f)]
        out.append(OrderedDict([
            ("i", s.get("i", j + 1)),
            ("s", round(float(s["s"]), 1)),
            ("e", round(float(s["e"]), 1)),
            ("dur", round(float(s.get("dur", s["e"] - s["s"])), 1)),
            ("trans", trans),
            ("D", f["D"]),
            ("dip", f["dip"]),
        ]))
    return out


# ----------------------------------------------------------------------------
# 3. camera
# ----------------------------------------------------------------------------

def camera_label(cum_x, cum_y, zoom):
    """pan -> tilt -> zoom, joined with ' + '.  Zoom clause is dropped when zoom
    is unavailable (needs pixels).  [R:camera]"""
    parts = []
    if cum_x >= CAM_TX:
        parts.append(LBL_PAN_R)
    elif cum_x <= -CAM_TX:
        parts.append(LBL_PAN_L)
    if cum_y >= CAM_TY:
        parts.append(LBL_TILT_D)
    elif cum_y <= -CAM_TY:
        parts.append(LBL_TILT_U)
    if zoom is not None:
        if zoom < CAM_ZLO:
            parts.append(LBL_ZOUT)
        elif zoom > CAM_ZHI:
            parts.append(LBL_ZIN)
    return " + ".join(parts) if parts else LBL_STATIC


def build_camera(cols, n, scenes_in, zooms=None):
    """Per-scene trimmed cumulative pan.  0.3 s is dropped at BOTH ends of every
    scene before summing, which is what removes the cross-cut phase-correlation
    spike.  Scenes with dur <= 1.0 s are excluded (too short to survive the trim).
    [R:camera]"""
    dx, dy = cols["pan_dx"], cols["pan_dy"]
    out = []
    dropped = []
    for s in scenes_in:
        dur = float(s.get("dur", s["e"] - s["s"]))
        if dur <= CAM_MIN_DUR:
            dropped.append({"i": s.get("i"), "dur": round(dur, 1)})
            continue
        a = int(round(float(s["s"]) * FPS)) + CAM_TRIM
        b = int(round(float(s["e"]) * FPS)) - CAM_TRIM
        a = max(0, min(n, a))
        b = max(a, min(n, b))
        cx = float(np.nansum(dx[a:b]))
        cy = float(np.nansum(dy[a:b]))
        z = None if zooms is None else zooms.get(s.get("i"))
        item = OrderedDict([
            ("i", s.get("i")),
            ("s", round(float(s["s"]), 1)),
            ("dur", round(dur, 1)),
            ("cum_x", round(cx, 1)),
            ("cum_y", round(cy, 1)),
            ("zoom", None if z is None else r3(z)),
            ("label", camera_label(cx, cy, z)),
        ])
        if z is None:
            # never silently omit: say what is missing and why, in-band
            item["zoom_requires_video"] = True
            item["label_partial_no_zoom"] = True
        out.append(item)
    return out, dropped


# ----------------------------------------------------------------------------
# 4. metrics
# ----------------------------------------------------------------------------

def window_rows(t0, t1, n):
    """Half-open row window for a time span, clipped to the array. [R:perminute]"""
    a = max(0, int(round(t0 * FPS)))
    b = min(n, int(round(t1 * FPS)))
    return a, max(a, b)


def window_db(cols, a, b):
    """PLAIN ARITHMETIC MEAN OF dBFS (a mean of log values, not energy-correct).
    This is what the artifact did: 20/20 minutes and 12/12 chapters exact, while
    median / energy-mean / power-mean all score 0.  No silence gating.
    [R:perminute][R:chapters][R:audio-silence]"""
    seg = cols["audio_rms_dbfs"][a:b]
    seg = seg[~np.isnan(seg)]
    if not len(seg):
        return None
    return r1(seg.mean())


def wmean1(series, a, b):
    """Mean of a per-frame series over a half-open row window, rounded to 1 dp.
    Returns None when the window holds no finite sample (e.g. an rgb array
    shorter than the timeseries) rather than emitting NaN into JSON."""
    if series is None:
        return None
    seg = np.asarray(series[a:b], dtype=float)
    seg = seg[~np.isnan(seg)]
    if not len(seg):
        return None
    return r1(seg.mean())


def build_per_minute(cols, n, frame_v=None, frame_warm=None):
    """Half-open row window [m*600, min((m+1)*600, n)) per minute.  The final
    window is simply truncated: no special case, no padding.  [R:perminute]"""
    n_min = int(math.ceil(n / float(MINUTE_ROWS)))
    out = []
    for m in range(n_min):
        a = m * MINUTE_ROWS
        b = min((m + 1) * MINUTE_ROWS, n)
        row = OrderedDict([
            ("m", m),
            ("warm", wmean1(frame_warm, a, b)),
            ("V", wmean1(frame_v, a, b)),
            ("db", window_db(cols, a, b)),
        ])
        row["rows"] = b - a
        row["fill"] = r1(100.0 * (b - a) / MINUTE_ROWS)   # flag partial final window
        if frame_warm is None:
            row["warm_requires_video"] = True
        if frame_v is None:
            row["V_requires_video"] = True
        out.append(row)
    return out


def build_chapter_rows(cols, n, chapters, scenes_in, frame_v=None, frame_warm=None):
    """chapterRows = chapters expanded with t1 = next t0, last t1 = the runtime
    rounded UP to a whole second.  scenes = count of scene STARTS in [t0, t1)
    (start-based, never overlap-based: that is what makes the counts sum to the
    total).  per_min = scenes / minutes, 1 dp, banker's rounding. [R:chapters]"""
    end = int(math.ceil(n / FPS))
    rows = []
    for k, (t0, name) in enumerate(chapters):
        t1 = chapters[k + 1][0] if k + 1 < len(chapters) else end
        a, b = window_rows(t0, t1, n)
        cnt = sum(1 for s in scenes_in if t0 <= float(s["s"]) < t1)
        minutes = (t1 - t0) / 60.0
        row = OrderedDict([
            ("t0", int(t0) if float(t0).is_integer() else t0),
            ("t1", int(t1) if float(t1).is_integer() else t1),
            ("name", name),
            ("scenes", cnt),
            ("per_min", r1(cnt / minutes) if minutes > 0 else None),
            ("warm", wmean1(frame_warm, a, b)),
            ("V", wmean1(frame_v, a, b)),
            ("db", window_db(cols, a, b)),
        ])
        if frame_warm is None:
            row["warm_requires_video"] = True
        if frame_v is None:
            row["V_requires_video"] = True
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------
# 5. transitions
# ----------------------------------------------------------------------------

def build_transitions(scenes, candidates):
    """Type counts with EVERY denominator spelled out.  The reference report
    quotes 25.4% (B+C+D over 71 interior boundaries) in one section and 19.7%
    (B+C over 71) in another without saying they are different definitions;
    both are emitted here, each with its own numerator and denominator."""
    interior = [s for s in scenes if s["trans"] != OPEN_LABEL]
    n_int = len(interior)
    n_sc = len(scenes)
    cnt = Counter(s["trans"][0] for s in interior)

    def pct(num, den):
        return None if not den else r1(100.0 * num / den)

    by_type = []
    for code, label in TRANS_LABEL.items():
        c = cnt.get(code, 0)
        by_type.append(OrderedDict([
            ("code", code), ("label", label), ("n", c),
            ("pct_of_interior_boundaries", pct(c, n_int)),
            ("pct_of_scenes", pct(c, n_sc)),
        ]))

    bcd = sum(cnt.get(x, 0) for x in "BCD")
    bc = sum(cnt.get(x, 0) for x in "BC")
    agg = [
        OrderedDict([("name", "pass-through, wide (B+C+D: whiteout+blackout+occluder wipe)"),
                     ("n", bcd), ("den_interior_boundaries", n_int),
                     ("pct", pct(bcd, n_int)), ("den_scenes", n_sc),
                     ("pct_of_scenes", pct(bcd, n_sc))]),
        OrderedDict([("name", "pass-through, narrow (B+C: whiteout+blackout only)"),
                     ("n", bc), ("den_interior_boundaries", n_int),
                     ("pct", pct(bc, n_int)), ("den_scenes", n_sc),
                     ("pct_of_scenes", pct(bc, n_sc))]),
    ]

    cand_cnt = Counter(c["trans_code"] for c in candidates) if candidates else Counter()
    return OrderedDict([
        ("denominators", OrderedDict([
            ("scenes", n_sc),
            ("interior_boundaries", n_int),
            ("note", "interior_boundaries = scenes - 1 (the first scene's 'OPEN' is the "
                     "video start, not a transition). Percentages in the reference report "
                     "use interior_boundaries; both are given here."),
            ("candidate_shots", len(candidates) + 1 if candidates else 0),
            ("candidate_boundaries", len(candidates)),
        ])),
        ("by_type", by_type),
        ("aggregates", agg),
        ("candidates_by_type", OrderedDict(
            (TRANS_LABEL[c], cand_cnt.get(c, 0)) for c in TRANS_LABEL)),
        ("classifier", OrderedDict([
            ("features", "lmax/lmin = max/min(luma_mean[r-6:r+6]); "
                         "dip = min(sharpness_dip_ratio[r-6:r+6]); r = round(t*10)"),
            ("rules", ["lmax >= %g -> B" % THR_LMAX_B,
                       "lmin <= %g -> C" % THR_LMIN_C,
                       "dip < %g -> D" % THR_DIP_D,
                       "dip < %g -> E" % THR_DIP_E,
                       "dip < %g -> F" % THR_DIP_F,
                       "else -> A"]),
            ("note", "straddle_distance (the published 'D' column) is NOT used: it is "
                     "descriptively true of hard cuts but carries no discriminative "
                     "information given dip."),
        ])),
    ])


# ----------------------------------------------------------------------------
# 6. silences
# ----------------------------------------------------------------------------

def build_silences(cues, cols, n, thr=SILENCE_MAIN, scenes=None):
    """A '침묵' in the reference report is a NARRATION GAP in the caption file,
    not an audio silence: no audio threshold reproduces the published set, while
    'gap between consecutive cues >= 3.0 s' reproduces 14/14 timecodes and 7/7
    durations.  Audio statistics are attached to each event so the distinction
    stays visible - on the reference episode the music bed plays through every
    one of them.  [R:audio-silence]"""
    cues = sorted([[float(c[0]), float(c[1]), c[2]] for c in cues], key=lambda c: c[0])
    gaps = []
    for i in range(len(cues) - 1):
        gaps.append((cues[i][1], cues[i + 1][0], cues[i + 1][0] - cues[i][1], i))

    # scene-boundary alignment, so the report can state whether the silences
    # actually land on cuts instead of asserting it.  On the reference episode
    # only 5 of 14 contain a boundary, against a prose claim of "all of them".
    bnds = sorted(float(x["s"]) for x in scenes[1:]) if scenes else []

    db = cols["audio_rms_dbfs"]
    events = []
    for (t_in, t_out, g, i) in gaps:
        if g < thr:
            continue
        a, b = window_rows(t_in, t_out, n)
        seg = db[a:b]
        seg = seg[~np.isnan(seg)]
        events.append(OrderedDict([
            ("timecode", timecode(t_in)),
            ("t_in", round(t_in, 3)),
            ("t_out", round(t_out, 3)),
            ("dur", r1(g)),
            ("prev_cue_index", i),
            ("prev_cue", cues[i][2]),
            ("next_cue", cues[i + 1][2]),
            ("audio_mean_dbfs", r1(seg.mean()) if len(seg) else None),
            ("audio_min_dbfs", r1(seg.min()) if len(seg) else None),
            ("contains_scene_boundary",
             any(t_in <= b <= t_out for b in bnds) if bnds else None),
            ("dist_to_nearest_boundary_s",
             round(min(abs(b - t_in) for b in bnds), 2) if bnds else None),
        ]))

    allg = np.array([g[2] for g in gaps]) if gaps else np.zeros(0)
    counts = OrderedDict()
    for t in sorted(set([thr] + list(SILENCE_ALSO))):
        counts["gaps_ge_%.1fs" % t] = int((allg >= t).sum())
    pauses = allg[allg > PAUSE_MIN]
    return OrderedDict([
        ("rule", "silence := gap between consecutive transcript cues, "
                 "gap = next.start - prev.end, threshold >= %.1f s" % thr),
        ("threshold_s", thr),
        ("caution", "This is a narration gap, NOT audio silence. On the reference "
                    "episode audio_rms_dbfs during these events sits only 1.5-11.5 dB "
                    "below the whole-video mean; music and ambience play through. "
                    "The count is also threshold-fragile: +/-0.05 s moves it by ~2."),
        ("counts", counts),
        ("pauses", OrderedDict([
            ("min_gap_s", PAUSE_MIN),
            ("n", int(len(pauses))),
            ("median_s", round(float(np.median(pauses)), 4) if len(pauses) else None),
        ])),
        ("n_events", len(events)),
        ("n_events_containing_a_scene_boundary",
         sum(1 for e in events if e["contains_scene_boundary"]) if bnds else None),
        ("events", events),
    ])


def narration_stats(cues, cols, n):
    """Narration coverage / ducking / centroid.  Two masks are reported because
    the published pair (1248 Hz narration, 873 Hz music) is not producible by any
    single mask - the two published numbers came from different masks.
    [R:audio-silence]"""
    t = cols["t_sec"]
    db = cols["audio_rms_dbfs"]
    cen = cols.get("spectral_centroid_hz")
    out = OrderedDict()
    for name, off in (("sample_time", 0.0), ("center_of_frame", 0.5 / FPS)):
        mask = np.zeros(n, dtype=bool)
        tt = t + off
        for c in cues:
            mask |= (tt >= float(c[0])) & (tt < float(c[1]))
        nar, non = db[mask], db[~mask]
        row = OrderedDict([
            ("coverage_pct", r1(100.0 * mask.sum() / n)),
            ("duck_median_db", r1(np.nanmedian(nar) - np.nanmedian(non))),
            ("duck_mean_db", r1(np.nanmean(nar) - np.nanmean(non))),
        ])
        if cen is not None:
            row["centroid_narration_hz"] = r1(np.nanmean(cen[mask]))
            row["centroid_non_narration_hz"] = r1(np.nanmean(cen[~mask]))
        out[name] = row
    d = cols["audio_rms_dbfs"]
    out["rms_dbfs"] = OrderedDict([
        ("median", r1(np.nanmedian(d))),
        ("p5", r1(np.nanpercentile(d, 5))),
        ("p95", r1(np.nanpercentile(d, 95))),
    ])
    out["note"] = ("Ducking is reported as BOTH median and mean because the reference "
                   "report's '+5.6 dB' is the median difference while the mean is +7.3 dB, "
                   "and the report does not say which it used.")
    return out


# ----------------------------------------------------------------------------
# 7. optional pixel pass (only when --rgb is supplied)
# ----------------------------------------------------------------------------

def pixel_series(rgb_path, n, warm_params):
    """Per-frame V and warm from a stage-1 rgb array of shape (frames, H, W, 3),
    uint8, sampled at the same 10 fps as the timeseries (pipeline/rgb.py output).

    V    = mean over pixels of max(R,G,B), rescaled to 0-100 (i.e. /2.55).
           The reduction is recovered; the identification of max(R,G,B) rests on
           a palette cross-check, not on an exact reproduction. [R:perminute]
    warm = 100 * fraction of pixels satisfying a hue/S/V predicate.
           THE PREDICATE IS NOT RECOVERABLE from the published artifact: no hue
           range, saturation floor or value floor is stated anywhere, and the
           reference palette has an empty hue gap from 51 to 173 deg in which
           every boundary is observationally equivalent.  The defaults below are
           the palette-consistent choice (H < 90 or H >= 330), NOT the original.
           Whatever you pick, it is stamped into derive_manifest.json - a warm
           number computed with different parameters is not comparable.
    """
    arr = np.load(rgb_path, mmap_mode="r")
    if arr.ndim != 4 or arr.shape[-1] != 3:
        sys.exit("derive.py: --rgb must be (frames, H, W, 3); got %r" % (arr.shape,))
    f = min(len(arr), n)
    v_series = np.full(n, np.nan)
    w_series = np.full(n, np.nan)
    hlo, hhi, smin, vmin = (warm_params["hue_lo"], warm_params["hue_hi"],
                            warm_params["s_min"], warm_params["v_min"])
    for i in range(f):
        px = np.asarray(arr[i], dtype=np.float32)
        mx = px.max(axis=-1)
        mn = px.min(axis=-1)
        v_series[i] = mx.mean() / 2.55                      # 0-100
        # HSV hue in degrees, saturation and value as 0-100
        d = mx - mn
        safe = np.where(d == 0, 1.0, d)
        r, g, b = px[..., 0], px[..., 1], px[..., 2]
        h = np.zeros_like(mx)
        m_r = (mx == r) & (d > 0)
        m_g = (mx == g) & (d > 0) & ~m_r
        m_b = (mx == b) & (d > 0) & ~m_r & ~m_g
        h[m_r] = (60 * ((g - b) / safe))[m_r]
        h[m_g] = (60 * ((b - r) / safe) + 120)[m_g]
        h[m_b] = (60 * ((r - g) / safe) + 240)[m_b]
        h = np.mod(h, 360.0)
        s = np.where(mx > 0, d / np.where(mx == 0, 1.0, mx), 0.0) * 100.0
        v = mx / 2.55
        if hlo <= hhi:
            hue_ok = (h >= hlo) & (h < hhi)
        else:                                   # wrap-around band, e.g. 330..90
            hue_ok = (h >= hlo) | (h < hhi)
        warm_mask = hue_ok & (s >= smin) & (v >= vmin)
        w_series[i] = 100.0 * warm_mask.mean()
    return v_series, w_series, f


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def _scan_nonfinite(obj, where=""):
    """Collect JSON paths holding NaN/Inf.  json.dump would emit the bare tokens
    NaN / Infinity, which are NOT valid JSON: jq, JSON.parse, Go and serde all
    reject them.  Emitting an unparseable file is worse than failing."""
    bad = []
    if isinstance(obj, float):
        if not math.isfinite(obj):
            bad.append((where or "<root>", obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            bad += _scan_nonfinite(v, "%s.%s" % (where, k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad += _scan_nonfinite(v, "%s[%d]" % (where, i))
    return bad


def dump(obj, path):
    bad = _scan_nonfinite(obj)
    if bad:
        sys.exit("derive.py: refusing to write %s - %d non-finite value(s) would "
                 "become invalid JSON. First offenders: %s.  This means the input "
                 "timeseries has a hole where a value was needed; fix the input "
                 "rather than the output."
                 % (path, len(bad), ", ".join("%s=%r" % b for b in bad[:5])))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Stage 2: 10 fps timeseries -> candidates / scenes / camera / "
                    "metrics / transitions / silences.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ts", required=True, help="timeseries_10fps.csv from stage 1")
    ap.add_argument("--scenes", help="human-verified scene list (JSON). Without it, "
                                     "only candidates + a worksheet are emitted.")
    ap.add_argument("--chapters", help="editorial chapter list: [[t0,name],...]. "
                                       "Chapters are authored, not measurable.")
    ap.add_argument("--transcript", help="caption cues [[start,end,text],...] for silences")
    ap.add_argument("--rgb", help="stage-1 rgb.npy (frames,H,W,3) @10fps; enables V and warm")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--min-silence", type=float, default=SILENCE_MAIN)
    ap.add_argument("--det-ts", type=float, default=DET_TS)
    ap.add_argument("--det-td", type=float, default=DET_TD)
    ap.add_argument("--det-gap", type=int, default=DET_G)
    ap.add_argument("--warm-hue-lo", type=float, default=330.0)
    ap.add_argument("--warm-hue-hi", type=float, default=90.0)
    ap.add_argument("--warm-s-min", type=float, default=0.0)
    ap.add_argument("--warm-v-min", type=float, default=0.0)
    ap.add_argument("--allow-nonuniform-grid", action="store_true",
                    help="proceed even if t_sec is not a uniform 0.1 s grid "
                         "(every recovered formula indexes by round(t*10), so the "
                         "output will be wrong; off by default)")
    ap.add_argument("--allow-out-of-range-scenes", action="store_true",
                    help="clamp scenes that fall outside the timeseries instead of "
                         "refusing. The clamped rows are fabricated; off by default")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    cols, n, nonuniform = load_timeseries(a.ts)
    if nonuniform:
        msg = ("t_sec is not a uniform %.1f s grid. Every recovered formula indexes "
               "rows as round(t*%g), so D / dip / camera / metrics would all be "
               "silently misaligned." % (1.0 / FPS, FPS))
        if not a.allow_nonuniform_grid:
            sys.exit("derive.py: " + msg + " Re-sample the timeseries, or pass "
                     "--allow-nonuniform-grid to override.")
        print("  WARNING: " + msg, file=sys.stderr)
    duration = float(cols["t_sec"][-1])
    end_sec = int(math.ceil(n / FPS))
    print("timeseries: %d rows, t = %.1f .. %.1f s (runtime rounded up: %d s)"
          % (n, cols["t_sec"][0], duration, end_sec))

    requires_video = []
    requires_human = []

    # ---- 1. candidates -----------------------------------------------------
    rows_acc, arms = detect_candidates(cols, n, ts=a.det_ts, td=a.det_td, g=a.det_gap)
    cands = build_candidates(cols, n, rows_acc, arms)
    dump(OrderedDict([
        ("detector", OrderedDict([
            ("arm_A", "straddle_distance local max (radius %d) >= %g" % (DET_RS, a.det_ts)),
            ("arm_B", "sharpness_dip_ratio local min (radius %d) <= %g" % (DET_RD, a.det_td)),
            ("min_gap_rows", a.det_gap),
            ("note", "recall-limited screen: at these settings it recovered 97/98 "
                     "published candidate rows and 71/71 human-verified boundaries "
                     "on the reference episode while emitting ~7% surplus. 97/98 is "
                     "this operating point, not a ceiling - rD=7 gives 98/98 for one "
                     "extra candidate. Expect to drop ~1 in 3."),
        ])),
        ("n_candidates", len(cands)),
        ("candidates", cands),
    ]), os.path.join(a.out, "candidates.json"))
    worksheet(cands, duration, os.path.join(a.out, "worksheet.md"))
    print("candidates.json : %d boundary candidates -> %d proposed shots"
          % (len(cands), len(cands) + 1))

    # ---- optional pixel pass ----------------------------------------------
    frame_v = frame_warm = None
    warm_params = {"hue_lo": a.warm_hue_lo, "hue_hi": a.warm_hue_hi,
                   "s_min": a.warm_s_min, "v_min": a.warm_v_min}
    if a.rgb:
        frame_v, frame_warm, nf = pixel_series(a.rgb, n, warm_params)
        print("rgb pass       : %d frames -> per-frame V and warm" % nf)
    else:
        requires_video += ["metrics.perMinute[].V", "metrics.perMinute[].warm",
                           "metrics.chapterRows[].V", "metrics.chapterRows[].warm",
                           "metrics.palette"]

    if not a.scenes:
        manifest = OrderedDict([
            ("mode", "candidates-only"),
            ("inputs", {"ts": os.path.abspath(a.ts)}),
            ("outputs", ["candidates.json", "worksheet.md"]),
            ("requires_human", ["scenes.json: verify the candidate boundaries "
                                "(delete-only) and re-run with --scenes"]),
            ("requires_video", requires_video),
        ])
        dump(manifest, os.path.join(a.out, "derive_manifest.json"))
        print("no --scenes: stopped after the candidate pass. "
              "Verify worksheet.md, then re-run with --scenes.")
        return 0

    # ---- 2. scenes ---------------------------------------------------------
    scenes_in = load_scenes(a.scenes)
    oor = check_scene_span(scenes_in, n, strict=not a.allow_out_of_range_scenes)
    scenes = build_scenes(cols, n, scenes_in)
    dump(scenes, os.path.join(a.out, "scenes.json"))
    print("scenes.json    : %d scenes (D, dip, trans recomputed from the timeseries)"
          % len(scenes))

    # ---- 3. camera ---------------------------------------------------------
    cam, cam_dropped = build_camera(cols, n, scenes_in, zooms=None)
    requires_video.append("camera[].zoom")
    dump(cam, os.path.join(a.out, "camera.json"))
    print("camera.json    : %d of %d scenes measured (dropped dur <= %.1f s: %s)"
          % (len(cam), len(scenes_in), CAM_MIN_DUR,
             ", ".join("i=%s(%.1fs)" % (d["i"], d["dur"]) for d in cam_dropped) or "none"))

    # ---- 4. metrics --------------------------------------------------------
    per_min = build_per_minute(cols, n, frame_v, frame_warm)
    chapters = load_chapters(a.chapters) if a.chapters else []
    if not chapters:
        requires_human.append("metrics.chapters: chapter boundaries and names are "
                              "editorial; no formula recovers them from the timeseries")
    chap_rows = build_chapter_rows(cols, n, chapters, scenes_in, frame_v, frame_warm) \
        if chapters else []
    metrics = OrderedDict([
        ("perMinute", per_min),
        ("scenes", [OrderedDict([(k, s[k]) for k in ("i", "s", "e", "dur", "trans")])
                    for s in scenes]),
        ("palette", None),
        ("camera", cam),
        ("chapters", [[c[0], c[1]] for c in chapters]),
        ("chapterRows", chap_rows),
    ])
    if frame_warm is None:
        metrics["palette_requires_video"] = True
    dump(metrics, os.path.join(a.out, "metrics.json"))
    print("metrics.json   : %d minute rows, %d chapter rows" % (len(per_min), len(chap_rows)))

    # ---- 5. transitions ----------------------------------------------------
    trans = build_transitions(scenes, cands)
    dump(trans, os.path.join(a.out, "transitions.json"))
    print("transitions.json: %d interior boundaries"
          % trans["denominators"]["interior_boundaries"])

    # ---- 6. silences -------------------------------------------------------
    if a.transcript:
        cues = json.load(open(a.transcript, encoding="utf-8"))
        sil = build_silences(cues, cols, n, thr=a.min_silence, scenes=scenes)
        sil["narration"] = narration_stats(cues, cols, n)
        dump(sil, os.path.join(a.out, "silences.json"))
        print("silences.json  : %d events >= %.1f s" % (sil["n_events"], a.min_silence))
    else:
        print("silences.json  : skipped (no --transcript)")

    # ---- manifest ----------------------------------------------------------
    manifest = OrderedDict([
        ("mode", "full"),
        ("inputs", OrderedDict([
            ("ts", os.path.abspath(a.ts)),
            ("scenes", os.path.abspath(a.scenes)),
            ("chapters", os.path.abspath(a.chapters) if a.chapters else None),
            ("transcript", os.path.abspath(a.transcript) if a.transcript else None),
            ("rgb", os.path.abspath(a.rgb) if a.rgb else None),
        ])),
        ("runtime_s", duration),
        ("rows", n),
        ("input_warnings", OrderedDict([
            ("nonuniform_t_grid", bool(nonuniform)),
            ("out_of_range_scenes", oor),
        ])),
        ("requires_video", sorted(set(requires_video))),
        ("requires_human", requires_human),
        ("parameters", OrderedDict([
            ("detector", {"TS": a.det_ts, "rS": DET_RS, "TD": a.det_td, "rD": DET_RD,
                          "min_gap_rows": a.det_gap}),
            ("dip_window_rows", list(DIP_WIN)),
            ("transition_thresholds", {"lmax_B": THR_LMAX_B, "lmin_C": THR_LMIN_C,
                                       "dip_D": THR_DIP_D, "dip_E": THR_DIP_E,
                                       "dip_F": THR_DIP_F}),
            ("camera", {"trim_rows": CAM_TRIM, "min_dur_s": CAM_MIN_DUR,
                        "Tx_px": CAM_TX, "Ty_px": CAM_TY,
                        "proxy_width_px": CAM_PROXY_W,
                        "zoom_lo": CAM_ZLO, "zoom_hi": CAM_ZHI}),
            ("warm_predicate", dict(warm_params, **{
                "STATUS": "NOT RECOVERED - these are defaults, not the original "
                          "thresholds. Any warm figure computed with them is not "
                          "comparable to the reference episode's published curve."})),
            ("silence_threshold_s", a.min_silence),
        ])),
        ("notes", [
            "db is a plain arithmetic mean of dBFS (a mean of logs). That is what "
            "the reference artifact did (32/32 windows exact); it is NOT an "
            "energy-correct loudness and it is dominated by quiet passages.",
            "The final minute window is truncated, not padded: check perMinute[-1].fill.",
            "camera[].label omits its zoom clause whenever zoom is null.",
        ]),
    ])
    dump(manifest, os.path.join(a.out, "derive_manifest.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
