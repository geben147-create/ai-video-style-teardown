# -*- coding: utf-8 -*-
"""남은 산출물 전부를 공개 저장소 스테이징 폴더로 옮긴다."""
import os, shutil, glob, re, json
import numpy as np

SP = os.environ["SP"]
AN, PUB = os.path.join(SP, "an"), os.path.join(SP, "pub")
LOCAL_ROOT = os.environ.get("PROJECT_ROOT", "")

for d in ["pipeline", "data/timeseries", "data/raw",
          "assets/highlights/frames", "assets/verify/frames", "assets/figures/frames"]:
    os.makedirs(os.path.join(PUB, d), exist_ok=True)

added = {}

# ---------- 1. 분석 스크립트 (로컬 절대경로만 치환) ----------
ORDER = ["rgb.py","cuts.py","stage.py","stage2.py","gen_head.py","gen_a.py",
         "body1.py","body2.py","body3.py","body4.py","body5.py",
         "assemble.py","gallery.py","reg.py"]
n = 0
for f in ORDER:
    p = os.path.join(AN, f)
    if not os.path.exists(p):
        continue
    s = open(p, encoding="utf-8").read()
    s = s.replace(SP, os.environ["SP"]).replace(LOCAL_ROOT, os.environ.get("PROJECT_ROOT", ""))
    s = re.sub(r'C:\\+Users\\+USER[^\s"\']*', "<LOCAL_PATH>", s)
    open(os.path.join(PUB, "pipeline", f), "w", encoding="utf-8").write(s)
    n += 1
added["pipeline scripts"] = n

# ---------- 2. 시계열 데이터 (.npy → CSV) ----------
TS = [("d1.npy","frame_delta","0.1초 간격 프레임 간 평균 RGB 차이"),
      ("Y.npy","luma_mean","프레임 평균 휘도 0-255"),
      ("sstd.npy","luma_spatial_std","프레임 내 휘도 표준편차(공간 대비)"),
      ("sharp.npy","sharpness","그래디언트 에너지(고주파 선명도)"),
      ("ratio.npy","sharpness_dip_ratio","국소 선명도 / 주변 중앙값 — 디졸브 판별 지표"),
      ("nov.npy","ssm_novelty","자기유사도 노벨티(±1.6초 체커보드 커널)"),
      ("D.npy","straddle_distance","±0.5초 히스토그램 코사인 거리 — 장면 확정 지표"),
      ("DX.npy","pan_dx","위상상관 수평 변위 px(96px 폭 기준)"),
      ("DY.npy","pan_dy","위상상관 수직 변위 px"),
      ("db.npy","audio_rms_dbfs","0.1초 오디오 RMS dBFS"),
      ("cen.npy","spectral_centroid_hz","0.1초 스펙트럴 센트로이드 Hz")]
cols, meta, N = {}, [], None
for f, name, desc in TS:
    p = os.path.join(AN, f)
    if not os.path.exists(p):
        continue
    a = np.load(p).astype(np.float64)
    cols[name] = a
    meta.append({"column": name, "description": desc, "samples": int(len(a))})
    N = max(N or 0, len(a))
keys = list(cols)
with open(os.path.join(PUB, "data/timeseries/timeseries_10fps.csv"), "w", encoding="utf-8") as fh:
    fh.write("t_sec," + ",".join(keys) + "\n")
    for i in range(N):
        row = [f"{i/10:.1f}"]
        for k in keys:
            v = cols[k]
            row.append(f"{v[i]:.4f}" if i < len(v) else "")
        fh.write(",".join(row) + "\n")
json.dump({"sample_rate_fps": 10, "rows": N, "columns": meta},
          open(os.path.join(PUB, "data/timeseries/README.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
added["timeseries columns"] = len(keys)

# ---------- 3. 검출 이력 JSON ----------
for f, t in [("shots.json", "data/detection_pass1_196.json"),
             ("scenes.json", "data/detection_pass2_99.json"),
             ("charts.json", "data/charts_svg.json")]:
    p = os.path.join(AN, f)
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(PUB, t))
added["detection history"] = 3

# ---------- 4. 원시 ffmpeg 측정 덤프 ----------
for f, t in [("scd.txt", "data/raw/ffmpeg_scdet_scores_60fps.txt"),
             ("yavg.txt", "data/raw/ffmpeg_signalstats_yavg_60fps.txt")]:
    p = os.path.join(AN, f)
    if os.path.exists(p):
        shutil.copy2(p, os.path.join(PUB, t))
added["raw ffmpeg dumps"] = 2

# ---------- 5. 개별 캡처 프레임 전부 ----------
def bulk(srcdir, dstdir, pattern, skip_underscore=False):
    c = 0
    for p in sorted(glob.glob(os.path.join(AN, srcdir, pattern))):
        b = os.path.basename(p)
        if skip_underscore and not b.startswith("_"):
            continue
        if b.startswith("_"):
            b = b[1:]
        shutil.copy2(p, os.path.join(PUB, dstdir, b)); c += 1
    return c

added["highlight frames"] = bulk("hi", "assets/highlights/frames", "[ABCD]_*.jpg")
added["boundary pair frames"] = bulk("pairs", "assets/verify/frames", "p*.jpg")
added["recall filmstrip frames"] = bulk("recall", "assets/verify/frames", "r*.jpg")
added["transition strip frames"] = bulk("strip", "assets/verify/frames", "_*.jpg")
added["figure source frames"] = bulk("fig", "assets/figures/frames", "_*.jpg")

tot = cnt = 0
for root, _, files in os.walk(PUB):
    for f in files:
        tot += os.path.getsize(os.path.join(root, f)); cnt += 1
print("추가 항목:")
for k, v in added.items():
    print(f"  {k:26s} {v}")
print(f"\n스테이징 총계: {cnt}개 파일 · {tot/1024/1024:.1f} MB")
