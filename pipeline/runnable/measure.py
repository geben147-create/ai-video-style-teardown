#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure.py — STAGE 1 of the AI-video style teardown pipeline.

    VIDEO  ->  raw ffmpeg dumps  +  the 11-column 10 fps timeseries

This is the stage that was run ad-hoc in the original session and never saved.
It has been rebuilt from the published artifacts of
/home/user/ai-video-style-teardown by forensic reconstruction; every constant that
could be recovered numerically from those artifacts is hard-coded below with a
citation to the evidence that pinned it.

USAGE
    python3 measure.py <video.(mp4|mkv|...)> --out <dir>
    python3 measure.py --self-test [--out <dir>] [--keep]

OUTPUT (under <dir>)
    raw/scdet_scores.txt              ffmpeg scdet per-frame score, native fps
    raw/signalstats_yavg.txt          ffmpeg signalstats YAVG per frame, native fps
    timeseries/timeseries_10fps.csv   12 columns (t_sec + 11 metrics), 4 decimals
    timeseries/README.json            column dictionary (same schema as published)
    meta.json                         video identity, duration, fps, resolution,
                                      tool versions, and the constants used

DEPENDENCIES
    numpy (only).  Pillow is permitted by the project but is not needed: all pixel
    data arrives as raw rgb24 on an ffmpeg pipe.  No OpenCV, no SciPy — the phase
    correlation is done with numpy's FFT.

MEMORY
    The 60 fps source is NEVER held in RAM.  ffmpeg decodes and downscales to a
    96x54 proxy at 10 fps and the frames are consumed one at a time off a pipe.
    The only multi-frame state is a 33-entry ring of 576-float histograms
    (~76 KB) plus the O(n) output columns (~12 floats/row).  Peak RSS is
    independent of the video's resolution and frame rate.

DETERMINISM
    Same input -> byte-identical CSV.  No timestamps, no RNG, no dict-ordering
    dependence, no threading in the numeric path.  (Verified by --self-test.)

COLUMN STATUS -- how strongly each definition is pinned
    Read this before trusting a number.  NUMERIC = a script reproduced published
    or analytically-known values.  CONTRACT = the semantics come from the
    published column dictionary, but the exact arithmetic was a choice that no
    published artifact can confirm or refute.

    frame_delta           edge rule NUMERIC (forward difference; the published
                          column is empty on the LAST row and README.json gives
                          it n-1 samples).  Per-sample mean|dRGB|: CONTRACT.
    luma_mean             WEAK (downgraded from NUMERIC by adversarial review).
                          Rec.601 0.299/0.587/0.114 is retained as the choice,
                          but an independent re-test against the 196 published
                          frames (recon/adv2/H_luma_coeff.py) puts Rec.601
                          (5.73/255) BEHIND a plain RGB mean (5.54/255), the
                          opposite of the ordering cited from the recon phase,
                          and the paired difference is not significant
                          (t = 0.88, n = 196).  Rec.709 IS refuted (t = 2.81).
                          Treat 601-vs-plain-mean as undetermined.
    luma_spatial_std      CONTRACT (population std of that same luma plane).
    sharpness             CONTRACT (mean gradient magnitude).  Note the ratio
                          below is scale-free, and it is the ratio -- not this
                          column -- that every downstream detector consumes.
    sharpness_dip_ratio   NUMERIC AND UNIQUE.  median over S[i-25:i+25]:
                          11689/11689 published rows within 1e-3, 11210 of them
                          bit-exact at 4 dp; 1 of 121 candidate windows fits.
    ssm_novelty           Kernel half-width NUMERIC (16 rows = +-1.6 s, pinned
                          by the exactly-16 zero rows at each end of the
                          published column).  Histogram binning, cosine SSM and
                          the 2L^2 normalisation: CONTRACT.
    straddle_distance     Edge rule NUMERIC (CLAMPED, not zero-filled: published
                          row 0 = 0.0033 and row n-1 = 0.0001 are both nonzero).
                          LAG: CONTRACT, NOT numeric -- see STRADDLE_LAG below.
                          Histogram: CONTRACT.
    pan_dx / pan_dy       Magnitude AND sign NUMERIC against a synthetic pan of
                          known geometry (predicted 3.0 proxy-px/row, measured
                          3.0).  Published values are integers inside the 96x54
                          FFT wrap limits, as this implementation produces.
    audio_rms_dbfs        Window NUMERIC (1600 samples @16 kHz).  Level NUMERIC
                          (lavfi sine amplitude 0.125 -> analytic -21.0721 dBFS,
                          measured -21.0739).  The epsilon 1.001e-6 is FITTED to
                          one published number: it reproduces the -119.9913
                          digital-silence floor, which a plain 1e-6 does not.
    spectral_centroid_hz  NUMERIC to under 1% on pure tones (440/880/1760 Hz ->
                          0.76/0.24/0.08% error).  Window function
                          (rectangular) and magnitude-vs-power weighting:
                          CONTRACT.

    THE INCA SOURCE VIDEO IS UNAVAILABLE, so no CONTRACT cell above can be
    validated end to end.  Any of them could differ from the original by a
    constant factor or an off-by-one with no published artifact revealing it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

# =============================================================================
# RECOVERED CONSTANTS
# -----------------------------------------------------------------------------
# Each block cites how the value was pinned.  "RECOVERED" means a probe script
# reproduced published numbers; "CONTRACT" means the value is fixed by the
# published column dictionary / README prose but was not independently
# re-derived from data.
# =============================================================================

# --- sampling grid ---------------------------------------------------------
# RECOVERED: data/timeseries/README.json says sample_rate_fps=10 and rows=11689;
# t_sec runs 0.0..1168.8 in exact 0.1 steps (checked: 11689 rows, all 1-decimal).
SAMPLE_FPS = 10
ROW_DT = 1.0 / SAMPLE_FPS

# --- video proxy -----------------------------------------------------------
# RECOVERED: pipeline/rgb.py (surviving) uses W,H,FPS = 96,54,10 with
# scale=...:flags=area and format=rgb24.  Independently corroborated by the
# published pan_dx range [-47,+47] == the phase-correlation wrap limit of a
# 96-px-wide frame, and by README.json's "위상상관 수평 변위 px(96px 폭 기준)".
PROXY_W, PROXY_H = 96, 54

# --- luma ------------------------------------------------------------------
# WEAKLY INDICATED, NOT RECOVERED (corrected by adversarial review).  The recon
# phase reported mean |err| 2.93/255 for Rec.601 vs 3.96 (Rec.709) and 3.05
# (plain RGB mean).  An independent re-test -- scoring each of the 196 published
# assets/frames/sNNN.jpg against the matching shot's `Y` in
# data/detection_pass1_196.json (recon/adv2/H_luma_coeff.py) -- does NOT
# reproduce that ordering: plain RGB mean 5.54/255, Rec.601 5.73, Rec.2020 6.15,
# SMPTE240 6.20, Rec.709 6.28.  Rec.601 vs plain mean is a coin flip
# (paired t = 0.88, n = 196; same ordering on the 34 shortest shots), while
# Rec.709 is genuinely worse (t = 2.81).  So the published data refutes Rec.709
# and cannot separate Rec.601 from a plain mean.  Rec.601 is kept because it is
# the correct luma for SD/BT.601-tagged source, but it is a CHOICE.
LUMA_COEFF = np.array([0.299, 0.587, 0.114], dtype=np.float64)

# --- sharpness_dip_ratio ---------------------------------------------------
# RECOVERED NUMERICALLY IN THIS SESSION.  Both `sharpness` and
# `sharpness_dip_ratio` are published, so the denominator is solvable:
#     dip[i] = sharpness[i] / median(sharpness[i-25 : i+25])
# i.e. a 5.0 s HALF-OPEN window, asymmetric by exactly one sample -- the same
# shape as the +-0.6 s min-window the recon phase recovered for the downstream
# `dip` field.  Probe /home/user/work/recon/dipwin_probe4.py:
#     window S[i-25:i+25] (len 50) -> 11210/11689 bit-exact at 4 dp,
#                                     11689/11689 within 1e-3, max err 1.0e-4
# Sweeping lo in -30..-20 x hi in 20..30 (121 windows), it is the ONLY window
# that puts EVERY row within 1e-3; the runner-up S[i-24:i+24] gets 7084/11689
# bit-exact with max err 1.9e-1, 1900x worse.  (recon/dipwin_probe5.py)
# The residual 479 non-bit-exact rows all have |err| == exactly 1e-4, the
# signature of taking the median of the CSV's already-4dp-rounded `sharpness`
# instead of the full-precision series.
DIP_MEDIAN_LO = -25      # inclusive
DIP_MEDIAN_HI = +25      # exclusive  (Python slice S[i-25:i+25], 50 samples)

# --- ssm_novelty -----------------------------------------------------------
# RECOVERED: README.json says "자기유사도 노벨티(±1.6초 체커보드 커널)" and the
# published column is exactly 0.0000 on the first 16 and last 16 rows
# (nonzero span = rows 16..11672 of 11689).  16 rows @10fps = 1.6 s, so the
# Foote checkerboard kernel spans [i-16, i+16] inclusive (33x33) and novelty is
# only defined where the full kernel fits.
SSM_KERNEL_HALF = 16

# CONTRACT: README.md L190 / index.html 17-1 -- "3x3 공간 분할 컬러
# 히스토그램(576차) 자기유사도 노벨티".  576 = 3x3 blocks x 64 bins, and 64 bins
# = 4x4x4 uniform RGB quantisation.  96/3 == 32 and 54/3 == 18 divide exactly.
HIST_BLOCKS = 3
HIST_LEVELS = 4                                   # per RGB channel -> 4^3 = 64
assert HIST_LEVELS in (2, 4, 8, 16, 32, 64, 128, 256), "HIST_LEVELS must be a power of 2"
HIST_SHIFT = 8 - HIST_LEVELS.bit_length() + 1     # 4 levels -> >>6.  Derived, not
                                                  # hard-coded, so HIST_LEVELS is a
                                                  # real knob (adversarial review).
HIST_BINS_PER_BLOCK = HIST_LEVELS ** 3            # 64
HIST_DIM = HIST_BLOCKS * HIST_BLOCKS * HIST_BINS_PER_BLOCK   # 576

# --- straddle_distance -----------------------------------------------------
# CONTRACT (corrected by adversarial review -- this was previously labelled
# RECOVERED/NUMERIC, which the evidence does not support):
#   * the value 5 comes from README.json's PROSE, "±0.5초 히스토그램 코사인 거리",
#     not from any numeric fit.  Mutation testing (recon/adv2) shows that setting
#     STRADDLE_LAG = 3 still passes --self-test 36/36, --verify-contract 16/16 and
#     recon/validate_measure.py 31/31: nothing in the published data discriminates
#     the lag, so it must not be advertised as numerically recovered.
#   * two attempts to pin it from published data both FAILED
#     (recon/adv2/E_lag_stack.py, recon/adv2/G_lag_onset.py): stacking the
#     published straddle profile over the 37 human-verified hard cuts of
#     scenes_verified.json gives a smooth trapezoid whose half-max width is 9
#     rows, not the sharp 2L = 10-row rectangle this single-pair implementation
#     produces, and on the 5 cuts clean enough to read an onset the implied L
#     scatters over 3..14 (median 7).  The ±0.5 s SCALE is corroborated; the
#     exact lag, and indeed whether the original compared a single pair of
#     histograms at all rather than two window means, is NOT recoverable.
# NUMERIC: row 0 and row n-1 are nonzero in the published CSV (0.0033 / 0.0001),
# so the index is CLAMPED at both ends rather than zero-filled.
STRADDLE_LAG = 5

# --- audio -----------------------------------------------------------------
# CONTRACT: README.json "0.1초 오디오 RMS dBFS" / "0.1초 스펙트럴 센트로이드 Hz".
# 16 kHz mono is the project's stated decode target; 0.1 s -> 1600 samples,
# rfft bin width exactly 10 Hz, Nyquist 8000 Hz.
AUDIO_SR = 16000
AUDIO_WIN = AUDIO_SR // SAMPLE_FPS               # 1600 samples per row

# FITTED (one published value).  The published CSV's digital-silence floor is a
# single distinct value, -119.9913 dBFS, occurring on 35 rows, all of which also
# have spectral_centroid_hz == 0.0000 -> those windows are exactly zero.  So the
# original used dbfs = 20*log10(rms + EPS) with
#     20*log10(EPS) = -119.9913  ->  EPS = 1.001e-6
# A bare 1e-6 gives -120.0000 and does NOT match.  The exact expression the
# original used is not recoverable; this constant reproduces the published floor.
AUDIO_EPS = 1.001e-6

# --- ffmpeg ----------------------------------------------------------------
# PROJECT NOTE: newer ffmpeg removed -vsync; use -fps_mode passthrough.
FPS_MODE = ["-fps_mode", "passthrough"]

CSV_HEADER = ("t_sec,frame_delta,luma_mean,luma_spatial_std,sharpness,"
              "sharpness_dip_ratio,ssm_novelty,straddle_distance,pan_dx,pan_dy,"
              "audio_rms_dbfs,spectral_centroid_hz")


# =============================================================================
# small helpers
# =============================================================================

def _run(cmd, **kw):
    """Run a command, raise with stderr attached on failure."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    if p.returncode != 0:
        raise RuntimeError("command failed (%d): %s\n%s"
                           % (p.returncode, " ".join(cmd),
                              p.stderr.decode("utf-8", "replace")[-4000:]))
    return p.stdout.decode("utf-8", "replace")


def _which(name):
    path = shutil.which(name)
    if not path:
        raise RuntimeError("required tool not found on PATH: %s" % name)
    return path


def ffprobe_info(path):
    """Return a dict of stream/format facts used for meta.json and sizing."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", path])
    j = json.loads(out)
    v = next((s for s in j["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in j["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        raise RuntimeError("no video stream in %s" % path)

    def _rat(s):
        try:
            n, d = s.split("/")
            return float(n) / float(d) if float(d) else 0.0
        except Exception:
            return 0.0

    dur = float(j["format"].get("duration") or v.get("duration") or 0.0)
    return {
        "duration_sec": dur,
        "width": int(v["width"]), "height": int(v["height"]),
        "video_codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "r_frame_rate": v.get("r_frame_rate"),
        "avg_frame_rate": v.get("avg_frame_rate"),
        "fps": _rat(v.get("avg_frame_rate") or "0/1") or _rat(v.get("r_frame_rate") or "0/1"),
        "has_audio": a is not None,
        "audio_codec": (a or {}).get("codec_name"),
        "audio_sample_rate": int((a or {}).get("sample_rate") or 0) if a else 0,
        "audio_channels": int((a or {}).get("channels") or 0) if a else 0,
        "format_name": j["format"].get("format_name"),
        "size_bytes": int(j["format"].get("size") or 0),
    }


# =============================================================================
# raw dumps (byte-format-identical to data/raw/ffmpeg_*_60fps.txt)
# =============================================================================
#
# The published dumps are literal `metadata=mode=print` output:
#     frame:%-4d pts:%-7s pts_time:%s
#     <key>=<value>
# exactly two lines per frame (140268 lines == 2 x 70134 frames), i.e. the
# original restricted printing with `key=`, otherwise scdet would also have
# emitted `lavfi.scd.time` on detected frames.  We reproduce that by passing
# key= as well, and by letting ffmpeg do the number formatting (scd.score is
# ffmpeg's own %.3f; YAVG is ffmpeg's %g, 0-4 decimals in the published file).

def dump_scdet(video, out_path):
    """Per-frame ffmpeg scdet score at the source frame rate."""
    # threshold=100 => never above threshold => no `lavfi.scd.time` keys, and
    # sc_pass defaults to false so no frames are dropped.  key= restricts the
    # metadata filter to the one line per frame the published dump has.
    vf = ("scdet=threshold=100,"
          "metadata=mode=print:key=lavfi.scd.score:file=" + _esc_file(out_path))
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
          "-i", video, "-an", "-sn", "-dn", "-vf", vf] + FPS_MODE +
         ["-f", "null", "-"])


def dump_signalstats(video, out_path):
    """Per-frame ffmpeg signalstats YAVG (mean Y) at the source frame rate."""
    vf = ("signalstats,"
          "metadata=mode=print:key=lavfi.signalstats.YAVG:file=" + _esc_file(out_path))
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
          "-i", video, "-an", "-sn", "-dn", "-vf", vf] + FPS_MODE +
         ["-f", "null", "-"])


def _esc_file(p):
    """Escape a path for use inside an ffmpeg filter argument."""
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


RAW_LINE_A = re.compile(r"^frame:(\d+)\s+pts:(\S+)\s+pts_time:(\S+)$")
RAW_LINE_B = re.compile(r"^([A-Za-z0-9_.]+)=(-?[0-9.eE+-]+|nan|inf)$")


def check_raw_dump(path):
    """Validate a dump against the published two-line-per-frame contract.

    Returns (n_frames, key, first_pts_time, last_pts_time).
    """
    n = 0
    key = None
    first_t = last_t = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        while True:
            a = f.readline()
            if not a:
                break
            b = f.readline()
            ma = RAW_LINE_A.match(a.rstrip("\n"))
            mb = RAW_LINE_B.match((b or "").rstrip("\n"))
            if not ma or not mb:
                raise AssertionError("raw dump %s: bad record at frame %d:\n%r\n%r"
                                     % (path, n, a, b))
            if int(ma.group(1)) != n:
                raise AssertionError("raw dump %s: frame index out of order at %d" % (path, n))
            if key is None:
                key = mb.group(1)
                first_t = ma.group(3)
            elif mb.group(1) != key:
                raise AssertionError("raw dump %s: mixed metadata keys (%s vs %s)"
                                     % (path, key, mb.group(1)))
            last_t = ma.group(3)
            n += 1
    return n, key, first_t, last_t


# =============================================================================
# per-frame features
# =============================================================================

def block_histogram(rgb_u8):
    """576-dim 3x3-blocked colour histogram of one 96x54 rgb24 frame.

    Each of the 9 spatial blocks gets a 4x4x4 = 64-bin uniform RGB histogram
    (channel >> 6).  Each block histogram is L1-normalised so every block
    contributes equally, then the 9 are concatenated to 576 dims and the whole
    vector is L2-normalised so cosine similarity is a plain dot product.
    """
    q = (rgb_u8 >> HIST_SHIFT).astype(np.int32)        # (H, W, 3) in 0..L-1
    idx = (q[:, :, 0] * HIST_LEVELS + q[:, :, 1]) * HIST_LEVELS + q[:, :, 2]
    h, w = idx.shape
    bh, bw = h // HIST_BLOCKS, w // HIST_BLOCKS
    vec = np.empty(HIST_DIM, dtype=np.float64)
    k = 0
    for by in range(HIST_BLOCKS):
        y0 = by * bh
        y1 = h if by == HIST_BLOCKS - 1 else y0 + bh
        for bx in range(HIST_BLOCKS):
            x0 = bx * bw
            x1 = w if bx == HIST_BLOCKS - 1 else x0 + bw
            blk = idx[y0:y1, x0:x1].ravel()
            cnt = np.bincount(blk, minlength=HIST_BINS_PER_BLOCK).astype(np.float64)
            s = cnt.sum()
            if s > 0:
                cnt /= s
            vec[k:k + HIST_BINS_PER_BLOCK] = cnt
            k += HIST_BINS_PER_BLOCK
    nrm = np.sqrt(np.dot(vec, vec))
    if nrm > 0:
        vec /= nrm
    return vec


def luma_of(rgb_f):
    """Rec.601 luma plane, 0-255 float."""
    return rgb_f[:, :, 0] * LUMA_COEFF[0] + rgb_f[:, :, 1] * LUMA_COEFF[1] + \
        rgb_f[:, :, 2] * LUMA_COEFF[2]


def gradient_energy(Y):
    """`sharpness`: mean gradient magnitude of the luma plane (0-255 units).

    CONTRACT-ONLY definition (README.json: "그래디언트 에너지(고주파 선명도)").
    Forward 1-px differences, evaluated on the common (H-1, W-1) support.
    NOTE: sharpness_dip_ratio is a RATIO of this quantity to its own rolling
    median, so the dip column -- the one every downstream detector actually
    uses -- is invariant to any positive rescaling of this definition.
    """
    gx = Y[:-1, 1:] - Y[:-1, :-1]
    gy = Y[1:, :-1] - Y[:-1, :-1]
    return float(np.sqrt(gx * gx + gy * gy).mean())


def phase_correlate(Yprev, Ycur):
    """Integer (dx, dy) CAMERA displacement in proxy pixels, via numpy FFT.

    Returns the camera move, not the content move:
        pan_dx > 0  <=>  camera pans LEFT -> RIGHT
        pan_dy > 0  <=>  camera tilts DOWNWARD
    which is the convention the published camera.json labels require
    ("좌→우 팬" at cum_x >= +14, "하강 틸트" at cum_y >= +10).  The raw
    phase-correlation peak gives the CONTENT shift, so the sign is negated.
    Asserted end-to-end by --self-test on a synthetic left-to-right pan.
    """
    a = Yprev - Yprev.mean()
    b = Ycur - Ycur.mean()
    Fa = np.fft.rfft2(a)
    Fb = np.fft.rfft2(b)
    R = Fb * np.conj(Fa)
    mag = np.abs(R)
    R = R / (mag + 1e-12)
    corr = np.fft.irfft2(R, s=Yprev.shape)
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    H, W = Yprev.shape
    if py > H // 2:
        py -= H
    if px > W // 2:
        px -= W
    return -int(px), -int(py)


# =============================================================================
# streaming video pass
# =============================================================================

def _foote_kernel(L):
    """Foote checkerboard kernel over lags [-L, +L]; centre row/col are 0."""
    a = np.arange(-L, L + 1)
    s = np.sign(a).astype(np.float64)
    return np.outer(s, s)


def video_pass(video, verbose=True):
    """Decode -> 96x54 rgb24 @10fps and stream the six pixel columns.

    Returns dict of 1-D arrays (length n) plus n.
    """
    W, H = PROXY_W, PROXY_H
    vf = "fps=%d,scale=%d:%d:flags=area,format=rgb24" % (SAMPLE_FPS, W, H)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", video, "-an", "-sn", "-dn", "-vf", vf] + FPS_MODE + \
          ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=1024 * 1024)

    fsz = W * H * 3
    K = _foote_kernel(SSM_KERNEL_HALF)
    KNORM = 2.0 * SSM_KERNEL_HALF * SSM_KERNEL_HALF     # == sum of +weights
    RING = 2 * SSM_KERNEL_HALF + 1                      # 33

    frame_delta, luma_mean, luma_std, sharp = [], [], [], []
    pan_dx, pan_dy = [], []
    novelty, straddle = [], []

    ring = []               # histograms for the most recent RING frames
    head_hists = []         # first (STRADDLE_LAG+1) histograms, for the clamped head
    prev_rgb = None
    prev_Y = None
    i = 0
    try:
        while True:
            buf = proc.stdout.read(fsz)
            if len(buf) < fsz:
                break
            rgb_u8 = np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
            rgb_f = rgb_u8.astype(np.float64)
            Y = luma_of(rgb_f)

            luma_mean.append(float(Y.mean()))
            luma_std.append(float(Y.std()))
            sharp.append(gradient_energy(Y))

            if prev_rgb is None:
                pan_dx.append(0)
                pan_dy.append(0)
            else:
                # frame_delta[i-1] = mean |rgb[i] - rgb[i-1]| (FORWARD difference:
                # the published column is empty on the LAST row and README.json
                # gives it 11688 samples against 11689 rows).
                frame_delta.append(float(np.abs(rgb_f - prev_rgb).mean()))
                dx, dy = phase_correlate(prev_Y, Y)
                pan_dx.append(dx)
                pan_dy.append(dy)
            prev_rgb = rgb_f
            prev_Y = Y

            hv = block_histogram(rgb_u8)
            ring.append(hv)
            # keep the first 2*LAG+1 histograms so the CLAMPED head rows
            # (j = 0..LAG-1, which need histogram j+LAG, i.e. up to index 2*LAG-1)
            # can be finished after the stream ends.
            if len(head_hists) < 2 * STRADDLE_LAG + 1:
                head_hists.append(hv)
            if len(ring) > RING:
                ring.pop(0)

            # ---- straddle_distance for frame j = i - STRADDLE_LAG ----------
            # needs histograms j-5 and j+5 == ring[-11] and ring[-1]
            if len(ring) >= 2 * STRADDLE_LAG + 1:
                straddle.append(1.0 - float(np.dot(ring[-(2 * STRADDLE_LAG + 1)], ring[-1])))

            # ---- ssm_novelty for frame j = i - SSM_KERNEL_HALF -------------
            if len(ring) == RING:
                Hm = np.asarray(ring)                 # (33, 576), L2-normalised
                S = Hm @ Hm.T                         # cosine SSM band
                novelty.append(max(0.0, float((K * S).sum()) / KNORM))

            i += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
        proc.wait()
    if i == 0:
        raise RuntimeError("no frames decoded from %s\n%s" % (video, err[-2000:]))
    n = i

    # ---- head/tail fill ---------------------------------------------------
    # The stream emitted straddle for j = LAG .. n-1-LAG only.  Both ends are
    # CLAMPED, not zero-filled: the published row 0 (0.0033) and row n-1
    # (0.0001) are nonzero, so the original clamped the index into [0, n-1].
    LAG = STRADDLE_LAG
    if n <= 2 * LAG + 1:
        # whole clip fits in head_hists -> compute directly, no assembly needed
        straddle = [1.0 - float(np.dot(head_hists[max(0, j - LAG)],
                                       head_hists[min(n - 1, j + LAG)]))
                    for j in range(n)]
    else:
        head_pad = [1.0 - float(np.dot(head_hists[max(0, j - LAG)],
                                       head_hists[j + LAG]))
                    for j in range(LAG)]
        # `ring` ends at frame n-1; frame f sits at index len(ring)-1-((n-1)-f)
        base = len(ring) - 1 - (n - 1)
        tail_pad = [1.0 - float(np.dot(ring[base + (j - LAG)], ring[len(ring) - 1]))
                    for j in range(n - LAG, n)]
        straddle = head_pad + straddle + tail_pad
    assert len(straddle) == n, "straddle assembly produced %d rows, expected %d" % (
        len(straddle), n)

    # novelty: zero outside the span where the full +-1.6 s kernel fits.
    novelty = [0.0] * min(SSM_KERNEL_HALF, n) + novelty
    novelty = novelty[:n] + [0.0] * max(0, n - len(novelty))

    return {
        "n": n,
        "frame_delta": np.array(frame_delta, dtype=np.float64),   # length n-1
        "luma_mean": np.array(luma_mean, dtype=np.float64),
        "luma_spatial_std": np.array(luma_std, dtype=np.float64),
        "sharpness": np.array(sharp, dtype=np.float64),
        "ssm_novelty": np.array(novelty, dtype=np.float64),
        "straddle_distance": np.array(straddle, dtype=np.float64),
        "pan_dx": np.array(pan_dx, dtype=np.float64),
        "pan_dy": np.array(pan_dy, dtype=np.float64),
        "ffmpeg_stderr": err,
    }


def dip_ratio(sharpness):
    """sharpness_dip_ratio = sharpness[i] / median(sharpness[i-25 : i+25]).

    RECOVERED window; see DIP_MEDIAN_LO/HI above (11689/11689 rows within 1e-3
    of the published column, 11210 bit-exact at 4 dp).
    """
    n = len(sharpness)
    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        lo = max(0, i + DIP_MEDIAN_LO)
        hi = min(n, i + DIP_MEDIAN_HI)
        m = float(np.median(sharpness[lo:hi]))
        out[i] = sharpness[i] / m if m > 0 else 0.0
    return out


# =============================================================================
# streaming audio pass
# =============================================================================

def audio_pass(video, n_rows, has_audio, verbose=True):
    """0.1 s RMS (dBFS) and spectral centroid (Hz) from 16 kHz mono."""
    rms = np.zeros(n_rows, dtype=np.float64)
    cen = np.zeros(n_rows, dtype=np.float64)

    if not has_audio:
        # No audio track: every window is digital silence.
        rms[:] = 20.0 * np.log10(AUDIO_EPS)
        return rms, cen, 0

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", video, "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", str(AUDIO_SR),
           "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=1024 * 1024)
    freqs = np.fft.rfftfreq(AUDIO_WIN, d=1.0 / AUDIO_SR)   # 0..8000 Hz, 10 Hz bins
    nbytes = AUDIO_WIN * 2
    i = 0
    total = 0
    try:
        while i < n_rows:
            buf = proc.stdout.read(nbytes)
            if not buf:
                break
            x = np.frombuffer(buf, dtype="<i2").astype(np.float64) / 32768.0
            total += len(x)
            if len(x) < AUDIO_WIN:                       # zero-pad the last window
                x = np.concatenate([x, np.zeros(AUDIO_WIN - len(x))])
            r = float(np.sqrt(np.mean(x * x)))
            rms[i] = 20.0 * np.log10(r + AUDIO_EPS)
            X = np.abs(np.fft.rfft(x))                   # rectangular window
            s = float(X.sum())
            cen[i] = float(np.dot(freqs, X) / s) if s > 0 else 0.0
            i += 1
        # drain anything past n_rows so ffmpeg can exit cleanly
        while proc.stdout.read(1 << 20):
            pass
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.stderr.read()
        proc.stderr.close()
        proc.wait()
    if i < n_rows:                                       # audio shorter than video
        rms[i:] = 20.0 * np.log10(AUDIO_EPS)
        cen[i:] = 0.0
    return rms, cen, total


# =============================================================================
# CSV / README / meta writers
# =============================================================================

def _f4(v):
    """4-decimal field, with -0.0000 normalised to 0.0000 (published CSV has none)."""
    s = "%.4f" % (v + 0.0)
    return "0.0000" if s == "-0.0000" else s


def write_csv(path, cols, n):
    """Write the 12-column CSV. t_sec is built from integers so the 0.1 grid is exact."""
    fd = cols["frame_delta"]
    lines = [CSV_HEADER]
    for i in range(n):
        t = "%d.%d" % (i // SAMPLE_FPS, i % SAMPLE_FPS)
        # frame_delta is a FORWARD difference -> undefined (empty) on the last row
        fdv = _f4(fd[i]) if i < len(fd) else ""
        lines.append(",".join((
            t, fdv,
            _f4(cols["luma_mean"][i]),
            _f4(cols["luma_spatial_std"][i]),
            _f4(cols["sharpness"][i]),
            _f4(cols["sharpness_dip_ratio"][i]),
            _f4(cols["ssm_novelty"][i]),
            _f4(cols["straddle_distance"][i]),
            _f4(cols["pan_dx"][i]),
            _f4(cols["pan_dy"][i]),
            _f4(cols["audio_rms_dbfs"][i]),
            _f4(cols["spectral_centroid_hz"][i]),
        )))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


# Same schema, key order and Korean descriptions as the published README.json.
README_COLUMNS = [
    ("frame_delta",          "0.1초 간격 프레임 간 평균 RGB 차이"),
    ("luma_mean",            "프레임 평균 휘도 0-255"),
    ("luma_spatial_std",     "프레임 내 휘도 표준편차(공간 대비)"),
    ("sharpness",            "그래디언트 에너지(고주파 선명도)"),
    ("sharpness_dip_ratio",  "국소 선명도 / 주변 중앙값 — 디졸브 판별 지표"),
    ("ssm_novelty",          "자기유사도 노벨티(±1.6초 체커보드 커널)"),
    ("straddle_distance",    "±0.5초 히스토그램 코사인 거리 — 장면 확정 지표"),
    ("pan_dx",               "위상상관 수평 변위 px(96px 폭 기준)"),
    ("pan_dy",               "위상상관 수직 변위 px"),
    ("audio_rms_dbfs",       "0.1초 오디오 RMS dBFS"),
    ("spectral_centroid_hz", "0.1초 스펙트럴 센트로이드 Hz"),
]


def write_readme(path, n):
    doc = {"sample_rate_fps": SAMPLE_FPS, "rows": n, "columns": []}
    for name, desc in README_COLUMNS:
        doc["columns"].append({
            "column": name,
            "description": desc,
            "samples": n - 1 if name == "frame_delta" else n,
        })
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")


def tool_versions():
    def first(cmd):
        try:
            return _run(cmd).splitlines()[0].strip()
        except Exception as e:
            return "unavailable (%s)" % e
    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "ffmpeg": first(["ffmpeg", "-hide_banner", "-version"]),
        "ffprobe": first(["ffprobe", "-hide_banner", "-version"]),
    }


def write_meta(path, video, info, n, raw_stats, audio_samples,
               decode_check=None, decode_stderr=""):
    meta = {
        "video": {
            "path": os.path.abspath(video),
            "id": os.path.splitext(os.path.basename(video))[0],
            "size_bytes": info["size_bytes"],
            "format_name": info["format_name"],
            "duration_sec": round(info["duration_sec"], 6),
            "fps": round(info["fps"], 6),
            "r_frame_rate": info["r_frame_rate"],
            "avg_frame_rate": info["avg_frame_rate"],
            "width": info["width"], "height": info["height"],
            "video_codec": info["video_codec"], "pix_fmt": info["pix_fmt"],
            "has_audio": info["has_audio"],
            "audio_codec": info["audio_codec"],
            "audio_sample_rate": info["audio_sample_rate"],
            "audio_channels": info["audio_channels"],
        },
        "timeseries": {
            "sample_rate_fps": SAMPLE_FPS,
            "rows": n,
            "t_first": 0.0,
            "t_last": round((n - 1) / SAMPLE_FPS, 1),
            "audio_samples_16k_mono": audio_samples,
            "decode_check": decode_check,
            "decode_stderr": decode_stderr,
        },
        "raw_dumps": raw_stats,
        "constants": {
            "proxy_wh": [PROXY_W, PROXY_H],
            "proxy_scaler": "area",
            "luma": "rec601 0.299/0.587/0.114",
            "dip_median_window_rows": [DIP_MEDIAN_LO, DIP_MEDIAN_HI],
            "dip_median_window_sec": (DIP_MEDIAN_HI - DIP_MEDIAN_LO) / SAMPLE_FPS,
            "ssm_kernel_half_rows": SSM_KERNEL_HALF,
            "ssm_kernel_half_sec": SSM_KERNEL_HALF / SAMPLE_FPS,
            "hist_dim": HIST_DIM,
            "hist_blocks": "%dx%d" % (HIST_BLOCKS, HIST_BLOCKS),
            "hist_bins_per_block": HIST_BINS_PER_BLOCK,
            "straddle_lag_rows": STRADDLE_LAG,
            "straddle_lag_sec": STRADDLE_LAG / SAMPLE_FPS,
            "audio_sample_rate": AUDIO_SR,
            "audio_window_samples": AUDIO_WIN,
            "audio_dbfs_epsilon": AUDIO_EPS,
            "pan_sign": "pan_dx>0 = camera pans left->right; pan_dy>0 = camera tilts down",
        },
        "tools": tool_versions(),
    }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    return meta


# =============================================================================
# driver
# =============================================================================

def stream_extents(path):
    """Last packet pts_time per stream kind, by DEMUX ONLY (no decoding).

    Cheap (~0.35 s for 10 800 packets, ~2 s for the 70 134-frame Inca source) and
    it is the only reference that distinguishes the two ways a video pass can come
    up short:
      * the container claims N seconds but NO stream has packets that far  -> the
        file itself is truncated / corrupt;
      * the VIDEO stream is short but another stream reaches the container
        duration                                                          -> a
        legitimately short video track (e.g. a long audio tail), not an error.
    Returns {"video": t or None, "audio": t or None}.
    """
    out = {}
    for kind, sel in (("video", "v"), ("audio", "a")):
        try:
            txt = _run(["ffprobe", "-v", "error", "-select_streams", sel,
                        "-show_entries", "packet=pts_time", "-of", "csv=p=0", path])
        except RuntimeError:
            out[kind] = None
            continue
        vals = [l.strip().rstrip(",") for l in txt.splitlines() if l.strip().rstrip(",")]
        good = []
        for v in vals[-4096:]:
            try:
                good.append(float(v))
            except ValueError:
                pass
        out[kind] = max(good) if good else None
    return out


def _slack(nominal):
    """Rows/seconds of tolerance: 2 units or 0.5%, whichever is larger."""
    return max(2.0, 0.005 * nominal)


def check_decode_complete(info, extents, n_rows, stderr_text, allow_short, log):
    """Fail loudly when the produced timeseries does not cover the whole video.

    Without this a truncated or corrupt source makes ffmpeg exit 0 after emitting
    only part of the stream, and the pipeline writes a short-but-perfectly-formed
    CSV whose row count silently contradicts the duration recorded three keys away
    in meta.json.  That is exactly the "plausible garbage" failure this stage must
    not have.  Two independent checks, because they catch different faults.
    """
    rep = {"container_duration_sec": info["duration_sec"],
           "video_packet_extent_sec": extents.get("video"),
           "audio_packet_extent_sec": extents.get("audio"),
           "rows": n_rows, "problems": []}
    dur = info["duration_sec"] or 0.0
    vext = extents.get("video")
    aext = extents.get("audio")
    reach = max([x for x in (vext, aext) if x is not None] or [0.0])

    # (A) file truncation: no stream at all reaches the advertised duration.
    if dur > 0 and reach > 0 and reach < dur - _slack(dur) / SAMPLE_FPS - 0.5:
        rep["problems"].append(
            "container advertises %.3f s but no stream has packets past %.3f s "
            "(%.1f%%) -- the file is truncated or corrupt" % (dur, reach, 100 * reach / dur))

    # (B) decoder bail-out: fewer rows than the demuxable video actually spans.
    if vext is not None and vext > 0:
        want = int(round(vext * SAMPLE_FPS)) + 1
        if n_rows < want - _slack(want):
            rep["problems"].append(
                "video packets span %.3f s (~%d rows) but only %d rows decoded "
                "(%.1f%%) -- the decoder stopped early"
                % (vext, want, n_rows, 100.0 * n_rows / want))

    rep["ok"] = not rep["problems"]
    if rep["problems"]:
        msg = "decode-completeness check FAILED:\n  - " + "\n  - ".join(rep["problems"])
        if stderr_text.strip():
            msg += "\nffmpeg said:\n" + stderr_text.strip()[-2000:]
        if not allow_short:
            raise RuntimeError(msg + "\nRe-run with --allow-short to accept a partial "
                                     "decode (the CSV then covers only the decoded part).")
        log("[measure] WARNING " + msg)
    return rep


def measure(video, outdir, want_raw=True, verbose=True, allow_short=False):
    _which("ffmpeg"); _which("ffprobe")
    outdir = os.path.abspath(outdir)
    os.makedirs(os.path.join(outdir, "raw"), exist_ok=True)
    os.makedirs(os.path.join(outdir, "timeseries"), exist_ok=True)

    info = ffprobe_info(video)
    log = (lambda *a: print(*a, file=sys.stderr)) if verbose else (lambda *a: None)
    extents = stream_extents(video)
    log("[measure] %s  %dx%d  %.3fs  %.4f fps  audio=%s"
        % (os.path.basename(video), info["width"], info["height"],
           info["duration_sec"], info["fps"], info["has_audio"]))

    raw_stats = {}
    if want_raw:
        p1 = os.path.join(outdir, "raw", "scdet_scores.txt")
        p2 = os.path.join(outdir, "raw", "signalstats_yavg.txt")
        log("[measure] raw dump 1/2  scdet ...")
        dump_scdet(video, p1)
        log("[measure] raw dump 2/2  signalstats ...")
        dump_signalstats(video, p2)
        for tag, p in (("scdet_scores.txt", p1), ("signalstats_yavg.txt", p2)):
            nf, key, t0, t1 = check_raw_dump(p)
            raw_stats[tag] = {"frames": nf, "key": key,
                              "pts_time_first": t0, "pts_time_last": t1,
                              "bytes": os.path.getsize(p)}
            log("[measure]   %s -> %d frames, key=%s, %s..%s" % (tag, nf, key, t0, t1))

    log("[measure] video pass (96x54 @10fps, streamed) ...")
    cols = video_pass(video, verbose)
    n = cols["n"]
    log("[measure]   %d rows (%.1f s)" % (n, (n - 1) / SAMPLE_FPS))
    decode_check = check_decode_complete(info, extents, n, cols["ffmpeg_stderr"],
                                          allow_short, log)
    if cols["ffmpeg_stderr"].strip():
        log("[measure] NOTE ffmpeg wrote to stderr during the video pass:\n%s"
            % cols["ffmpeg_stderr"].strip()[-2000:])

    cols["sharpness_dip_ratio"] = dip_ratio(cols["sharpness"])

    log("[measure] audio pass (16 kHz mono, streamed) ...")
    rms, cen, nsamp = audio_pass(video, n, info["has_audio"], verbose)
    cols["audio_rms_dbfs"] = rms
    cols["spectral_centroid_hz"] = cen

    csv_path = os.path.join(outdir, "timeseries", "timeseries_10fps.csv")
    write_csv(csv_path, cols, n)
    write_readme(os.path.join(outdir, "timeseries", "README.json"), n)
    meta = write_meta(os.path.join(outdir, "meta.json"), video, info, n,
                      raw_stats, nsamp, decode_check,
                      cols["ffmpeg_stderr"].strip()[-2000:])
    log("[measure] wrote %s (%d bytes)" % (csv_path, os.path.getsize(csv_path)))
    return {"outdir": outdir, "csv": csv_path, "n": n, "cols": cols, "meta": meta}


# =============================================================================
# self-test
# =============================================================================

SELF_TEST_PLAN = {
    # (t0, t1) in seconds
    "seg_warm_a":  (0.0, 2.0),
    "seg_cool":    (2.0, 4.0),
    "seg_fade":    (4.0, 6.0),     # cool texture fading to black
    "seg_pan":     (6.0, 9.0),     # camera pans LEFT -> RIGHT over a static texture
    "seg_warm_b":  (9.0, 12.0),
    "cuts":        (2.0, 9.0),     # hard cuts (4.0 and 6.0 are soft/continuous)
    "tone_440":    (0.0, 3.0),
    "silence":     (3.0, 5.0),
    "tone_880":    (5.0, 8.0),
    "tone_1760":   (8.0, 12.0),
}

_TEX_WARM = "geq=r='120+90*sin(X/5)':g='60+40*sin(Y/6)':b='40+30*cos((X-Y)/8)'"
_TEX_COOL = "geq=r='40+30*cos(Y/7)':g='90+50*sin(X/4)':b='140+80*sin((X+Y)/6)'"
_TEX_PAN  = "geq=r='128+100*sin(X/9)':g='128+100*sin(Y/7)':b='128+100*cos((X+Y)/11)'"


def make_synthetic(path):
    """Build the self-test clip entirely with ffmpeg lavfi sources.

    12 s, 30 fps, 320x180, lossless 16 kHz mono PCM audio:
      0-2 s  warm static texture
      2-4 s  cool static texture           <- HARD CUT at 2.0 s
      4-6 s  same cool texture fading to black (luma sweep + sharpness collapse)
      6-9 s  320x180 window sliding RIGHT across a 640x360 static texture
             == camera pans LEFT -> RIGHT   (pins the pan_dx sign)
      9-12 s warm static texture           <- HARD CUT at 9.0 s
    audio:
      0-3 s  440 Hz  |  3-5 s digital silence  |  5-8 s 880 Hz  |  8-12 s 1760 Hz
    440/880/1760 Hz are exact multiples of the 10 Hz rfft bin width and complete
    an integer number of cycles in every 1600-sample window, so the expected
    centroid is the tone frequency itself with no leakage.
    """
    v = "color=c=black:s=320x180:r=30:d=%g,format=gbrp,%s"
    src = [
        v % (2, _TEX_WARM),
        v % (2, _TEX_COOL),
        (v % (2, _TEX_COOL)) + ",fade=t=out:st=0:d=2",
        ("color=c=black:s=640x360:r=30:d=3,format=gbrp,%s,"
         "crop=320:180:x='min(300,floor(t*100))':y=90" % _TEX_PAN),
        v % (3, _TEX_WARM),
    ]
    aud = [
        "sine=frequency=440:sample_rate=16000:duration=3",
        "anullsrc=r=16000:cl=mono:d=2",
        "sine=frequency=880:sample_rate=16000:duration=3",
        "sine=frequency=1760:sample_rate=16000:duration=4",
    ]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for s in src:
        cmd += ["-f", "lavfi", "-i", s]
    for s in aud:
        cmd += ["-f", "lavfi", "-i", s]
    fc = ("[0:v][1:v][2:v][3:v][4:v]concat=n=5:v=1:a=0[v];"
          "[5:a][6:a][7:a][8:a]concat=n=4:v=0:a=1[a]")
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-r", "30", "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", path]
    _run(cmd)
    return path


def _bruteforce_banded(clip):
    """Reference implementation of straddle_distance / ssm_novelty.

    Stores every histogram and builds the full n x n self-similarity matrix.
    O(n^2) memory -- only ever used on the short self-test clip.
    """
    W, H = PROXY_W, PROXY_H
    vf = "fps=%d,scale=%d:%d:flags=area,format=rgb24" % (SAMPLE_FPS, W, H)
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                          "-i", clip, "-an", "-vf", vf] + FPS_MODE +
                         ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    fsz = W * H * 3
    n = len(raw) // fsz
    Hs = np.array([block_histogram(np.frombuffer(raw[i * fsz:(i + 1) * fsz],
                                                 np.uint8).reshape(H, W, 3))
                   for i in range(n)])
    L = STRADDLE_LAG
    st = np.array([1.0 - float(np.dot(Hs[max(0, i - L)], Hs[min(n - 1, i + L)]))
                   for i in range(n)])
    K = _foote_kernel(SSM_KERNEL_HALF)
    KL = SSM_KERNEL_HALF
    S = Hs @ Hs.T
    nv = np.zeros(n)
    for i in range(KL, n - KL):
        nv[i] = max(0.0, float((K * S[i - KL:i + KL + 1, i - KL:i + KL + 1]).sum())
                    / (2.0 * KL * KL))
    return st, nv


class _Checks:
    def __init__(self):
        self.rows = []
        self.failed = 0

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        if not ok:
            self.failed += 1
        return bool(ok)

    def table(self):
        w = max(len(r[0]) for r in self.rows)
        out = ["| %-*s | %-4s | %s" % (w, "check", "res", "measured"),
               "|-%s-|------|%s" % ("-" * w, "-" * 40)]
        for name, ok, det in self.rows:
            out.append("| %-*s | %-4s | %s" % (w, name, "PASS" if ok else "FAIL", det))
        return "\n".join(out)


def _published_silence_floor(repo):
    """The single distinct audio_rms_dbfs value below -100 in the published CSV.

    Returns None if the repo/column is unavailable; raises if the column is
    present but does NOT have exactly one digital-silence value (that would mean
    the assumption behind AUDIO_EPS is wrong and the caller must know).
    """
    pub = os.path.join(repo, "data/timeseries/timeseries_10fps.csv")
    if not os.path.exists(pub):
        return None
    import csv as _csv
    vals = set()
    with open(pub, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            v = float(r["audio_rms_dbfs"])
            if v < -100:
                vals.add(round(v, 4))
    if not vals:
        return None
    if len(vals) != 1:
        raise AssertionError("published CSV has %d distinct sub -100 dBFS values (%s); "
                             "the single-epsilon model behind AUDIO_EPS does not hold"
                             % (len(vals), sorted(vals)))
    return vals.pop()


def self_test(outdir, keep=False, ref_repo="/home/user/ai-video-style-teardown"):
    tmp = tempfile.mkdtemp(prefix="measure_selftest_")
    clip = os.path.join(tmp, "synthetic.mkv")
    outdir = os.path.abspath(outdir or os.path.join(tmp, "out"))
    print("[self-test] generating synthetic clip -> %s" % clip, file=sys.stderr)
    make_synthetic(clip)

    r = measure(clip, outdir, want_raw=True, verbose=True)
    n, c = r["n"], r["cols"]
    C = _Checks()
    P = SELF_TEST_PLAN

    def rng(t0, t1):
        return slice(int(round(t0 * SAMPLE_FPS)), int(round(t1 * SAMPLE_FPS)))

    # ---------- A. output contract ----------------------------------------
    txt = open(r["csv"], encoding="utf-8").read()
    lines = txt.split("\n")
    C.check("csv.header_matches_published", lines[0] == CSV_HEADER, lines[0][:60] + "...")
    C.check("csv.trailing_newline", txt.endswith("\n") and lines[-1] == "", repr(txt[-12:]))
    body = [l for l in lines[1:] if l]
    C.check("csv.row_count", len(body) == n, "%d rows" % len(body))
    C.check("csv.12_fields_every_row", all(len(l.split(",")) == 12 for l in body), "12")
    empt = [i for i, l in enumerate(body) if "" in l.split(",")]
    C.check("csv.only_last_frame_delta_empty", empt == [n - 1],
            "empty cells on rows %s" % empt)
    dec = set()
    for l in body:
        for v in l.split(",")[1:]:
            if v:
                dec.add(len(v.split(".")[1]))
    C.check("csv.all_metrics_4dp", dec == {4}, "decimal widths %s" % sorted(dec))
    tdec = set(len(l.split(",")[0].split(".")[1]) for l in body)
    C.check("csv.t_sec_1dp", tdec == {1}, "decimal widths %s" % sorted(tdec))
    C.check("csv.no_negative_zero", "-0.0000" not in txt, "none found")

    rd = json.load(open(os.path.join(outdir, "timeseries", "README.json"), encoding="utf-8"))
    C.check("readme.schema", rd["sample_rate_fps"] == 10 and rd["rows"] == n
            and len(rd["columns"]) == 11
            and [x["column"] for x in rd["columns"]] == [k for k, _ in README_COLUMNS],
            "rows=%d cols=%d" % (rd["rows"], len(rd["columns"])))
    C.check("readme.frame_delta_samples", rd["columns"][0]["samples"] == n - 1,
            "%d == rows-1" % rd["columns"][0]["samples"])
    mt = json.load(open(os.path.join(outdir, "meta.json"), encoding="utf-8"))
    C.check("meta.fields", all(k in mt for k in ("video", "timeseries", "raw_dumps",
                                                 "constants", "tools"))
            and mt["video"]["width"] == 320 and mt["timeseries"]["rows"] == n,
            "%dx%d, %d rows" % (mt["video"]["width"], mt["video"]["height"],
                                mt["timeseries"]["rows"]))

    # ---------- B. raw dumps ----------------------------------------------
    for tag in ("scdet_scores.txt", "signalstats_yavg.txt"):
        p = os.path.join(outdir, "raw", tag)
        nf, key, t0, t1 = check_raw_dump(p)
        nl = sum(1 for _ in open(p, encoding="utf-8"))
        C.check("raw.%s.format" % tag, nl == 2 * nf and nf == 360,
                "%d frames, %d lines, key=%s" % (nf, nl, key))
    # cross-format against the published dumps (line shape, not content)
    pubs = [os.path.join(ref_repo, "data/raw/ffmpeg_scdet_scores_60fps.txt"),
            os.path.join(ref_repo, "data/raw/ffmpeg_signalstats_yavg_60fps.txt")]
    if all(os.path.exists(p) for p in pubs):
        ours = [os.path.join(outdir, "raw", "scdet_scores.txt"),
                os.path.join(outdir, "raw", "signalstats_yavg.txt")]
        ok, det = True, []
        for pub, our in zip(pubs, ours):
            with open(pub, encoding="utf-8") as f:
                pa, pb = f.readline().rstrip("\n"), f.readline().rstrip("\n")
            with open(our, encoding="utf-8") as f:
                oa, ob = f.readline().rstrip("\n"), f.readline().rstrip("\n")
            same_shape = (RAW_LINE_A.match(pa) and RAW_LINE_A.match(oa)
                          and pa[:len("frame:0    pts:0       pts_time:")] ==
                          oa[:len("frame:0    pts:0       pts_time:")]
                          and pb.split("=")[0] == ob.split("=")[0])
            ok = ok and bool(same_shape)
            det.append("%s|%s" % (oa, ob))
        C.check("raw.matches_published_byte_shape", ok, " ; ".join(det))

    # ---------- C. detectors fire where expected --------------------------
    fd = c["frame_delta"]
    # frame_delta is a FORWARD difference, so the spike for a cut at time T sits
    # on row round(T*10)-1.  The clip has THREE visual discontinuities: the two
    # hard cuts (2.0 s, 9.0 s) and the black -> pan-texture join at 6.0 s.
    disc = [19, 59, 89]
    top3 = sorted(np.argsort(fd)[-3:].tolist())
    C.check("frame_delta.top3_are_the_three_discontinuities", top3 == disc,
            "argmax rows %s (t=%s) vs expected %s"
            % (top3, [round(x / 10 + 0.1, 1) for x in top3], disc))
    quiet = float(np.median(np.concatenate([fd[2:17], fd[22:57], fd[92:117]])))
    C.check("frame_delta.spikes_dominate_shot_interior",
            all(fd[d] > 20 * quiet for d in disc),
            "spikes %s vs shot-interior median %.4f"
            % ([round(float(fd[d]), 2) for d in disc], quiet))

    st = c["straddle_distance"]
    C.check("straddle.peaks_at_cuts",
            st[rng(1.8, 2.3)].max() > 0.5 and st[rng(8.8, 9.3)].max() > 0.5,
            "cut1 %.3f cut2 %.3f (mid-shot median %.3f)"
            % (st[rng(1.8, 2.3)].max(), st[rng(8.8, 9.3)].max(),
               float(np.median(st[rng(0.6, 1.4)]))))

    nv = c["ssm_novelty"]
    C.check("ssm_novelty.edges_zero",
            np.all(nv[:SSM_KERNEL_HALF] == 0) and np.all(nv[n - SSM_KERNEL_HALF:] == 0)
            and np.any(nv[SSM_KERNEL_HALF:n - SSM_KERNEL_HALF] > 0),
            "first/last %d rows zero, interior max %.4f"
            % (SSM_KERNEL_HALF, nv[SSM_KERNEL_HALF:n - SSM_KERNEL_HALF].max()))
    hard_cut_rows = [int(round(t * SAMPLE_FPS)) for t in P["cuts"]]   # 20, 90
    C.check("ssm_novelty.peaks_at_a_hard_cut",
            min(abs(int(np.argmax(nv)) - r_) for r_ in hard_cut_rows) <= 3,
            "argmax row %d (%.1fs) vs hard cuts %s, value %.4f"
            % (int(np.argmax(nv)), int(np.argmax(nv)) / 10.0, hard_cut_rows, nv.max()))

    lm = c["luma_mean"]
    C.check("luma.fade_to_black",
            lm[rng(4.0, 4.2)].mean() > 60 and lm[rng(5.8, 6.0)].mean() < 12,
            "start %.1f -> end %.1f" % (lm[rng(4.0, 4.2)].mean(), lm[rng(5.8, 6.0)].mean()))

    dp = c["sharpness_dip_ratio"]
    C.check("dip_ratio.collapses_on_fade_out",
            dp[rng(5.5, 6.0)].min() < 0.35 and 0.8 < float(np.median(dp[rng(0.5, 1.5)])) < 1.25,
            "fade min %.3f, steady median %.3f"
            % (dp[rng(5.5, 6.0)].min(), float(np.median(dp[rng(0.5, 1.5)]))))
    C.check("dip_ratio.median_of_flat_series_is_1",
            abs(float(np.median(dp[rng(0.4, 1.6)])) - 1.0) < 0.05,
            "%.4f" % float(np.median(dp[rng(0.4, 1.6)])))

    dx, dy = c["pan_dx"], c["pan_dy"]
    pan = dx[rng(6.3, 8.7)]
    C.check("pan_dx.sign_is_left_to_right_positive",
            float(np.median(pan)) > 0 and (pan > 0).mean() > 0.8,
            "median %.1f px/0.1s, %.0f%% positive" % (float(np.median(pan)), 100 * (pan > 0).mean()))
    # Ground truth: crop x advances 100 source-px/s; the 320-px-wide crop is
    # area-scaled to the 96-px proxy, so 100 * 96/320 = 30 proxy-px/s
    # = 3.0 proxy-px per 0.1 s row.  Phase correlation is integer-valued, so an
    # exact hit is expected.
    C.check("pan_dx.magnitude_equals_geometric_prediction",
            abs(float(np.median(pan)) - 3.0) <= 0.5,
            "median %.1f px/row; predicted 100 px/s * 96/320 / 10 = 3.0"
            % float(np.median(pan)))
    C.check("pan_dx.zero_on_static_shots",
            np.all(dx[rng(0.4, 1.8)] == 0) and np.all(dx[rng(9.4, 11.8)] == 0),
            "static |dx| max %d" % int(max(abs(dx[rng(0.4, 1.8)]).max(),
                                           abs(dx[rng(9.4, 11.8)]).max())))
    C.check("pan_dy.zero_on_pure_horizontal_pan",
            np.all(dy[rng(6.3, 8.7)] == 0), "|dy| max %d" % int(abs(dy[rng(6.3, 8.7)]).max()))

    ar = c["audio_rms_dbfs"]
    sil = ar[rng(3.3, 4.7)]
    ton = ar[rng(0.3, 2.7)]
    # NOTE (adversarial review): comparing our floor to 20*log10(AUDIO_EPS) is
    # tautological -- it is the same constant that produced the number, so it
    # cannot discriminate AUDIO_EPS at all.  Cross-check against the PUBLISHED
    # floor instead, which is what actually pins the constant.
    pub_floor = _published_silence_floor(ref_repo)
    C.check("audio.silence_at_floor",
            sil.max() < -100 and abs(sil.min() - 20 * np.log10(AUDIO_EPS)) < 1e-6,
            "silence %.4f..%.4f dBFS, floor const %.4f"
            % (sil.min(), sil.max(), 20 * np.log10(AUDIO_EPS)))
    if pub_floor is not None:
        C.check("audio.silence_floor_matches_published",
                "%.4f" % sil.min() == "%.4f" % pub_floor,
                "ours %.4f vs published Inca floor %.4f (eps=%g)"
                % (sil.min(), pub_floor, AUDIO_EPS))
    # ffmpeg's `sine` source defaults to peak amplitude 0.125, i.e.
    # rms = 0.125/sqrt(2) = 0.0883883 -> 20*log10 = -21.0721 dBFS.
    # (Measured directly off a raw lavfi sine: peak 0.1249695, rms 0.0883687,
    #  -21.0740 dBFS -- s16 quantisation accounts for the 0.002 dB.)
    C.check("audio.tone_level_matches_lavfi_sine_amplitude",
            abs(ton.mean() - (-21.074)) < 0.15,
            "%.4f dBFS vs -21.074 predicted from amplitude 0.125" % ton.mean())

    cn = c["spectral_centroid_hz"]
    for lbl, (t0, t1), f0 in (("440", (0.3, 2.7), 440.0),
                              ("880", (5.3, 7.7), 880.0),
                              ("1760", (8.3, 11.7), 1760.0)):
        m = float(np.median(cn[rng(t0, t1)]))
        C.check("centroid.%sHz_tone" % lbl, abs(m - f0) <= 0.02 * f0,
                "measured %.2f Hz vs %.0f Hz (%.2f%% err)" % (m, f0, 100 * abs(m - f0) / f0))
    C.check("centroid.zero_in_silence", np.all(cn[rng(3.3, 4.7)] == 0.0),
            "max %.4f" % cn[rng(3.3, 4.7)].max())

    # ---------- D. streaming vs brute force -------------------------------
    # The 33-entry histogram ring is the only clever part of the video pass.
    # Re-decode the clip, keep EVERY histogram, and recompute the two banded
    # columns the naive way (full n x n SSM); they must agree exactly.
    ref_st, ref_nv = _bruteforce_banded(clip)
    C.check("streaming.straddle_equals_bruteforce",
            np.abs(c["straddle_distance"] - ref_st).max() < 1e-12,
            "max |stream - bruteforce| = %.3e"
            % np.abs(c["straddle_distance"] - ref_st).max())
    C.check("streaming.novelty_equals_bruteforce",
            np.abs(c["ssm_novelty"] - ref_nv).max() < 1e-9,
            "max |stream - bruteforce| = %.3e"
            % np.abs(c["ssm_novelty"] - ref_nv).max())

    # ---------- E. determinism --------------------------------------------
    out2 = os.path.join(tmp, "out2")
    measure(clip, out2, want_raw=False, verbose=False)
    a = open(r["csv"], "rb").read()
    b = open(os.path.join(out2, "timeseries", "timeseries_10fps.csv"), "rb").read()
    C.check("determinism.csv_byte_identical", a == b,
            "%d bytes, sha equal=%s" % (len(a), a == b))

    print("\n=== SELF-TEST RESULTS =========================================", file=sys.stderr)
    print(C.table())
    print("\n%d/%d checks passed" % (len(C.rows) - C.failed, len(C.rows)))
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("[self-test] artefacts kept in %s and %s" % (tmp, outdir), file=sys.stderr)
    return C.failed == 0


def verify_contract(repo, produced_csv=None):
    """Check this module against the PUBLISHED Inca artifacts.

    Three things are checkable without the source video:
      1. the CSV header / field-shape contract,
      2. the raw-dump line format,
      3. `sharpness_dip_ratio` -- because both `sharpness` and
         `sharpness_dip_ratio` are published, dip_ratio() can be re-run on the
         published `sharpness` column and scored against the published dip
         column.  This is the one column formula that is NUMERICALLY validated
         against the real artifact rather than only contract-validated.
    """
    pub_csv = os.path.join(repo, "data/timeseries/timeseries_10fps.csv")
    pub_rd = os.path.join(repo, "data/timeseries/README.json")
    rows = []

    with open(pub_csv, encoding="utf-8") as f:
        head = f.readline().rstrip("\n")
    rows.append(("published header == CSV_HEADER", head == CSV_HEADER, head[:48] + "..."))

    txt = open(pub_csv, encoding="utf-8").read()
    body = [l for l in txt.split("\n")[1:] if l]
    ncols = set(len(l.split(",")) for l in body)
    rows.append(("published rows are 12-field", ncols == {12}, "field counts %s" % sorted(ncols)))
    dec = set()
    for l in body:
        for v in l.split(",")[1:]:
            if v:
                dec.add(len(v.split(".")[1]))
    rows.append(("published metrics are 4dp", dec == {4}, "widths %s" % sorted(dec)))
    empt = [i for i, l in enumerate(body) if "" in l.split(",")]
    rows.append(("published: only last frame_delta empty", empt == [len(body) - 1],
                 "empty rows %s" % empt))
    rows.append(("published: no -0.0000", "-0.0000" not in txt, "ok"))

    rd = json.load(open(pub_rd, encoding="utf-8"))
    mine = {"sample_rate_fps": SAMPLE_FPS, "rows": len(body),
            "columns": [{"column": k, "description": d,
                         "samples": len(body) - 1 if k == "frame_delta" else len(body)}
                        for k, d in README_COLUMNS]}
    rows.append(("README.json reproduced exactly", mine == rd,
                 "schema+descriptions+sample counts"))

    # NOTE (adversarial review): this block used to (a) skip the check entirely
    # when the file was absent -- so a repo with no raw/ dir still reported
    # "8/8 checks passed" -- and (b) record a literal True as the verdict.  Both
    # are fixed: a missing or unparseable dump is now an explicit FAIL row, and
    # the verdict is computed from what the parser actually returned.
    for tag in ("ffmpeg_scdet_scores_60fps.txt", "ffmpeg_signalstats_yavg_60fps.txt"):
        p_ = os.path.join(repo, "data/raw", tag)
        label = "raw parser accepts published %s" % tag.split("_")[1]
        if not os.path.exists(p_):
            rows.append((label, False, "MISSING: %s" % p_))
            continue
        try:
            nf, key, t0, t1 = check_raw_dump(p_)
        except AssertionError as e:
            rows.append((label, False, "parse error: %s" % str(e).splitlines()[0]))
            continue
        nl = sum(1 for _ in open(p_, encoding="utf-8"))
        rows.append((label, nf > 0 and bool(key) and nl == 2 * nf,
                     "%d frames, %d lines (2/frame), key=%s, %s..%s"
                     % (nf, nl, key, t0, t1)))

    # ---- numeric: re-derive sharpness_dip_ratio from published `sharpness` ---
    import csv as _csv
    R = list(_csv.DictReader(open(pub_csv, encoding="utf-8")))
    S = np.array([float(r["sharpness"]) for r in R])
    D = np.array([float(r["sharpness_dip_ratio"]) for r in R])
    pred = dip_ratio(S)
    err = np.abs(np.round(pred, 4) - D)
    n = len(D)
    exact = int((err <= 5e-5).sum())
    near = int((err <= 1e-3).sum())
    rows.append(("dip_ratio() vs published, bit-exact at 4dp",
                 exact / n > 0.95, "%d/%d = %.2f%%" % (exact, n, 100 * exact / n)))
    rows.append(("dip_ratio() vs published, within 1e-3",
                 near == n, "%d/%d = %.2f%%, max err %.2e" % (near, n, 100 * near / n, err.max())))

    # ---- numeric: published columns that pin constants of this module -------
    # (adversarial review) mutation testing showed SSM_KERNEL_HALF, AUDIO_EPS and
    # AUDIO_SR could all be changed without --verify-contract noticing, because
    # the checks that discriminate them lived only in an external script.  They
    # belong here, where the module's own constants are on trial.
    NV = np.array([float(r["ssm_novelty"]) for r in R])
    ST = np.array([float(r["straddle_distance"]) for r in R])
    PDX = np.array([float(r["pan_dx"]) for r in R])
    PDY = np.array([float(r["pan_dy"]) for r in R])
    AR = np.array([float(r["audio_rms_dbfs"]) for r in R])
    CN = np.array([float(r["spectral_centroid_hz"]) for r in R])
    nzv = np.nonzero(NV)[0]
    rows.append(("SSM_KERNEL_HALF == published novelty zero-span",
                 len(nzv) > 0 and nzv.min() == SSM_KERNEL_HALF
                 and (len(NV) - 1 - nzv.max()) == SSM_KERNEL_HALF,
                 "published zeros: first %d, last %d rows; constant = %d"
                 % (nzv.min(), len(NV) - 1 - nzv.max(), SSM_KERNEL_HALF)))
    rows.append(("straddle edge rule is CLAMPED, not zero-filled",
                 ST[0] > 0 and ST[-1] > 0,
                 "published row0 %.4f, row n-1 %.4f" % (ST[0], ST[-1])))
    rows.append(("pan_* integral and inside the %dx%d FFT wrap limits"
                 % (PROXY_W, PROXY_H),
                 np.all(PDX == np.round(PDX)) and np.all(PDY == np.round(PDY))
                 and PDX.min() >= -(PROXY_W // 2) and PDX.max() <= PROXY_W // 2
                 and PDY.min() >= -(PROXY_H // 2) and PDY.max() <= PROXY_H // 2,
                 "dx [%d,%d] of [%d,%d], dy [%d,%d] of [%d,%d]"
                 % (PDX.min(), PDX.max(), -(PROXY_W // 2), PROXY_W // 2,
                    PDY.min(), PDY.max(), -(PROXY_H // 2), PROXY_H // 2)))
    pf = _published_silence_floor(repo)
    rows.append(("AUDIO_EPS reproduces the published silence floor",
                 pf is not None and "%.4f" % (20 * np.log10(AUDIO_EPS)) == "%.4f" % pf,
                 "20log10(%g) = %.4f vs published %.4f on %d rows"
                 % (AUDIO_EPS, 20 * np.log10(AUDIO_EPS), pf if pf is not None else float("nan"),
                    int((AR < -100).sum()))))
    rows.append(("AUDIO_SR Nyquist covers the published centroid range",
                 CN.max() < AUDIO_SR / 2.0,
                 "published max %.1f Hz vs %d Hz Nyquist" % (CN.max(), AUDIO_SR // 2)))
    rows.append(("frame_delta orientation: only the LAST cell is empty",
                 [r["frame_delta"] for r in R][-1] == ""
                 and [r["frame_delta"] for r in R][0] != "",
                 "row0 %r, last %r" % (R[0]["frame_delta"], R[-1]["frame_delta"])))

    if produced_csv and os.path.exists(produced_csv):
        with open(produced_csv, encoding="utf-8") as f:
            h2 = f.readline().rstrip("\n")
        rows.append(("our CSV header == published header", h2 == head, h2[:48] + "..."))

    w = max(len(r[0]) for r in rows)
    print("| %-*s | %-4s | %s" % (w, "contract / numeric check", "res", "measured"))
    print("|-%s-|------|%s" % ("-" * w, "-" * 44))
    nfail = 0
    for name, ok, det in rows:
        nfail += 0 if ok else 1
        print("| %-*s | %-4s | %s" % (w, name, "PASS" if ok else "FAIL", det))
    print("\n%d/%d checks passed" % (len(rows) - nfail, len(rows)))
    return nfail == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", help="source video file")
    ap.add_argument("--out", "-o", default=None, help="output directory")
    ap.add_argument("--no-raw", action="store_true",
                    help="skip the two ffmpeg raw dumps (faster)")
    ap.add_argument("--self-test", action="store_true",
                    help="generate a synthetic lavfi clip and assert end to end")
    ap.add_argument("--keep", action="store_true", help="keep self-test artefacts")
    ap.add_argument("--verify-contract", metavar="REPO", default=None,
                    help="check header/format/README/dip formula against a published "
                         "teardown repo (e.g. /home/user/ai-video-style-teardown)")
    ap.add_argument("--allow-short", action="store_true",
                    help="downgrade the decode-completeness guard to a warning "
                         "(the CSV then covers only the part that decoded)")
    ap.add_argument("--quiet", "-q", action="store_true")
    a = ap.parse_args(argv)

    if a.verify_contract:
        return 0 if verify_contract(a.verify_contract) else 1
    if a.self_test:
        return 0 if self_test(a.out, keep=a.keep) else 1
    if not a.video or not a.out:
        ap.error("need <video> and --out (or --self-test)")
    measure(a.video, a.out, want_raw=not a.no_raw, verbose=not a.quiet,
            allow_short=a.allow_short)
    return 0


if __name__ == "__main__":
    sys.exit(main())
