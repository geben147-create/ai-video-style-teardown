#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_compare_guards.py — TEST 6..8 for pipeline/compare.py.

Regression tests for defects found by adversarial review of the original
test_compare.py, which had ZERO coverage of these paths:

  TEST 6  REFUSAL ON UNKNOWN PARAMETERS.  compare.py's honesty contract says it
          refuses to compare quantities computed with different unrecovered
          parameters.  It originally refused only when BOTH sides stamped the
          parameter and the two stamps DIFFERED.  An episode dir that stamps
          nothing — the commonest real case — sailed straight through and got a
          classification, including CHANNEL_RULE.  An undeclared parameter is
          exactly as incomparable as a different one.

  TEST 7  CLI ARGUMENT VALIDATION.  --acts and --tolerances were unvalidated:
          --acts 0 raised ZeroDivisionError, --acts -3 raised a numpy
          ValueError, --acts 1 silently emitted a one-bin "profile" that just
          duplicates scenes_per_minute, --acts 500 emitted a worst-bin verdict
          drawn from 431 empty bins, and --tolerances accepted a string value
          (numpy UFuncNoLoopError), a negative value, and a changed `kind`
          (ValueError deep inside scalar()).

  TEST 8  NO SILENT RESCALING / NO WRONG REASON TEXT.  A staged timeseries CSV
          shorter than the runtime was stretched to fill normalized time and
          compared as if it covered the whole episode; a palette that exists but
          has a null H reported the reason "no palette in episode B".

Run:  python3 test_compare_guards.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
PIPE = os.path.join(WORK, "pipeline", "compare.py")
EPI = os.path.join(WORK, "episodes")
TMP = tempfile.mkdtemp(prefix="cmp_guard_")

INSUFFICIENT = "INSUFFICIENT"
fails, n_checks = [], 0


def check(name, cond, detail=""):
    global n_checks
    n_checks += 1
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + detail) if detail else ""))
    if not cond:
        fails.append(name)


def jload(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def jdump(o, p):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(o, fh, ensure_ascii=False, indent=1)


def mk(name, base="inca_pix"):
    d = os.path.join(TMP, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(os.path.join(EPI, base), d)
    return d


def run(a, b, out, extra=()):
    o = os.path.join(TMP, out)
    if os.path.exists(o):
        shutil.rmtree(o)
    p = subprocess.run([sys.executable, PIPE, "--a", a, "--b", b, "--out", o] + list(extra),
                       capture_output=True, text=True)
    jp = os.path.join(o, "comparison.json")
    rep = jload(jp) if os.path.exists(jp) else None
    return p, rep, ({m["metric"]: m for m in rep["metrics"]} if rep else {})


# =============================================================================
print("\n=== TEST 6: refusal when a free parameter is UNDECLARED, not just different ===")

# -- warm predicate absent on one side, and the warm numbers genuinely differ
d = mk("warm_nopred")
man = jload(os.path.join(d, "derive_manifest.json"))
man["parameters"].pop("warm_predicate")
jdump(man, os.path.join(d, "derive_manifest.json"))
m = jload(os.path.join(d, "metrics.json"))
for r in m["perMinute"]:
    if r.get("warm") is not None:
        r["warm"] = round(r["warm"] * 0.5, 1)
jdump(m, os.path.join(d, "metrics.json"))
_, _, mm = run(os.path.join(EPI, "inca_pix"), d, "o_warm_nopred")
for k in ("warm_curve", "warm_level", "warm_dynamic_range"):
    check("%s refuses when B declares no warm_predicate" % k,
          mm[k]["classification"] == INSUFFICIENT, "got %s" % mm[k]["classification"])
check("the refusal says the predicate is UNDECLARED, and which side",
      "does not declare a warm_predicate" in (mm["warm_curve"].get("reason") or "") and
      "episode B" in (mm["warm_curve"].get("reason") or ""),
      "%r" % (mm["warm_curve"].get("reason") or "")[:80])

# -- warm predicate absent on BOTH sides with IDENTICAL numbers: the dangerous
#    direction, because it would otherwise read as a positive agreement
da, db = mk("warm_noman_a"), mk("warm_noman_b")
for x in (da, db):
    os.remove(os.path.join(x, "derive_manifest.json"))
_, _, mm = run(da, db, "o_warm_noman")
check("identical warm numbers with no predicate on either side still refuse",
      mm["warm_level"]["classification"] == INSUFFICIENT,
      "got %s (a=%s b=%s) — agreeing here would be a positive claim about a "
      "quantity whose definition is unknown"
      % (mm["warm_level"]["classification"], mm["warm_level"]["a"], mm["warm_level"]["b"]))
check("V and db are NOT collateral damage of the warm refusal",
      mm["V_curve"]["classification"] == "CHANNEL_RULE" and
      mm["db_curve"]["classification"] == "CHANNEL_RULE")

# -- silence threshold absent on one side
d = mk("sil_nothr")
s = jload(os.path.join(d, "silences.json"))
s.pop("threshold_s", None)
s["events"] = s["events"][:5]
jdump(s, os.path.join(d, "silences.json"))
_, _, mm = run(os.path.join(EPI, "inca_pix"), d, "o_sil_nothr")
r = mm["silences_per_10min"]
check("silence rate refuses when B declares no threshold_s",
      r["classification"] == INSUFFICIENT, "got %s" % r["classification"])
check("the label does not assert A's threshold as if it were B's",
      ">=3.0 s" not in (r.get("label") or ""), "label=%r" % r.get("label"))

# -- narration mask convention mismatch
d = mk("mask_other")
s = jload(os.path.join(d, "silences.json"))
s["narration"].pop("sample_time")
jdump(s, os.path.join(d, "silences.json"))
_, _, mm = run(os.path.join(EPI, "inca_pix"), d, "o_mask")
for k in ("narration_coverage", "duck_median_db", "centroid_ratio"):
    check("%s refuses across different caption-mask conventions" % k,
          mm[k]["classification"] == INSUFFICIENT, "got %s" % mm[k]["classification"])

# =============================================================================
print("\n=== TEST 7: CLI argument validation ===")
GOOD = (os.path.join(EPI, "inca_pix"), os.path.join(EPI, "synth_perturbed"))
for n in ("0", "1", "-3", "500"):
    p, _, _ = run(GOOD[0], GOOD[1], "o_acts%s" % n.replace("-", "m"), ["--acts", n])
    check("--acts %s is rejected with a message, not a traceback" % n,
          p.returncode != 0 and p.stderr.strip().startswith("compare.py:"),
          "rc=%d  %r" % (p.returncode, p.stderr.strip().splitlines()[-1][:70] if p.stderr.strip() else ""))
p, _, _ = run(GOOD[0], GOOD[1], "o_acts10", ["--acts", "10"])
check("--acts 10 still works", p.returncode == 0)

tp = os.path.join(TMP, "tol.json")
for name, payload, must_fail in (
        ("a plain numeric override", {"scene_dur_median": {"value": 0.50}}, False),
        ("a string value", {"scene_dur_median": {"value": "loose"}}, True),
        ("a negative value", {"scene_dur_median": {"value": -1.0}}, True),
        ("a changed kind", {"scene_dur_median": {"kind": "pmin", "value": 0.05}}, True),
        ("p >= 1.5", {"transition_mix": {"value": 1.5}}, True),
        ("rho >= 2.0", {"warm_curve": {"value": 2.0}}, True),
        ("overriding the unclassifiable row", {"scene_count": {"value": 1.0}}, True),
        ("an unknown metric", {"nope": {"value": 1}}, True),
        ("a non-object entry", {"scene_dur_median": 0.5}, True)):
    jdump(payload, tp)
    p, _, mm = run(GOOD[0], GOOD[1], "o_tol", ["--tolerances", tp])
    ok = (p.returncode != 0 and p.stderr.strip().startswith("compare.py:")) if must_fail \
        else p.returncode == 0
    check("--tolerances %s -> %s" % (name, "rejected" if must_fail else "accepted"), ok,
          "rc=%d %s" % (p.returncode,
                        (p.stderr.strip().splitlines() or [""])[-1][:70]))
jdump({"scene_dur_median": {"value": 0.50}}, tp)
_, _, mm = run(GOOD[0], GOOD[1], "o_tol_ok", ["--tolerances", tp])
check("an accepted override actually reclassifies, and is stamped",
      mm["scene_dur_median"]["classification"] == "CHANNEL_RULE" and
      mm["scene_dur_p90"]["classification"] == "EPISODE_CHOICE" and
      "[OVERRIDDEN via" in mm["scene_dur_median"]["tolerance"]["justification"])

# =============================================================================
print("\n=== TEST 8: no silent rescaling, no wrong reason text ===")
d = mk("half_csv")
src = os.path.join(EPI, "inca_pix", "timeseries", "timeseries_10fps.csv")
lines = open(src, encoding="utf-8").read().splitlines()
open(os.path.join(d, "timeseries", "timeseries_10fps.csv"), "w", encoding="utf-8").write(
    "\n".join([lines[0]] + lines[1:1 + (len(lines) - 1) // 2]) + "\n")
_, _, mm = run(os.path.join(EPI, "inca_pix"), d, "o_halfcsv")
r = mm["luma_curve"]
check("a CSV covering half the runtime is refused, not stretched to fill it",
      r["classification"] == INSUFFICIENT, "got %s" % r["classification"])
check("the refusal quotes the actual coverage",
      "50" in (r.get("reason") or ""), "%r" % (r.get("reason") or "")[:90])
_, _, mm = run(os.path.join(EPI, "inca_pix"), os.path.join(EPI, "inca_pix"), "o_cov")
det = mm["luma_curve"]["detail"]
check("a full-coverage CSV records its coverage in the detail block",
      abs(det["csv_coverage_of_runtime_a"] - 1.0) < 0.01,
      "coverage=%s" % det["csv_coverage_of_runtime_a"])

d = mk("null_hue")
pal = jload(os.path.join(d, "palette.json"))
pal[0]["H"] = None
jdump(pal, os.path.join(d, "palette.json"))
m = jload(os.path.join(d, "metrics.json"))
m["palette"] = pal
jdump(m, os.path.join(d, "metrics.json"))
_, _, mm = run(os.path.join(EPI, "inca_pix"), d, "o_nullhue")
r = mm["palette_hue_distance"]
check("a palette with a null H does not report 'no palette'",
      r["classification"] == INSUFFICIENT and
      "no palette" not in (r.get("reason") or "") and
      "HAS a palette" in (r.get("reason") or ""),
      "%r" % (r.get("reason") or "")[:90])

# =============================================================================
print("\n%s  %d/%d checks passed" % ("ALL PASS" if not fails else "FAILURES",
                                     n_checks - len(fails), n_checks))
for f in fails:
    print("  FAILED: %s" % f)
sys.exit(1 if fails else 0)
