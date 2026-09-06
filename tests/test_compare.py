#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_compare.py — validation for pipeline/compare.py.

There is one real episode in this container and no source video, so compare.py
is validated by falsification rather than by reproduction:

  TEST 1  SELF-COMPARISON, raw derive.py output (episodes/inca vs itself).
          Every metric that has data on both sides MUST classify as agreeing;
          every metric that is null MUST classify INSUFFICIENT with a stated
          reason.  A single EPISODE_CHOICE here would mean the comparator can
          report a difference where there is provably none.

  TEST 2  SELF-COMPARISON, pixel columns present (episodes/inca_pix vs itself).
          Same assertion, but now the warm / V / palette rows carry real values,
          so the colour and palette comparators are exercised rather than
          short-circuited.

  TEST 3  PERTURBATION (inca_pix vs episodes/synth_perturbed).
          Each deliberate perturbation MUST flip its target row to
          EPISODE_CHOICE, and — just as important — the runtime-normalized rows
          that a uniform time-stretch should NOT move MUST stay consistent.
          A comparator that flags everything is as useless as one that flags
          nothing, so both directions are asserted.

  TEST 4  GUARD. Two episodes whose warm figures were computed with different
          (unrecoverable) warm predicates MUST classify INSUFFICIENT, not
          "consistent" — comparing them at all would be comparing different
          quantities.

  TEST 5  UNIT TESTS of the local statistics against hand-checkable cases,
          since scipy is not available and every p-value here comes from code
          in this repo.

Run:  python3 test_compare.py
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
PIPE = os.path.join(WORK, "pipeline")
EPI = os.path.join(WORK, "episodes")
sys.path.insert(0, PIPE)

import compare as X  # noqa: E402

fails = []
n_checks = 0


def check(name, cond, detail=""):
    global n_checks
    n_checks += 1
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + detail) if detail else ""))
    if not cond:
        fails.append(name)


def run(a, b, out):
    """Invoke compare.py as a subprocess (so the CLI contract is tested too)."""
    if os.path.exists(out):
        shutil.rmtree(out)
    p = subprocess.run([sys.executable, os.path.join(PIPE, "compare.py"),
                        "--a", a, "--b", b, "--out", out],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        raise SystemExit("compare.py failed on %s vs %s" % (a, b))
    with open(os.path.join(out, "comparison.json"), encoding="utf-8") as fh:
        rep = json.load(fh)
    return rep, {m["metric"]: m for m in rep["metrics"]}


TMP = tempfile.mkdtemp(prefix="cmp_tests_")

# =============================================================================
print("\n=== TEST 1: self-comparison, raw derive.py output (episodes/inca) ===")
rep1, m1 = run(os.path.join(EPI, "inca"), os.path.join(EPI, "inca"),
               os.path.join(TMP, "self_raw"))
print("  summary: %s" % {k: v for k, v in rep1["summary"].items()})

bad = [k for k, v in m1.items() if v["classification"] == X.EPISODE_CHOICE]
check("no metric reports a difference between an episode and itself",
      not bad, "offenders: %s" % bad)

# every INSUFFICIENT row must give a reason — silent nulls are the failure mode
noreason = [k for k, v in m1.items()
            if v["classification"] == X.INSUFFICIENT and not v.get("reason")]
check("every INSUFFICIENT row states why", not noreason, "%s" % noreason)

# the rows that DO have data must actually be classified, not skipped
must_have_data = ["scenes_per_minute", "scene_dur_median", "scene_dur_p10",
                  "scene_dur_p90", "scene_dur_shape", "scene_dur_distribution",
                  "scenes_per_min_by_act", "transition_mix", "hard_cut_share",
                  "pass_through_share", "db_curve", "db_level", "luma_curve",
                  "chapters_per_10min", "chapter_len_shape", "chapter_ending_ratio",
                  "silences_per_10min", "silence_positions", "narration_coverage",
                  "duck_median_db", "centroid_ratio", "camera_speed_median",
                  "camera_speed_p90", "camera_speed_distribution",
                  "camera_move_mix", "camera_static_share"]
missing = [k for k in must_have_data
           if m1.get(k, {}).get("classification") != X.CHANNEL_RULE]
check("all %d data-bearing rows classify as consistent" % len(must_have_data),
      not missing, "not consistent: %s" % missing)

# the pixel-dependent rows must be INSUFFICIENT here, not silently agreeing
needs_video = ["warm_curve", "warm_level", "warm_dynamic_range", "V_curve",
               "V_level", "palette_hue_distance", "palette_warm_share"]
wrong = [k for k in needs_video if m1[k]["classification"] != X.INSUFFICIENT]
check("pixel-dependent rows are INSUFFICIENT without a pixel pass",
      not wrong, "%s" % wrong)
check("raw scene count is never classified as agreement",
      m1["scene_count"]["classification"] == X.INSUFFICIENT)

# identity checks on the actual numbers
check("scene_dur_median identical (%s s)" % m1["scene_dur_median"]["a"],
      m1["scene_dur_median"]["a"] == m1["scene_dur_median"]["b"])
check("db_curve spearman == 1.0",
      abs(m1["db_curve"]["detail"]["spearman"] - 1.0) < 1e-9,
      "rho=%s" % m1["db_curve"]["detail"]["spearman"])
check("luma_curve spearman == 1.0",
      abs(m1["luma_curve"]["detail"]["spearman"] - 1.0) < 1e-9,
      "rho=%s" % m1["luma_curve"]["detail"]["spearman"])
check("KS statistic == 0 on identical scene durations",
      m1["scene_dur_distribution"]["detail"]["ks_statistic"] == 0.0)
check("chi-square == 0 on an identical transition mix",
      abs(m1["transition_mix"]["detail"]["chi2"]) < 1e-12)

# honesty contract
check("no row claims a rule is established",
      all(v["established"] is False for v in m1.values()))
check("every CHANNEL_RULE row carries the n=2 wording",
      all(v["verdict_text"] == "consistent across 2 episodes (n=2, not established)"
          for v in m1.values() if v["classification"] == X.CHANNEL_RULE))
check("every classified row declares a tolerance with a justification",
      all(v.get("tolerance") and v["tolerance"].get("justification")
          for v in m1.values() if v["classification"] != X.INSUFFICIENT))
md = open(os.path.join(TMP, "self_raw", "comparison.md"), encoding="utf-8").read()
check("comparison.md never prints the bare phrase 'channel rule' as a verdict",
      "not established" in md and "| channel rule |" not in md.lower())

# =============================================================================
print("\n=== TEST 2: self-comparison with pixel columns (episodes/inca_pix) ===")
rep2, m2 = run(os.path.join(EPI, "inca_pix"), os.path.join(EPI, "inca_pix"),
               os.path.join(TMP, "self_pix"))
print("  summary: %s" % {k: v for k, v in rep2["summary"].items()})
bad2 = [k for k, v in m2.items() if v["classification"] == X.EPISODE_CHOICE]
check("no metric reports a difference between inca_pix and itself",
      not bad2, "offenders: %s" % bad2)
now_live = ["warm_curve", "warm_level", "warm_dynamic_range", "V_curve", "V_level",
            "palette_hue_distance", "palette_warm_share"]
notlive = [k for k in now_live if m2[k]["classification"] != X.CHANNEL_RULE]
check("the 7 colour/palette rows are now exercised and all consistent",
      not notlive, "%s" % notlive)
check("warm_curve spearman == 1.0",
      abs(m2["warm_curve"]["detail"]["spearman"] - 1.0) < 1e-9)
check("palette circular EMD == 0 on an identical palette",
      abs(m2["palette_hue_distance"]["detail"]["circular_emd_degrees"]) < 1e-9)
check("only scene_count and subject_budget remain INSUFFICIENT",
      sorted(k for k, v in m2.items() if v["classification"] == X.INSUFFICIENT)
      == ["scene_count", "subject_budget"],
      "%s" % sorted(k for k, v in m2.items()
                    if v["classification"] == X.INSUFFICIENT))

# =============================================================================
print("\n=== TEST 3: perturbed synthetic episode (inca_pix vs synth_perturbed) ===")
rep3, m3 = run(os.path.join(EPI, "inca_pix"), os.path.join(EPI, "synth_perturbed"),
               os.path.join(TMP, "perturbed"))
print("  summary: %s" % {k: v for k, v in rep3["summary"].items()})

# (a) the perturbations MUST be caught
MUST_FLIP = {
    "scene_dur_median": "scene durations stretched 1.4x",
    "scene_dur_p10": "scene durations stretched 1.4x",
    "scene_dur_p90": "scene durations stretched 1.4x",
    "scenes_per_minute": "same scene count over a 1.4x longer runtime",
    "scenes_per_min_by_act": "scene rate down 1/1.4 in every act",
    "transition_mix": "transition codes A and E swapped",
    "hard_cut_share": "transition codes A and E swapped",
    "warm_curve": "warm curve rolled by half the runtime",
    "camera_speed_median": "camera displacement x3 over a 1.4x longer runtime",
    "camera_speed_p90": "camera displacement x3 over a 1.4x longer runtime",
    "palette_hue_distance": "palette hues rotated +120 deg",
    "db_level": "per-minute dBFS shifted +8 dB",
    # not a bug: 12 chapters over a 1.4x longer runtime IS a lower chapter rate,
    # exactly as 72 scenes over a 1.4x longer runtime is a lower scene rate.
    "chapters_per_10min": "same 12 chapters over a 1.4x longer runtime",
}
for k, why in MUST_FLIP.items():
    got = m3[k]["classification"]
    d = m3[k].get("detail") or {}
    num = next(("%s=%s" % (n, round(d[n], 4)) for n in
                ("rel_diff", "abs_diff", "p_value", "spearman",
                 "circular_emd_degrees", "worst_rel_diff") if d.get(n) is not None), "")
    check("FLIPS: %-26s (%s)" % (k, why), got == X.EPISODE_CHOICE,
          "got %s  %s" % (got, num))

# (b) runtime normalization MUST hold: a uniform time-stretch may not move these
MUST_HOLD = {
    "silence_positions": "silence times stretched uniformly -> tau unchanged",
    "scene_dur_shape": "IQR/median is dimensionless -> unchanged by a stretch",
    "V_curve": "V left untouched; only the time axis was stretched",
    "luma_curve": "luma left untouched; only the time axis was stretched",
    "chapter_len_shape": "chapter lengths scale together -> same normalized CV",
    "chapter_ending_ratio": "ratio of two lengths -> scale-free",
}
for k, why in MUST_HOLD.items():
    got = m3[k]["classification"]
    check("HOLDS: %-26s (%s)" % (k, why), got == X.CHANNEL_RULE, "got %s" % got)

_sp = m3["silence_positions"]["detail"]
check("normalized silence positions are unchanged by the 1.4x time-stretch",
      max(abs(a - b) for a, b in zip(_sp["tau_a"], _sp["tau_b"])) == 0.0 and
      _sp["p_value"] == 1.0,
      "max|dtau|=%g, KS p=%s (D=%.4f is 1/n floating-point noise, not a "
      "difference)" % (max(abs(a - b) for a, b in zip(_sp["tau_a"], _sp["tau_b"])),
                       _sp["p_value"], _sp["ks_statistic"]))
check("warm curve correlation went negative after the half-runtime roll",
      m3["warm_curve"]["detail"]["spearman"] < 0.0,
      "rho=%.3f" % m3["warm_curve"]["detail"]["spearman"])
# A rigid +120 deg rotation of a TWO-LOBE palette costs less than 120 deg,
# because optimal transport re-pairs the lobes rather than rotating each in
# place. Measured here: 72.9 deg. The exact-rotation case is unit-tested on a
# single-lobe histogram in TEST 5 instead.
_emd = m3["palette_hue_distance"]["detail"]["circular_emd_degrees"]
check("palette EMD is far past its 15 deg tolerance after a +120 deg rotation",
      _emd > 15.0 and 60.0 < _emd < 90.0, "EMD=%.1f deg" % _emd)
check("db level difference recovered the +8 dB shift",
      abs(m3["db_level"]["detail"]["abs_diff"] - 8.0) < 0.5,
      "diff=%.2f dB" % m3["db_level"]["detail"]["abs_diff"])
# (c) the documented UNDER-POWER caveats must be demonstrably real. Two rows
#     fail to catch a 1.4x change, exactly as their tolerance text warns. If
#     these ever started passing, the caveats would be overstated and should be
#     rewritten — so they are asserted, not left implicit.
check("KS on scene durations misses a 1.4x stretch (documented low power)",
      m3["scene_dur_distribution"]["classification"] == X.CHANNEL_RULE and
      m3["scene_dur_distribution"]["detail"]["p_value"] > 0.05,
      "p=%.3f at n=%d/%d — the tolerance text says a pass here means "
      "'not caught', not 'same'" % (m3["scene_dur_distribution"]["detail"]["p_value"],
                                    m3["scene_dur_distribution"]["detail"]["n_a"],
                                    m3["scene_dur_distribution"]["detail"]["n_b"]))
check("silence rate test misses a 1.4x rate change (documented low power)",
      m3["silences_per_10min"]["classification"] == X.CHANNEL_RULE and
      m3["silences_per_10min"]["detail"]["p_value"] > 0.05,
      "%.2f vs %.2f per 10 min, p=%.3f at %d+%d events"
      % (m3["silences_per_10min"]["a"], m3["silences_per_10min"]["b"],
         m3["silences_per_10min"]["detail"]["p_value"],
         m3["silences_per_10min"]["detail"]["n_a"],
         m3["silences_per_10min"]["detail"]["n_b"]))

# (d) level and shape must be separable: rolling a curve moves its shape and
#     leaves its mean alone; shifting it does the opposite.
check("rolling the warm curve moved its SHAPE but not its LEVEL",
      m3["warm_curve"]["classification"] == X.EPISODE_CHOICE and
      m3["warm_level"]["classification"] == X.CHANNEL_RULE,
      "rho=%.3f, level %.2f vs %.2f pp"
      % (m3["warm_curve"]["detail"]["spearman"],
         m3["warm_level"]["a"], m3["warm_level"]["b"]))
check("shifting db moved its LEVEL but not its SHAPE",
      m3["db_level"]["classification"] == X.EPISODE_CHOICE and
      m3["db_curve"]["classification"] == X.CHANNEL_RULE,
      "rho=%.4f, level %.2f vs %.2f dBFS"
      % (m3["db_curve"]["detail"]["spearman"],
         m3["db_level"]["a"], m3["db_level"]["b"]))
check("the act-profile SHAPE is preserved by a uniform stretch (rate is not)",
      abs(m3["scenes_per_min_by_act"]["detail"]["shape_spearman"] - 1.0) < 1e-9,
      "shape rho=%.4f while the rate flipped"
      % m3["scenes_per_min_by_act"]["detail"]["shape_spearman"])

check("the runtime mismatch is stated in blocking_notes",
      any("runtimes differ" in n for n in rep3["blocking_notes"]))
check("at least one EPISODE_CHOICE and one CHANNEL_RULE (comparator is not stuck)",
      rep3["summary"]["EPISODE_CHOICE"] > 0 and rep3["summary"]["CHANNEL_RULE"] > 0)

# =============================================================================
print("\n=== TEST 4: guard against comparing incomparable warm figures ===")
rep4, m4 = run(os.path.join(EPI, "inca_pix"), os.path.join(EPI, "inca_pix_otherwarm"),
               os.path.join(TMP, "warmguard"))
for k in ("warm_curve", "warm_level", "warm_dynamic_range"):
    check("%s refuses the comparison (different warm predicate)" % k,
          m4[k]["classification"] == X.INSUFFICIENT,
          "got %s" % m4[k]["classification"])
check("the refusal names the predicate mismatch",
      "warm predicate" in (m4["warm_curve"].get("reason") or ""))
check("V and db are unaffected by the warm guard",
      m4["V_curve"]["classification"] == X.CHANNEL_RULE and
      m4["db_curve"]["classification"] == X.CHANNEL_RULE)
check("the numbers are identical, so this is a REFUSAL not a difference",
      m4["warm_level"]["classification"] != X.EPISODE_CHOICE)

# =============================================================================
print("\n=== TEST 5: the local statistics (no scipy here, so check them) ===")
# chi-square survival against known table values
check("chi2_sf(3.841, 1) ~ 0.05", abs(X.chi2_sf(3.841, 1) - 0.05) < 1e-3,
      "%.5f" % X.chi2_sf(3.841, 1))
check("chi2_sf(11.070, 5) ~ 0.05", abs(X.chi2_sf(11.070, 5) - 0.05) < 1e-3,
      "%.5f" % X.chi2_sf(11.070, 5))
check("chi2_sf(0, 3) == 1", X.chi2_sf(0.0, 3) == 1.0)
# normal tail
check("norm_sf(1.96) ~ 0.025", abs(X.norm_sf(1.96) - 0.025) < 1e-4)
# KS on identical and on disjoint samples
d, p = X.ks_2samp([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
check("KS on identical samples: D=0, p=1", d == 0.0 and p == 1.0)
d, p = X.ks_2samp(list(range(50)), [x + 1000 for x in range(50)])
check("KS on disjoint samples: D=1, p<0.001", d == 1.0 and p < 1e-3, "p=%.2e" % p)
# spearman
check("spearman of a monotone map == 1",
      abs(X.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 500]) - 1.0) < 1e-12)
check("spearman of a reversal == -1",
      abs(X.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) + 1.0) < 1e-12)
check("spearman handles ties (average ranks)",
      abs(X.spearman([1, 1, 2, 3], [1, 1, 2, 3]) - 1.0) < 1e-12)
# two-proportion z
z = X.two_proportion_z(50, 100, 50, 100)
check("two-proportion z on identical proportions: p == 1", abs(z["p"] - 1.0) < 1e-12)
z = X.two_proportion_z(90, 100, 10, 100)
check("two-proportion z on 90% vs 10%: p < 1e-20", z["p"] < 1e-20, "p=%.2e" % z["p"])
# exact binomial
check("binom_test(5,10,0.5) == 1", abs(X.binom_test_two_sided(5, 10, 0.5) - 1.0) < 1e-12)
check("binom_test(10,10,0.5) == 2^-9",
      abs(X.binom_test_two_sided(10, 10, 0.5) - 2 * 0.5 ** 10) < 1e-12)
# circular EMD: a k-bin rotation of a delta must cost exactly k bins
h1 = [0.0] * 36
h2 = [0.0] * 36
h1[0] = 1.0
h2[3] = 1.0
check("circular EMD of a 3-bin rotation == 3 bins",
      abs(X.circular_emd(h1, h2, 36) - 3.0) < 1e-9,
      "%.4f" % X.circular_emd(h1, h2, 36))
h3 = [0.0] * 36
h3[33] = 1.0     # 3 bins the OTHER way round the circle
check("circular EMD takes the short way round the circle",
      abs(X.circular_emd(h1, h3, 36) - 3.0) < 1e-9,
      "%.4f" % X.circular_emd(h1, h3, 36))
# sparse-cell merging in the chi-square
r = X.chi2_homogeneity({"A": 40, "B": 1, "C": 1}, {"A": 40, "B": 1, "C": 1})
check("chi-square merges sparse categories and says so",
      set(r["merged"]) == {"B", "C"}, "merged=%s" % r["merged"])
# resampling
c = X.resample_curve([0.25, 0.75], [0.0, 100.0], 100)
check("resample_curve holds the ends flat instead of extrapolating",
      c[0] == 0.0 and c[-1] == 100.0 and abs(c[49] - 49.0) < 1e-9)
# every declared metric has a tolerance with a justification
nojust = [k for k, v in X.TOLERANCES.items() if not v.get("justification")]
check("all %d declared tolerances carry a written justification" % len(X.TOLERANCES),
      not nojust, "%s" % nojust)
check("the one unclassifiable metric declares WHY it has no tolerance",
      X.TOLERANCES["scene_count"]["kind"] == "none" and
      "never classified" in X.TOLERANCES["scene_count"]["justification"])
emitted = set(m2.keys())
undeclared = emitted - set(X.TOLERANCES)
check("every emitted metric has a declared tolerance", not undeclared, "%s" % undeclared)

# =============================================================================
print("\n%s  %d/%d checks passed" % ("ALL PASS" if not fails else "FAILURES",
                                     n_checks - len(fails), n_checks))
if fails:
    for f in fails:
        print("  FAILED: %s" % f)
print("outputs under %s" % TMP)
sys.exit(1 if fails else 0)
