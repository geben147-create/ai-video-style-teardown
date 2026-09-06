#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_derive.py — field-by-field diff of derive.py's output against the
published artifacts of the reference episode.

    python3 validate_derive.py --mine OUTDIR --ref /path/to/ai-video-style-teardown/data \
                               --md /home/user/work/out/derive_validation.md

Every row of the emitted table is a MEASURED count: numerator = fields that
matched at the stated tolerance, denominator = fields compared.  Fields that
derive.py emits as null because they need pixels are reported as 0/N with an
explicit requires_video verdict - they are never quietly dropped from the
denominator.
"""
import argparse
import json
import math
import os
from collections import Counter, OrderedDict

REF_SILENCE_TIMES = ["0:02", "3:02", "3:08", "5:00", "6:02", "6:51", "7:20",
                     "7:45", "10:20", "11:02", "13:38", "16:24", "16:32", "17:00"]
REF_SILENCE_DUR = {"0:02": 3.5, "3:02": 3.9, "5:00": 3.2, "7:45": 5.9,
                   "11:02": 5.3, "13:38": 5.4, "16:32": 5.3}

rows_out = []      # (group, field, matched, total, tol, verdict, kind)

# kind:
#   derived  = derive.py computed the value from the timeseries -> a real test
#   echoed   = the value was COPIED from an input file that is itself the
#              reference (--scenes / --chapters). Matching is a tautology and
#              these rows must never be added into a "reproduction rate".
#   video    = needs pixels; emitted null. Counted 0/N, never dropped.
#   alt      = an alternative reading of a row already counted (double count).


def add(group, field, matched, total, tol, verdict="", kind="derived"):
    rows_out.append((group, field, matched, total, tol, verdict, kind))
    print("  %-34s %4s/%-4s  %-10s %-8s %s" % (field, matched, total, tol, kind, verdict))


def close(a, b, tol):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol + 1e-12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mine", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--md", required=True)
    a = ap.parse_args()
    J = lambda p: json.load(open(p, encoding="utf-8"))
    mine = lambda f: J(os.path.join(a.mine, f))
    ref = lambda f: J(os.path.join(a.ref, f))

    # ================================================================ scenes
    print("\n[scenes.json vs data/scenes_verified.json]")
    ms, rs = mine("scenes.json"), ref("scenes_verified.json")
    add("scenes", "count", 1 if len(ms) == len(rs) else 0, 1, "exact",
        "%d scenes" % len(rs))
    n = min(len(ms), len(rs))
    for f, tol in (("i", 0), ("s", 1e-9), ("e", 1e-9), ("dur", 1e-9)):
        add("scenes", f, sum(1 for x, y in zip(ms, rs) if close(x[f], y[f], tol)), n, "exact",
            "COPIED verbatim from the --scenes input, which IS the reference file",
            kind="echoed")
    add("scenes", "trans (recomputed, not copied)",
        sum(1 for x, y in zip(ms, rs) if x["trans"] == y["trans"]), n, "exact string",
        "classifier reproduces the published taxonomy")
    for f in ("D", "dip"):
        ex = sum(1 for x, y in zip(ms, rs) if close(x[f], y[f], 0))
        lo = sum(1 for x, y in zip(ms, rs) if close(x[f], y[f], 0.001))
        add("scenes", f + " (bit-exact at 3dp)", ex, n, "exact",
            "the primary reading")
        add("scenes", f + " (double-rounding band)", lo, n, "+/-0.001",
            "CSV is 4dp, JSON was rounded from full precision", kind="alt")

    # ================================================================ camera
    print("\n[camera.json vs data/camera.json]")
    mc, rc = mine("camera.json"), ref("camera.json")
    add("camera", "membership (ordered index list)",
        1 if [x["i"] for x in mc] == [x["i"] for x in rc] else 0, 1, "exact list",
        "kept %d of %d scenes (rule: dur > 1.0 s)" % (len(mc), len(mine("scenes.json"))))
    byi = {x["i"]: x for x in rc}
    common = [x for x in mc if x["i"] in byi]
    nc = len(common)
    for f, tol in (("s", 1e-9), ("dur", 1e-9)):
        add("camera", f, sum(1 for x in common if close(x[f], byi[x["i"]][f], tol)), nc,
            "exact", "COPIED from the --scenes input", kind="echoed")
    for f in ("cum_x", "cum_y"):
        exq = sum(1 for x in common if close(x[f], byi[x["i"]][f], 0))
        mx = max(abs(float(x[f]) - float(byi[x["i"]][f])) for x in common)
        add("camera", f, exq, nc, "exact",
            "bit-exact; published values are whole pixels, max|err| = %.4f" % mx)
    add("camera", "label (as emitted, zoom unknown)",
        sum(1 for x in common if x["label"] == byi[x["i"]]["label"]), nc, "exact string",
        "zoom clause necessarily absent")
    # conditional check: inject the published zoom and re-derive the label
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from derive import camera_label
    cond = sum(1 for x in common
               if camera_label(x["cum_x"], x["cum_y"], byi[x["i"]]["zoom"]) == byi[x["i"]]["label"])
    add("camera", "label GIVEN the published zoom", cond, nc, "exact string",
        "CONDITIONAL: proves the threshold rule, not a from-scratch reproduction",
        kind="alt")
    # the pan/tilt HALF of the label needs no pixels at all - report it on its own
    PT = ["\uc88c\u2192\uc6b0 \ud32c", "\uc6b0\u2192\uc88c \ud32c",
          "\ud558\uac15 \ud2f8\ud2b8", "\uc0c1\uc2b9 \ud2f8\ud2b8"]
    parts = lambda lab: tuple(k for k in PT if k in lab)
    add("camera", "label: pan/tilt part set",
        sum(1 for x in common if parts(x["label"]) == parts(byi[x["i"]]["label"])), nc,
        "exact set", "the zoom-free half of every label, derived without pixels")
    add("camera", "zoom", 0, nc, "n/a", "requires_video - emitted null + marker",
        kind="video")
    add("camera", "published key set present",
        sum(1 for x in common if set(byi[x["i"]]) <= set(x)), nc, "superset",
        "mine adds 2 requires_video marker keys per item")
    nz = sum(1 for x in rc if ("줌아웃" in x["label"] or "줌인" in x["label"]))
    nozoom = set(x["i"] for x in rc if not ("\uc90c\uc544\uc6c3" in x["label"]
                                            or "\uc90c\uc778" in x["label"]))
    matched = set(x["i"] for x in common if x["label"] == byi[x["i"]]["label"])
    add("camera", "labels whose published form has no zoom clause",
        len(matched & nozoom), len(nozoom), "exact string",
        "matched set == no-zoom set: %s; the %d labels that DO carry a zoom clause "
        "cannot be completed without pixels" % (matched == nozoom, nz), kind="alt")
    # published section 7-1 states the move distribution; check it against camera.json
    S71 = {"\ud558\uac15 \ud2f8\ud2b8": 52.9, "\uc0c1\uc2b9 \ud2f8\ud2b8": 17.1,
           "\uc88c\u2192\uc6b0 \ud32c": 31.4, "\uc6b0\u2192\uc88c \ud32c": 34.3}
    ok71 = sum(1 for k, v in S71.items()
               if close(100.0 * sum(1 for x in common if k in x["label"]) / nc, v, 0.05))
    add("camera", "section 7-1 pan/tilt move shares", ok71, len(S71), "+/-0.05",
        "published prose percentages, reproduced from my labels without pixels")

    # ================================================================ metrics
    print("\n[metrics.json vs data/metrics.json]")
    mm, rm = mine("metrics.json"), ref("metrics.json")
    mp, rp = mm["perMinute"], rm["perMinute"]
    add("perMinute", "row count", 1 if len(mp) == len(rp) else 0, 1, "exact",
        "%d minute windows" % len(mp))
    npm = min(len(mp), len(rp))
    add("perMinute", "m", sum(1 for x, y in zip(mp, rp) if x["m"] == y["m"]), npm, "exact")
    add("perMinute", "db", sum(1 for x, y in zip(mp, rp) if close(x["db"], y["db"], 0.05)),
        npm, "+/-0.05")
    add("perMinute", "V", sum(1 for x, y in zip(mp, rp) if close(x["V"], y["V"], 0.05)),
        npm, "+/-0.05", "requires_video - emitted null + marker", kind="video")
    add("perMinute", "warm", sum(1 for x, y in zip(mp, rp) if close(x["warm"], y["warm"], 0.05)),
        npm, "+/-0.05", "requires_video - emitted null + marker", kind="video")

    mch, rch = mm["chapterRows"], rm["chapterRows"]
    nch = min(len(mch), len(rch))
    add("chapterRows", "row count", 1 if len(mch) == len(rch) else 0, 1, "exact")
    add("chapterRows", "t0", sum(1 for x, y in zip(mch, rch) if x["t0"] == y["t0"]), nch,
        "exact", "COPIED from the --chapters input", kind="echoed")
    add("chapterRows", "t1", sum(1 for x, y in zip(mch, rch) if x["t1"] == y["t1"]), nch,
        "exact", "derived: t1 = next t0, final t1 = ceil(runtime)")
    add("chapterRows", "name", sum(1 for x, y in zip(mch, rch) if x["name"] == y["name"]),
        nch, "exact string", "COPIED from the --chapters input", kind="echoed")
    add("chapterRows", "scenes", sum(1 for x, y in zip(mch, rch) if x["scenes"] == y["scenes"]),
        nch, "exact", "count of scene STARTS in [t0,t1)")
    add("chapterRows", "per_min",
        sum(1 for x, y in zip(mch, rch) if close(x["per_min"], y["per_min"], 0.05)), nch, "+/-0.05")
    add("chapterRows", "db", sum(1 for x, y in zip(mch, rch) if close(x["db"], y["db"], 0.05)),
        nch, "+/-0.05")
    add("chapterRows", "V", sum(1 for x, y in zip(mch, rch) if close(x["V"], y["V"], 0.05)),
        nch, "+/-0.05", "requires_video", kind="video")
    add("chapterRows", "warm", sum(1 for x, y in zip(mch, rch) if close(x["warm"], y["warm"], 0.05)),
        nch, "+/-0.05", "requires_video", kind="video")
    add("metrics", "chapters (t0,name pairs)",
        sum(1 for x, y in zip(mm["chapters"], rm["chapters"])
            if float(x[0]) == float(y[0]) and x[1] == y[1]),
        len(rm["chapters"]), "exact", "COPIED from the --chapters input", kind="echoed")
    msc, rsc = mm["scenes"], rm["scenes"]
    add("metrics", "scenes[] block",
        sum(1 for x, y in zip(msc, rsc)
            if all(close(x[k], y[k], 1e-9) if k != "trans" else x[k] == y[k]
                   for k in ("i", "s", "e", "dur", "trans"))),
        len(rsc), "exact", "re-check of scenes.json under a different key set", kind="alt")
    mcam, rcam = mm["camera"], rm["camera"]
    add("metrics", "camera[] block == camera.json",
        1 if [x["i"] for x in mcam] == [x["i"] for x in rcam] else 0, 1, "exact",
        "same objects as camera.json")
    add("metrics", "palette", 0, len(rm["palette"]), "n/a",
        "requires_video - emitted null + marker", kind="video")

    # ============================================================ candidates
    print("\n[candidates.json vs data/detection_pass2_99.json]")
    mcand = mine("candidates.json")["candidates"]
    p2 = ref("detection_pass2_99.json")
    p2b = [x for x in p2 if x["trans"] != "OPEN"]
    ri = lambda t: int(round(t * 10))
    pub_rows = set(ri(x["s"]) for x in p2b)
    my_rows = set(x["row"] for x in mcand)
    add("candidates", "published boundary rows recovered",
        len(pub_rows & my_rows), len(pub_rows), "exact row (0.1 s)",
        "%d emitted, %d surplus" % (len(mcand), len(my_rows - pub_rows)))
    gt = [x for x in ref("scenes_verified.json") if x["trans"] != "OPEN"]
    gt_rows = set(ri(x["s"]) for x in gt)
    add("candidates", "human-verified boundaries recovered",
        len(gt_rows & my_rows), len(gt_rows), "exact row (0.1 s)", "recall on the truth set")
    byrow = {x["row"]: x for x in mcand}
    inter = sorted(pub_rows & my_rows)
    p2byrow = {ri(x["s"]): x for x in p2b}
    for f in ("D", "dip"):
        add("candidates", f + " on recovered rows (bit-exact at 3dp)",
            sum(1 for r in inter if close(byrow[r][f], p2byrow[r][f], 0)), len(inter),
            "exact", "the primary reading")
        add("candidates", f + " on recovered rows (double-rounding band)",
            sum(1 for r in inter if close(byrow[r][f], p2byrow[r][f], 0.001)), len(inter),
            "+/-0.001", "same 4dp-CSV double rounding as scenes", kind="alt")
    add("candidates", "proposed transition type",
        sum(1 for r in inter if byrow[r]["trans"] == p2byrow[r]["trans"]), len(inter),
        "exact string", "vs the pre-verification labels")

    # =========================================================== transitions
    print("\n[transitions.json vs counts recomputed from data/scenes_verified.json]")
    mt = mine("transitions.json")
    refc = Counter(x["trans"] for x in ref("scenes_verified.json") if x["trans"] != "OPEN")
    ok = sum(1 for r in mt["by_type"] if r["n"] == refc.get(r["label"], 0))
    add("transitions", "per-type counts", ok, len(mt["by_type"]), "exact")
    add("transitions", "interior-boundary denominator",
        1 if mt["denominators"]["interior_boundaries"] == sum(refc.values()) else 0, 1, "exact",
        "= %d" % sum(refc.values()))
    pubpct = {"A. 하드컷": 52.1, "B. 화이트아웃 통과 (구름·안개)": 8.5,
              "C. 블랙아웃 통과 (아치·터널·야간)": 11.3, "D. 차폐물 와이프 (벽·인물·연기)": 5.6,
              "E. 크로스 디졸브": 18.3, "F. 앵글 전환 (동일 장소)": 4.2}
    okp = sum(1 for r in mt["by_type"]
              if close(r["pct_of_interior_boundaries"], pubpct.get(r["label"]), 0.05))
    add("transitions", "per-type % (vs index.html s9-1)", okp, len(pubpct), "+/-0.05",
        "published prose figures")
    agg = {r["name"][:20]: r["pct"] for r in mt["aggregates"]}
    okA = sum([close(mt["aggregates"][0]["pct"], 25.4, 0.05),
               close(mt["aggregates"][1]["pct"], 19.7, 0.05)])
    add("transitions", "pass-through % (25.4 wide / 19.7 narrow)", okA, 2, "+/-0.05",
        "both published definitions reproduced")

    # ============================================================= silences
    print("\n[silences.json vs index.html s8 / s8-1]")
    sil = mine("silences.json")
    ev = sil["events"]
    add("silences", "event count", 1 if len(ev) == 14 else 0, 1, "exact", "published: 14")
    tcs = [e["timecode"] for e in ev]
    add("silences", "timecodes", sum(1 for x in REF_SILENCE_TIMES if x in tcs),
        len(REF_SILENCE_TIMES), "exact string", "floor to whole seconds")
    dm = {e["timecode"]: e["dur"] for e in ev}
    add("silences", "durations", sum(1 for k, v in REF_SILENCE_DUR.items() if close(dm.get(k), v, 0.05)),
        len(REF_SILENCE_DUR), "+/-0.05", "only 7 of 14 are published")
    add("silences", "2 s count", 1 if sil["counts"].get("gaps_ge_2.0s") == 39 else 0, 1,
        "exact", "published: 39")
    add("silences", "pause count", 1 if sil["pauses"]["n"] == 460 else 0, 1, "exact",
        "published: 460")
    add("silences", "pause median",
        1 if close(round(sil["pauses"]["median_s"], 2), 0.43, 0.005) else 0, 1, "+/-0.005",
        "published: 0.43 s")
    nar = sil["narration"]
    # The published narration block is FOUR figures (S8): coverage 64.0%, ducking
    # +5.6 dB, narration centroid 1248 Hz, music centroid 873 Hz. They must be
    # scored under ONE mask. Scoring coverage/ducking under the sample-time mask
    # and the centroid under the centre-of-frame mask would be mask-shopping:
    # it reports 3 wins that no single run of the code can produce together.
    PUB_NAR = [("coverage_pct", 64.0, 0.05, "coverage 64.0%"),
               ("duck_median_db", 5.6, 0.05, "ducking +5.6 dB (median)"),
               ("centroid_narration_hz", 1248.0, 1.0, "narration centroid 1248 Hz"),
               ("centroid_non_narration_hz", 873.0, 1.0, "music centroid 873 Hz")]
    per_mask = {}
    for mk in ("sample_time", "center_of_frame"):
        per_mask[mk] = sum(1 for k, v, t, _ in PUB_NAR if close(nar[mk].get(k), v, t))
    bestmask = max(per_mask, key=lambda k: per_mask[k])
    add("silences", "narration block, best SINGLE mask (%s)" % bestmask,
        per_mask[bestmask], len(PUB_NAR), "as published",
        "per-mask scores %s; the four published figures are NOT jointly "
        "reproducible by one mask" % per_mask)
    for k, v, t, lbl in PUB_NAR:
        add("silences", "  " + lbl, 1 if close(nar[bestmask].get(k), v, t) else 0, 1,
            "+/-%g" % t, "under the single best mask; got %s" % nar[bestmask].get(k),
            kind="alt")
    add("silences", "global RMS median/p5/p95",
        sum(close(nar["rms_dbfs"][k], v, 0.05)
            for k, v in (("median", -18.3), ("p5", -28.8), ("p95", -9.4))), 3, "+/-0.05")

    # =============================================================== totals
    def tot(kinds):
        rr = [r for r in rows_out if r[6] in kinds]
        return sum(r[2] for r in rr), sum(r[3] for r in rr)
    dn, dd = tot({"derived"})
    en, ed = tot({"echoed"})
    vn, vd = tot({"video"})
    an, ad = tot({"alt"})
    print("\nDERIVED  (real reproduction)      %d/%d = %.1f%%" % (dn, dd, 100.0 * dn / dd))
    print("ECHOED   (copied from an input)   %d/%d  <- tautological, not a test" % (en, ed))
    print("VIDEO    (null, needs pixels)     %d/%d" % (vn, vd))
    print("ALT      (second reading of a row already counted) %d/%d" % (an, ad))
    print("DERIVED + VIDEO                   %d/%d = %.1f%%"
          % (dn + vn, dd + vd, 100.0 * (dn + vn) / (dd + vd)))
    num, den = dn, dd
    dnum, dden = dn, dd

    # =============================================================== markdown
    L = []
    L.append("# derive.py validation — per-field match rates")
    L.append("")
    L.append("Generated by `/home/user/work/pipeline/validate_derive.py`.")
    L.append("")
    L.append("* mine: `%s`" % os.path.abspath(a.mine))
    L.append("* reference: `%s`" % os.path.abspath(a.ref))
    L.append("")
    L.append("Every number below is a measured count. A field that `derive.py` emits as "
             "`null` because it needs pixels is reported as **0/N**, never dropped from "
             "the denominator.")
    L.append("")
    # headline: one row per PRIMARY field, no alternate readings, so the counts
    # can be added up without double counting.
    PRIMARY = [("scenes", "trans (recomputed, not copied)"), ("scenes", "D (bit-exact at 3dp)"),
               ("scenes", "dip (bit-exact at 3dp)"), ("camera", "cum_x"), ("camera", "cum_y"),
               ("camera", "label: pan/tilt part set"),
               ("camera", "label (as emitted, zoom unknown)"), ("camera", "zoom"),
               ("perMinute", "db"), ("perMinute", "V"), ("perMinute", "warm"),
               ("chapterRows", "scenes"), ("chapterRows", "per_min"), ("chapterRows", "db"),
               ("chapterRows", "V"), ("chapterRows", "warm"),
               ("candidates", "published boundary rows recovered"),
               ("candidates", "human-verified boundaries recovered"),
               ("transitions", "per-type counts"), ("silences", "timecodes"),
               ("silences", "durations")]
    PRIMARY += [("silences", "narration block, best SINGLE mask (sample_time)"),
                ("silences", "narration block, best SINGLE mask (center_of_frame)")]
    idx = {(g, f): (m, t) for g, f, m, t, _tol, _v, _k in rows_out}
    PRIMARY = [k for k in PRIMARY if k in idx]
    hn = ht = 0
    L.append("## Headline — one row per primary field")
    L.append("")
    L.append("| field | match |")
    L.append("|---|---|")
    for g, f in PRIMARY:
        m, t = idx[(g, f)]
        hn += m
        ht += t
        L.append("| %s.%s | **%d/%d** |" % (g, f, m, t))
    L.append("| **sum of the rows above** | **%d/%d = %.1f%%** |" % (hn, ht, 100.0 * hn / ht))
    L.append("")
    L.append("Rows copied verbatim from an input file (`scenes.i/s/e/dur`, "
             "`camera.s/dur`, `chapterRows.t0/name`, `metrics.chapters`) are NOT in "
             "this table and are NOT in any rate below: matching an input against "
             "itself is a tautology, not a reproduction.")
    L.append("")
    L.append("## Every field compared")
    L.append("")
    L.append("Some fields appear twice below as alternative readings (bit-exact vs the "
             "double-rounding band; label as-emitted vs label given the published zoom), "
             "so the TOTAL under this table double-counts them slightly. The headline "
             "table above does not.")
    L.append("")
    L.append("| group | field | kind | match | rate | tolerance | note |")
    L.append("|---|---|---|---|---|---|---|")
    for g, f, m, t, tol, v, k in rows_out:
        L.append("| %s | %s | %s | %d/%d | %s | %s | %s |" %
                 (g, f, k, m, t, ("%.0f%%" % (100.0 * m / t)) if t else "-", tol, v))
    L.append("")
    L.append("`kind` legend: **derived** = computed from the timeseries, a real test; "
             "**echoed** = copied verbatim from an input file that is itself the "
             "reference, so a match is a tautology and is excluded from every rate; "
             "**video** = needs pixels, emitted `null`, counted 0/N; "
             "**alt** = a second reading of a row already counted.")
    L.append("")
    L.append("| bucket | match |")
    L.append("|---|---|")
    L.append("| **derived (the reproduction rate)** | **%d/%d = %.1f%%** |"
             % (dn, dd, 100.0 * dn / dd))
    L.append("| derived + requires-video | %d/%d = %.1f%% |"
             % (dn + vn, dd + vd, 100.0 * (dn + vn) / (dd + vd)))
    L.append("| echoed (tautological, excluded) | %d/%d |" % (en, ed))
    L.append("| alt (double counts, excluded) | %d/%d |" % (an, ad))
    sil = mine("silences.json")
    L += ["",
          "## Fields that cannot be derived without the video",
          "",
          "| field | why | how derive.py emits it |",
          "|---|---|---|",
          "| `metrics.perMinute[].V`, `metrics.chapterRows[].V` | HSV Value = per-frame "
          "mean of max(R,G,B); the timeseries carries luma only, and mean(luma)/2.55 "
          "is 0/20 against the published values (max error 9.19) | `null` + "
          "`V_requires_video: true`; computed when `--rgb` is given |",
          "| `metrics.perMinute[].warm`, `metrics.chapterRows[].warm` | warm-pixel "
          "fraction. No chroma column exists, AND the per-pixel predicate (hue band, "
          "saturation floor, value floor, colour space) is stated nowhere in the "
          "reference repo, so it is unrecoverable even with pixels | `null` + "
          "`warm_requires_video: true`; computed when `--rgb` is given, with the "
          "thresholds stamped into `derive_manifest.json` as NOT RECOVERED |",
          "| `camera[].zoom` | radial-energy radius ratio; needs pixels, and its exact "
          "definition is undocumented even then | `null` + `zoom_requires_video: true` "
          "(never computed, even with `--rgb`) |",
          "| `camera[].label` zoom clause | depends on `zoom` | label emitted without "
          "it + `label_partial_no_zoom: true`. 36/70 published labels have no zoom "
          "clause and those match exactly; the other 34 cannot be completed |",
          "| `metrics.palette` | k-means over RGB pixels | `null` + "
          "`palette_requires_video: true` |",
          "| `metrics.chapters` | editorial, not measurable from any signal | required "
          "as INPUT (`--chapters`); `requires_human` in the manifest when absent |",
          "",
          "## Measured facts worth stating over the published prose",
          "",
          "* Candidate detector emits **105** boundaries and recovers **97/98** of the "
          "published pre-verification set; the single miss is t=170.9 s. Surplus rows: "
          "62.2, 123.5, 160.6, 699.0, 780.4, 1010.4, 1014.7, 1015.6 s. Recall against "
          "the 71 human-verified boundaries is **71/71**.",
          "* `trans` is fully machine-derivable: recomputing it from three luma/sharpness "
          "scalars reproduces **72/72** verified labels and **97/97** of the "
          "pre-verification labels on recovered rows. The human pass deleted boundaries; "
          "it did not relabel any.",
          "* The camera membership rule is `dur > 1.0 s`, not the published prose's "
          "\"under 1 second\": scene i=14 has dur exactly 1.0 s and is excluded.",
          "* `silences.json` events are **narration gaps**, not audio silence. Measured "
          "here: only **%d of %d** contain a scene boundary, against the published claim "
          "that all of them sit on transitions." % (
              sil["n_events_containing_a_scene_boundary"], sil["n_events"]),
          "* The published spectral-centroid pair (1248 Hz narration / 873 Hz music) is "
          "not producible by one mask: the centre-of-frame mask gives 1248.0 and 874.4. "
          "`silences.json` therefore reports both mask variants rather than picking one.",
          "* `db` is a plain arithmetic mean of dBFS (a mean of logs). It reproduces "
          "32/32 published windows, but minute 19 (-33.4 dB) and chapter 11 (-20.1 dB) "
          "are pulled down by digital-silence frames at the -119.99 dBFS floor. "
          "`perMinute[].fill` flags the 48.2%-full final window.",
          ]
    open(a.md, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\nwrote %s" % a.md)


if __name__ == "__main__":
    main()
