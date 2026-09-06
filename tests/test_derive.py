#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit + contract tests for pipeline/derive.py.

The reference-episode reproduction is measured by validate_derive.py; this file
covers the pieces that reproduction cannot exercise:
  * the rounding / indexing / interval conventions in isolation (they are
    load-bearing and silently wrong-able)
  * the --rgb pixel path, which has NO ground truth in this repo and is therefore
    tested against hand-computed values on synthetic frames (CONTRACT ONLY)
Run:  python3 test_derive.py
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.join(os.path.dirname(HERE), "pipeline")
sys.path.insert(0, PIPE)

import derive as D  # noqa: E402

fails = []
n_checks = 0


def check(name, cond, detail=""):
    global n_checks
    n_checks += 1
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + detail) if detail else ""))
    if not cond:
        fails.append(name)


print("\n[indexing / rounding conventions]")
# int() would give 453 here because 45.4*10 == 453.99999999999994
check("row_of(45.4) == 454", D.row_of(45.4) == 454, "int() would give 453")
check("row_of(0.0) == 0", D.row_of(0.0) == 0)
check("row_of clips to n-1", D.row_of(9999.0, 100) == 99)
check("timecode floors, not rounds", D.timecode(818.816) == "13:38" and D.timecode(2.833) == "0:02")
check("timecode 301.0 -> 5:01", D.timecode(301.0) == "5:01")
# banker's rounding: this is what makes chapter 3 (exactly 2.25 scenes/min) print 2.2
check("r1(2.25) == 2.2 (banker's)", D.r1(2.25) == 2.2)
check("r1(2.35) == 2.4", D.r1(2.35) == 2.4)

print("\n[local extremum convention: strict left, non-strict right]")
x = np.array([0.0, 1.0, 1.0, 0.0])
lm = D.localmax(x, 1)
check("plateau keeps the LEFT sample only", bool(lm[1]) and not bool(lm[2]),
      "-> %s" % lm.astype(int).tolist())

print("\n[transition classifier: ordered decision list]")
mk = lambda lmax, lmin, dip: {"lmax": lmax, "lmin": lmin, "_dip_raw": dip}
check("whiteout wins over everything", D.classify(mk(205.0, 10.0, 0.05)) == "B")
check("blackout is tested before dip", D.classify(mk(150.0, 20.0, 0.05)) == "C")
check("dip < 0.35 -> D", D.classify(mk(150.0, 100.0, 0.30)) == "D")
check("dip in [0.35,0.62) -> E", D.classify(mk(150.0, 100.0, 0.50)) == "E")
check("dip in [0.62,0.72) -> F", D.classify(mk(150.0, 100.0, 0.70)) == "F")
check("dip >= 0.72 -> A", D.classify(mk(150.0, 100.0, 0.90)) == "A")
check("thresholds are >= / <= as recovered",
      D.classify(mk(200.0, 100.0, 0.9)) == "B" and D.classify(mk(150.0, 30.0, 0.9)) == "C")
check("dip cuts are strict <",
      D.classify(mk(150.0, 100.0, 0.35)) == "E" and D.classify(mk(150.0, 100.0, 0.72)) == "A")

print("\n[camera label thresholds]")
check("cum_x == +14 pans right (>= is inclusive)", D.camera_label(14.0, 0.0, None) == D.LBL_PAN_R)
check("cum_x == 13.9 is static", D.camera_label(13.9, 0.0, None) == D.LBL_STATIC)
check("cum_y == -10 tilts up", D.camera_label(0.0, -10.0, None) == D.LBL_TILT_U)
check("zoom comparisons are strict",
      D.camera_label(0, 0, 0.955) == D.LBL_STATIC and D.camera_label(0, 0, 1.05) == D.LBL_STATIC)
check("zoom 0.954 -> pull back", D.camera_label(0, 0, 0.954) == D.LBL_ZOUT)
check("part order is pan + tilt + zoom",
      D.camera_label(20, 20, 1.2) == " + ".join([D.LBL_PAN_R, D.LBL_TILT_D, D.LBL_ZIN]))
check("no zoom -> zoom clause absent", D.camera_label(20, 20, None) ==
      " + ".join([D.LBL_PAN_R, D.LBL_TILT_D]))

print("\n[window mean guards]")
check("wmean1 of an all-NaN window is None", D.wmean1(np.array([np.nan, np.nan]), 0, 2) is None)
check("wmean1 ignores NaN", D.wmean1(np.array([1.0, np.nan, 3.0]), 0, 3) == 2.0)
check("wmean1(None) is None", D.wmean1(None, 0, 5) is None)

print("\n[pixel path — CONTRACT ONLY, no published ground truth exists]")
tmp = tempfile.mkdtemp(prefix="derive_test_")
# frame 0: uniform (200,100,50) -> max 200, hue 20 deg  => V 78.4, warm 100%
# frame 1: uniform ( 20, 60, 80) -> max  80, hue 200 deg => V 31.4, warm 0%
# frame 2: half warm / half cool                          => warm 50%
f = np.zeros((3, 4, 4, 3), dtype=np.uint8)
f[0] = [200, 100, 50]
f[1] = [20, 60, 80]
f[2, :, :2] = [200, 100, 50]
f[2, :, 2:] = [20, 60, 80]
rgbp = os.path.join(tmp, "rgb.npy")
np.save(rgbp, f)
wp = {"hue_lo": 330.0, "hue_hi": 90.0, "s_min": 0.0, "v_min": 0.0}
v, w, nf = D.pixel_series(rgbp, 3, wp)
check("V = mean(max(R,G,B))/2.55", abs(v[0] - 200 / 2.55) < 1e-4 and abs(v[1] - 80 / 2.55) < 1e-4,
      "got %.3f / %.3f" % (v[0], v[1]))
check("warm predicate: hue 20deg is warm, 200deg is not", w[0] == 100.0 and w[1] == 0.0)
check("warm is a pixel FRACTION x100", w[2] == 50.0)
v2, w2, _ = D.pixel_series(rgbp, 3, {"hue_lo": 0.0, "hue_hi": 90.0, "s_min": 90.0, "v_min": 0.0})
check("saturation floor is applied (S=75 < 90 -> excluded)", w2[0] == 0.0,
      "warm predicate is parameterised, not hardcoded")

print("\n[end-to-end on a synthetic episode]")
NR = 1250                      # 125 s -> 3 minute windows, the last one partial
rng = np.random.default_rng(0)
cols = {
    "t_sec": np.round(np.arange(NR) / 10.0, 1),
    "frame_delta": rng.random(NR),
    "luma_mean": 100 + 5 * rng.standard_normal(NR),
    "luma_spatial_std": 30 + rng.random(NR),
    "sharpness": 7 + rng.random(NR),
    "sharpness_dip_ratio": np.ones(NR),
    "ssm_novelty": rng.random(NR) * 0.05,
    "straddle_distance": rng.random(NR) * 0.05,
    "pan_dx": np.ones(NR),
    "pan_dy": -np.ones(NR),
    "audio_rms_dbfs": np.full(NR, -20.0),
    "spectral_centroid_hz": np.full(NR, 1000.0),
}
for r in (300, 700, 1100):                    # three obvious hard cuts
    cols["straddle_distance"][r] = 0.9
cols["sharpness_dip_ratio"][500] = 0.1        # one occluder wipe
csvp = os.path.join(tmp, "ts.csv")
with open(csvp, "w", encoding="utf-8") as fh:
    fh.write(",".join(cols.keys()) + "\n")
    for i in range(NR):
        fh.write(",".join("%.4f" % cols[c][i] for c in cols) + "\n")
scenes = [{"i": 1, "s": 0.0, "e": 30.0, "dur": 30.0},
          {"i": 2, "s": 30.0, "e": 50.0, "dur": 20.0},
          {"i": 3, "s": 50.0, "e": 50.5, "dur": 0.5},     # too short for camera
          {"i": 4, "s": 50.5, "e": 125.0, "dur": 74.5}]
scp = os.path.join(tmp, "scenes.json")
json.dump(scenes, open(scp, "w"))
json.dump([[0, "one"], [60, "two"]], open(os.path.join(tmp, "ch.json"), "w"))
json.dump([[0.0, 1.0, "hello"], [9.0, 10.0, "world"]], open(os.path.join(tmp, "tr.json"), "w"))
big = np.zeros((NR, 4, 4, 3), dtype=np.uint8)
big[:] = [200, 100, 50]
np.save(os.path.join(tmp, "big.npy"), big)
out = os.path.join(tmp, "out")
cmd = [sys.executable, os.path.join(PIPE, "derive.py"), "--ts", csvp, "--scenes", scp,
       "--chapters", os.path.join(tmp, "ch.json"), "--transcript", os.path.join(tmp, "tr.json"),
       "--rgb", os.path.join(tmp, "big.npy"), "--out", out]
p = subprocess.run(cmd, capture_output=True, text=True)
check("derive.py exits 0 on a synthetic episode", p.returncode == 0, p.stderr.strip()[-200:])
if p.returncode == 0:
    m = json.load(open(os.path.join(out, "metrics.json"), encoding="utf-8"))
    check("3 minute windows for 125 s", len(m["perMinute"]) == 3)
    check("final window is flagged partial", m["perMinute"][2]["fill"] == 8.3,
          "fill=%s%%" % m["perMinute"][2]["fill"])
    check("db = plain mean of a constant -20 dBFS column",
          all(r["db"] == -20.0 for r in m["perMinute"]))
    check("V non-null with --rgb", abs(m["perMinute"][0]["V"] - 78.4) < 0.05,
          "V=%s" % m["perMinute"][0]["V"])
    check("warm non-null with --rgb", m["perMinute"][0]["warm"] == 100.0)
    check("chapterRows t1 chain ends at ceil(runtime)", m["chapterRows"][-1]["t1"] == 125)
    check("chapter scene counts sum to the scene count",
          sum(r["scenes"] for r in m["chapterRows"]) == len(scenes))
    cam = json.load(open(os.path.join(out, "camera.json"), encoding="utf-8"))
    check("camera drops the 0.5 s scene", [c["i"] for c in cam] == [1, 2, 4])
    check("cum_x = trimmed row count x 1 px", cam[0]["cum_x"] == 300 - 6,
          "got %s" % cam[0]["cum_x"])
    check("cum_y sign preserved (no negation)", cam[0]["cum_y"] == -(300 - 6))
    cnd = json.load(open(os.path.join(out, "candidates.json"), encoding="utf-8"))
    rows = [c["row"] for c in cnd["candidates"]]
    check("detector finds the 3 planted cuts and the planted wipe",
          set([300, 500, 700, 1100]) <= set(rows), "rows=%s" % rows)
    types = {c["row"]: c["trans_code"] for c in cnd["candidates"]}
    check("planted wipe classified D, planted cuts A",
          types.get(500) == "D" and all(types.get(r) == "A" for r in (300, 700, 1100)))
    sil = json.load(open(os.path.join(out, "silences.json"), encoding="utf-8"))
    check("silence detected in the 8 s caption gap", sil["n_events"] == 1 and
          sil["events"][0]["dur"] == 8.0)
    man = json.load(open(os.path.join(out, "derive_manifest.json"), encoding="utf-8"))
    check("manifest still declares zoom as requires_video",
          "camera[].zoom" in man["requires_video"])
    check("manifest stamps the (unrecovered) warm thresholds",
          "STATUS" in man["parameters"]["warm_predicate"])

print("\n[bad input must fail loudly, not emit plausible garbage]")


def run(args):
    return subprocess.run([sys.executable, os.path.join(PIPE, "derive.py")] + args,
                          capture_output=True, text=True)


# (a) a scene outside the timeseries used to be clamped, producing a fabricated
#     D/dip/trans and a cum_x = cum_y = 0.0 "near-static" camera row.
oob = [{"i": 1, "s": 0.0, "e": 30.0, "dur": 30.0},
       {"i": 2, "s": 9999.0, "e": 10050.0, "dur": 51.0}]
oobp = os.path.join(tmp, "oob.json")
json.dump(oob, open(oobp, "w"))
p3 = run(["--ts", csvp, "--scenes", oobp, "--out", os.path.join(tmp, "o3")])
check("out-of-range scene is rejected", p3.returncode != 0 and "outside" in p3.stderr,
      p3.stderr.strip().splitlines()[-1][:90] if p3.stderr else "no stderr")
check("no fabricated scenes.json is written",
      not os.path.exists(os.path.join(tmp, "o3", "scenes.json")))
p3b = run(["--ts", csvp, "--scenes", oobp, "--allow-out-of-range-scenes",
           "--out", os.path.join(tmp, "o3b")])
check("the override still records what it clamped", p3b.returncode == 0 and
      json.load(open(os.path.join(tmp, "o3b", "derive_manifest.json"),
                     encoding="utf-8"))["input_warnings"]["out_of_range_scenes"])

# (b) e <= s
rev = [{"i": 1, "s": 0.0, "e": 30.0, "dur": 30.0},
       {"i": 2, "s": 60.0, "e": 40.0, "dur": -20.0}]
revp = os.path.join(tmp, "rev.json")
json.dump(rev, open(revp, "w"))
p4 = run(["--ts", csvp, "--scenes", revp, "--out", os.path.join(tmp, "o4")])
check("reversed scene (e <= s) is rejected", p4.returncode != 0 and "<=" in p4.stderr)

# (c) a hole in the timeseries must not become NaN in the JSON, nor a phantom "A"
holes = dict(cols)
holes["sharpness_dip_ratio"] = cols["sharpness_dip_ratio"].copy()
holes["sharpness_dip_ratio"][:] = 1.0
csvh = os.path.join(tmp, "ts_hole.csv")
with open(csvh, "w", encoding="utf-8") as fh:
    fh.write(",".join(cols.keys()) + "\n")
    for i in range(NR):
        vals = ["" if (c == "sharpness_dip_ratio" and 295 <= i <= 320)
                else "%.4f" % holes[c][i] for c in cols]
        fh.write(",".join(vals) + "\n")
hsc = os.path.join(tmp, "hsc.json")
json.dump([{"i": 1, "s": 0.0, "e": 30.0, "dur": 30.0},
           {"i": 2, "s": 30.5, "e": 125.0, "dur": 94.5}], open(hsc, "w"))
p5 = run(["--ts", csvh, "--scenes", hsc, "--out", os.path.join(tmp, "o5")])
check("all-NaN descriptor window is rejected, not written as NaN",
      p5.returncode != 0 and "no finite" in p5.stderr,
      (p5.stderr.strip().splitlines()[-1][:90] if p5.stderr else "rc=%d" % p5.returncode))
sp = os.path.join(tmp, "o5", "scenes.json")
check("no scenes.json with a bare NaN token is left behind",
      (not os.path.exists(sp)) or "NaN" not in open(sp, encoding="utf-8").read())

# (d) a non-uniform t_sec grid breaks every row index
csvn = os.path.join(tmp, "ts_nonuni.csv")
with open(csvn, "w", encoding="utf-8") as fh:
    fh.write(",".join(cols.keys()) + "\n")
    for i in range(NR):
        if i % 3 == 0:
            continue
        fh.write(",".join("%.4f" % cols[c][i] for c in cols) + "\n")
p6 = run(["--ts", csvn, "--out", os.path.join(tmp, "o6")])
check("non-uniform 0.1 s grid is rejected by default", p6.returncode != 0 and
      "uniform" in p6.stderr)
p7 = run(["--ts", csvn, "--allow-nonuniform-grid", "--out", os.path.join(tmp, "o7")])
check("--allow-nonuniform-grid overrides but warns",
      p7.returncode == 0 and "WARNING" in p7.stderr)

print("\n[candidates-only mode]")
out2 = os.path.join(tmp, "out2")
p2 = subprocess.run([sys.executable, os.path.join(PIPE, "derive.py"), "--ts", csvp,
                     "--out", out2], capture_output=True, text=True)
check("exits 0 without --scenes", p2.returncode == 0, p2.stderr.strip()[-200:])
check("emits candidates.json + worksheet.md only",
      sorted(os.listdir(out2)) == ["candidates.json", "derive_manifest.json", "worksheet.md"],
      str(sorted(os.listdir(out2))))

print("\n%d checks, %d failed" % (n_checks, len(fails)))
print("RESULT: " + ("ALL PASS" if not fails else "FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
