#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_episodes.py — fixture builder for the compare.py tests.

There is exactly ONE real episode in this container (the published Inca
teardown), and the source video is not available.  So the fixtures are:

  episodes/inca         a verbatim copy of the stage-2 derive.py output for the
                        Inca episode, plus a stage-1 meta.json reconstructed
                        from published facts.  warm / V / palette / zoom are
                        null here because no pixel pass was run — that is the
                        real, unenriched state of the pipeline output.

  episodes/inca_pix     the same dir with perMinute[].warm / .V,
                        chapterRows[].warm / .V and palette.json filled in from
                        the PUBLISHED data/metrics.json and data/palette.json.
                        This stands in for "derive.py was run with --rgb": the
                        values are the reference episode's own published ones,
                        not invented, and the substitution is recorded in the
                        dir's provenance.json.  Its only purpose is to let the
                        colour/palette rows of compare.py actually be exercised
                        instead of reading INSUFFICIENT.

  episodes/synth_perturbed
                        a SYNTHETIC second episode derived from inca_pix by a
                        list of named, deliberate perturbations (below).  It is
                        not a real video and is labelled as such.

  episodes/inca_pix_otherwarm
                        inca_pix with a different warm predicate stamped in the
                        manifest and nothing else changed — used to check that
                        compare.py REFUSES to compare warm figures computed
                        with different, unrecovered thresholds.

PERTURBATIONS APPLIED TO synth_perturbed (each one targets a specific row):
  1. uniform time-stretch x1.4       -> scene durations, runtime, all timestamps
  2. warm curve rolled by 0.5 in tau -> warm_curve shape destroyed
  3. transition codes A <-> E swapped-> transition mix inverted
  4. camera cum_x / cum_y x3.0       -> displacement rate changed
  5. palette hues rotated +120 deg   -> palette hue distance changed
  6. per-minute db shifted +8 dB     -> loudness level changed
Everything else is carried through unchanged, so the invariance checks
(normalized silence positions, dimensionless duration shape, the V curve) have
something to be invariant about.
"""
import json
import math
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
REF = "/home/user/ai-video-style-teardown"
SRC = os.path.join(WORK, "out", "run_full")
EPI = os.path.join(WORK, "episodes")

STRETCH = 1.4
WARM_ROLL_TAU = 0.5
CAM_SCALE = 3.0
HUE_ROTATE = 120.0
DB_SHIFT = 8.0


def jload(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def jdump(o, p):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(o, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


# ---------------------------------------------------------------------------
# 1. episodes/inca — stage-2 output + a reconstructed stage-1 meta.json
# ---------------------------------------------------------------------------
def build_inca():
    d = os.path.join(EPI, "inca")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(SRC, d)
    jdump({
        "video": {
            "path": "(not available in this container)",
            "id": "inca",
            "title": "Deep Horizon / 딥 호라이즌 — Inca",
            "duration_sec": 1168.883333,
            "fps": 60.0,
            "width": 1280, "height": 720,
            "has_audio": True,
            "PROVENANCE": "stage-1 meta.json reconstructed from the published "
                          "artifact (data/raw/*.txt frame count and pts_time span, "
                          "and the report's stated 1280x720/60fps). The source "
                          "video is NOT available here, so measure.py was not run "
                          "on it; only the fields compare.py reads are filled in.",
        },
        "timeseries": {"sample_rate_fps": 10, "rows": 11689,
                       "t_first": 0.0, "t_last": 1168.8},
    }, os.path.join(d, "meta.json"))
    # stage the stage-1 CSV into the episode dir. measure.py writes it there
    # anyway; compare.py uses it only for the full-resolution luma curve.
    os.makedirs(os.path.join(d, "timeseries"), exist_ok=True)
    shutil.copy(os.path.join(REF, "data", "timeseries", "timeseries_10fps.csv"),
                os.path.join(d, "timeseries", "timeseries_10fps.csv"))
    return d


def stretch_timeseries(src_csv, dst_csv, factor):
    """Resample a 10 fps timeseries onto a time-stretched grid.

    Every column is linearly interpolated at the new sample times; t_sec is
    regenerated. Used only to give the synthetic episode a well-formed stage-1
    CSV so the luma row has something to read. Because the stretch is uniform,
    the NORMALIZED-time luma curve is unchanged by construction — that makes
    luma_curve an invariance check rather than a perturbation target.
    """
    import csv as _csv
    with open(src_csv, encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    names = list(rows[0].keys())
    n0 = len(rows)
    def f2(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("nan")
    cols = {k: [f2(r[k]) for r in rows] for k in names}
    n1 = int(round(n0 * factor))
    with open(dst_csv, "w", encoding="utf-8", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(names)
        for i in range(n1):
            x = i * (n0 - 1) / float(n1 - 1)
            lo = int(x)
            hi = min(lo + 1, n0 - 1)
            f = x - lo
            out = []
            for k in names:
                v = cols[k][lo] + f * (cols[k][hi] - cols[k][lo])
                out.append(round(i / 10.0, 1) if k == "t_sec"
                           else ("" if not math.isfinite(v) else round(v, 4)))
            w.writerow(out)
    return n1


# ---------------------------------------------------------------------------
# 2. episodes/inca_pix — the same, with the published warm/V/palette dropped in
# ---------------------------------------------------------------------------
def build_inca_pix():
    d = os.path.join(EPI, "inca_pix")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(os.path.join(EPI, "inca"), d)

    pub = jload(os.path.join(REF, "data", "metrics.json"))
    m = jload(os.path.join(d, "metrics.json"))
    for row, pr in zip(m["perMinute"], pub["perMinute"]):
        assert row["m"] == pr["m"]
        row["warm"], row["V"] = pr["warm"], pr["V"]
        row.pop("warm_requires_video", None)
        row.pop("V_requires_video", None)
    for row, pr in zip(m["chapterRows"], pub["chapterRows"]):
        assert row["t0"] == pr["t0"]
        row["warm"], row["V"] = pr["warm"], pr["V"]
        row.pop("warm_requires_video", None)
        row.pop("V_requires_video", None)
    m.pop("palette_requires_video", None)
    m["palette"] = jload(os.path.join(REF, "data", "palette.json"))
    jdump(m, os.path.join(d, "metrics.json"))
    shutil.copy(os.path.join(REF, "data", "palette.json"),
                os.path.join(d, "palette.json"))

    mt = jload(os.path.join(d, "meta.json"))
    mt["video"]["id"] = "inca_pix"
    jdump(mt, os.path.join(d, "meta.json"))

    man = jload(os.path.join(d, "derive_manifest.json"))
    man["requires_video"] = [x for x in man.get("requires_video", [])
                             if "palette" not in x and ".V" not in x and "warm" not in x]
    jdump(man, os.path.join(d, "derive_manifest.json"))

    jdump({
        "what": "episodes/inca with the pixel-derived columns filled in",
        "why": "derive.py emits warm / V / palette as null without --rgb, and the "
               "source video is unavailable in this container. Without this the "
               "colour and palette rows of compare.py would all read INSUFFICIENT "
               "and the tests could not exercise them.",
        "source_of_the_filled_values": os.path.join(REF, "data/metrics.json") +
                                       " and data/palette.json (the PUBLISHED "
                                       "reference numbers, copied verbatim, not "
                                       "recomputed and not invented)",
        "fields_filled": ["metrics.perMinute[].warm", "metrics.perMinute[].V",
                          "metrics.chapterRows[].warm", "metrics.chapterRows[].V",
                          "metrics.palette", "palette.json"],
        "still_null": ["camera[].zoom (never published for the reference episode)"],
        "warning": "the warm predicate that produced the published warm values is "
                   "NOT recoverable; the manifest's warm_predicate block is "
                   "derive.py's stated default, not the original.",
    }, os.path.join(d, "provenance.json"))
    return d


# ---------------------------------------------------------------------------
# 3. episodes/synth_perturbed — the deliberate falsification target
# ---------------------------------------------------------------------------
def build_perturbed():
    src = os.path.join(EPI, "inca_pix")
    d = os.path.join(EPI, "synth_perturbed")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(src, d)

    rt0 = 1168.9              # covered span of the source timeseries
    rt1 = rt0 * STRETCH

    # -- meta / manifest ---------------------------------------------------
    meta = jload(os.path.join(d, "meta.json"))
    meta["video"].update(id="synth_perturbed",
                         title="SYNTHETIC perturbed episode (not a real video)",
                         duration_sec=round(rt1, 6))
    meta["video"]["PROVENANCE"] = (
        "SYNTHETIC. Built by tests/make_episodes.py from episodes/inca_pix by a "
        "fixed list of perturbations. There is no video behind this dir.")
    meta["timeseries"].update(rows=int(round(rt1 * 10)),
                              t_last=round(rt1 - 0.1, 6))
    jdump(meta, os.path.join(d, "meta.json"))

    man = jload(os.path.join(d, "derive_manifest.json"))
    man["runtime_s"] = round(rt1 - 0.1, 6)
    man["rows"] = int(round(rt1 * 10))
    man["SYNTHETIC"] = {
        "built_from": src,
        "perturbations": {
            "time_stretch": STRETCH,
            "warm_curve_roll_tau": WARM_ROLL_TAU,
            "transition_codes_swapped": ["A", "E"],
            "camera_displacement_scale": CAM_SCALE,
            "palette_hue_rotation_deg": HUE_ROTATE,
            "db_shift_db": DB_SHIFT,
        },
    }
    jdump(man, os.path.join(d, "derive_manifest.json"))

    # -- scenes: stretch times, swap transition codes A <-> E ---------------
    swap = {"A": "E", "E": "A"}
    labels = {}                       # code -> the label string used for it
    scenes = jload(os.path.join(d, "scenes.json"))
    for s in scenes:
        for k in ("s", "e", "dur"):
            s[k] = round(float(s[k]) * STRETCH, 4)
        t = s.get("trans")
        if t and t != "OPEN":
            labels.setdefault(t[:1], t)
    for s in scenes:
        t = s.get("trans")
        if t and t != "OPEN" and t[:1] in swap:
            new = swap[t[:1]]
            s["trans"] = labels.get(new, new + t[1:])
    jdump(scenes, os.path.join(d, "scenes.json"))

    # -- transitions.json rebuilt from the perturbed scenes ------------------
    tr = jload(os.path.join(d, "transitions.json"))
    counts = {}
    for s in scenes:
        t = s.get("trans")
        if t and t != "OPEN":
            counts[t[:1]] = counts.get(t[:1], 0) + 1
    den = sum(counts.values())
    tr["denominators"]["scenes"] = len(scenes)
    tr["denominators"]["interior_boundaries"] = den
    for row in tr["by_type"]:
        row["n"] = counts.get(row["code"], 0)
        row["pct_of_interior_boundaries"] = round(100.0 * row["n"] / den, 1)
        row["pct_of_scenes"] = round(100.0 * row["n"] / len(scenes), 1)
    jdump(tr, os.path.join(d, "transitions.json"))

    # -- camera: stretch times, scale displacement --------------------------
    cam = jload(os.path.join(d, "camera.json"))
    for c in cam:
        c["s"] = round(float(c["s"]) * STRETCH, 4)
        c["dur"] = round(float(c["dur"]) * STRETCH, 4)
        c["cum_x"] = round(float(c["cum_x"]) * CAM_SCALE, 4)
        c["cum_y"] = round(float(c["cum_y"]) * CAM_SCALE, 4)
    jdump(cam, os.path.join(d, "camera.json"))

    # -- metrics: rebuild the per-minute grid on the stretched runtime ------
    m = jload(os.path.join(d, "metrics.json"))
    old_pm = m["perMinute"]

    def old_curve(key):
        """(tau_center, value) of the source per-minute series."""
        out = []
        for r in old_pm:
            if r.get(key) is None:
                continue
            t0 = r["m"] * 60.0
            t1 = t0 + (r.get("rows", 600) / 10.0)
            out.append(((t0 + t1) / 2.0 / rt0, float(r[key])))
        return out

    def sample(curve, tau):
        """Linear interpolation of a (tau, value) curve, ends held flat."""
        if not curve:
            return None
        xs = [c[0] for c in curve]
        ys = [c[1] for c in curve]
        if tau <= xs[0]:
            return ys[0]
        if tau >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if tau <= xs[i]:
                f = (tau - xs[i - 1]) / (xs[i] - xs[i - 1])
                return ys[i - 1] + f * (ys[i] - ys[i - 1])
        return ys[-1]

    cur = {k: old_curve(k) for k in ("warm", "V", "db")}
    n_rows = int(round(rt1 * 10))
    n_min = int(math.ceil(n_rows / 600.0))
    new_pm = []
    for mm in range(n_min):
        a, b = mm * 600, min((mm + 1) * 600, n_rows)
        tau = ((a + b) / 2.0) / float(n_rows)
        warm = sample(cur["warm"], (tau + WARM_ROLL_TAU) % 1.0)   # <- rolled
        v = sample(cur["V"], tau)                                 # <- untouched
        db = sample(cur["db"], tau)
        new_pm.append({"m": mm,
                       "warm": None if warm is None else round(warm, 1),
                       "V": None if v is None else round(v, 1),
                       "db": None if db is None else round(db + DB_SHIFT, 1),
                       "rows": b - a,
                       "fill": round(100.0 * (b - a) / 600.0, 1)})
    m["perMinute"] = new_pm

    # chapters: stretch boundaries, resample the three columns
    for k, row in enumerate(m["chapterRows"]):
        row["t0"] = round(float(row["t0"]) * STRETCH, 1)
        row["t1"] = round(float(row["t1"]) * STRETCH, 1)
        tau = ((row["t0"] + row["t1"]) / 2.0) / rt1
        row["warm"] = round(sample(cur["warm"], (tau + WARM_ROLL_TAU) % 1.0), 1)
        row["V"] = round(sample(cur["V"], tau), 1)
        row["db"] = round(sample(cur["db"], tau) + DB_SHIFT, 1)
        cnt = sum(1 for s in scenes if row["t0"] <= float(s["s"]) < row["t1"])
        row["scenes"] = cnt
        row["per_min"] = round(cnt / ((row["t1"] - row["t0"]) / 60.0), 1)
    m["chapters"] = [[r["t0"], r["name"]] for r in m["chapterRows"]]
    m["scenes"] = [{k: s[k] for k in ("i", "s", "e", "dur", "trans")} for s in scenes]
    m["camera"] = cam

    # palette: rotate every hue by +120 degrees
    pal = [dict(p) for p in m["palette"]]
    for p in pal:
        p["H"] = int((float(p["H"]) + HUE_ROTATE) % 360.0)
        p["hex"] = "(rotated; hex not recomputed)"
    m["palette"] = pal
    jdump(m, os.path.join(d, "metrics.json"))
    jdump(pal, os.path.join(d, "palette.json"))

    # -- silences: stretch the timestamps, keep every event ------------------
    # Keeping all 14 is deliberate: it makes the normalized-position row an
    # INVARIANCE test (tau is unchanged by a uniform stretch) while the
    # per-10-minute rate row still has to move.
    sil = jload(os.path.join(d, "silences.json"))
    for e in sil.get("events", []):
        for k in ("t_in", "t_out", "dur"):
            if e.get(k) is not None:
                e[k] = round(float(e[k]) * STRETCH, 4)
    jdump(sil, os.path.join(d, "silences.json"))

    # stage-1 CSV, resampled onto the stretched grid
    stretch_timeseries(os.path.join(src, "timeseries", "timeseries_10fps.csv"),
                       os.path.join(d, "timeseries", "timeseries_10fps.csv"),
                       STRETCH)

    # candidates.json is left as-is: compare.py does not read it.
    return d


# ---------------------------------------------------------------------------
# 4. episodes/inca_pix_otherwarm — same numbers, different warm predicate
# ---------------------------------------------------------------------------
def build_otherwarm():
    src = os.path.join(EPI, "inca_pix")
    d = os.path.join(EPI, "inca_pix_otherwarm")
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(src, d)
    mt = jload(os.path.join(d, "meta.json"))
    mt["video"]["id"] = "inca_pix_otherwarm"
    jdump(mt, os.path.join(d, "meta.json"))
    man = jload(os.path.join(d, "derive_manifest.json"))
    man["parameters"]["warm_predicate"].update(hue_lo=0.0, hue_hi=120.0,
                                               s_min=10.0, v_min=20.0)
    jdump(man, os.path.join(d, "derive_manifest.json"))
    return d


def main():
    os.makedirs(EPI, exist_ok=True)
    for fn in (build_inca, build_inca_pix, build_perturbed, build_otherwarm):
        p = fn()
        print("built %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
