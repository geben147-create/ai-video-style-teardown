# -*- coding: utf-8 -*-
import os, shutil, glob
SP = os.environ["SP"]
AN = os.path.join(SP, "an")
PUB = os.path.join(SP, "pub")
if os.path.isdir(PUB):
    if os.environ.get("STAGE_CLEAN") != "1":
        raise SystemExit(f"{PUB} 가 이미 있습니다. 지우고 다시 만들려면 STAGE_CLEAN=1 로 실행하세요.")
    shutil.rmtree(PUB)
for d in ["assets/sheets", "assets/highlights", "assets/verify", "assets/frames", "data"]:
    os.makedirs(os.path.join(PUB, d), exist_ok=True)

def cp(src, dst):
    s = os.path.join(AN, src)
    if not os.path.exists(s):
        print("MISSING", src); return 0
    d = os.path.join(PUB, dst)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(s, d); return 1

n = 0
n += cp("AI_VIDEO_STYLE_TEARDOWN_INCA.html", "index.html")
n += cp("README_pub.md", "README.md")
for f, t in [("scenes_verified.json","data/scenes_verified.json"),("data.json","data/metrics.json"),
             ("transcript.json","data/transcript.json"),("palette.json","data/palette.json"),
             ("camera.json","data/camera.json")]:
    n += cp(f, t)
for i in range(1, 9):
    n += cp(f"sheets/sheet{i:02d}.jpg", f"assets/sheets/contact_sheet_{i:02d}.jpg")
for tag, name in [("A","cajamarca_ambush"),("B","smallpox_spread"),("C","legacy_village"),("D","quipu")]:
    n += cp(f"hi/HI_{tag}.jpg", f"assets/highlights/HI_{tag}_{name}.jpg")
for i in range(1, 5):
    n += cp(f"pairs/pairs{i}.jpg", f"assets/verify/boundary_pairs_{i}.jpg")
n += cp("strip/verify_transitions.jpg", "assets/verify/transition_types_strip.jpg")
n += cp("strip/verify_E.jpg", "assets/verify/false_positive_check.jpg")
for i in range(1, 7):
    n += cp(f"recall/recall{i}.jpg", f"assets/verify/recall_filmstrip_{i}.jpg")
for f, t in [("fig/fig1.jpg","assets/fig1_visual_style.jpg"),("fig/fig5.jpg","assets/fig5_character_handling.jpg"),
             ("fig/fig6.jpg","assets/fig6_props.jpg"),("fig/fig9.jpg","assets/fig9_transitions.jpg")]:
    n += cp(f, t)
for p in sorted(glob.glob(os.path.join(AN, "rep", "s*.jpg"))):
    shutil.copy2(p, os.path.join(PUB, "assets/frames", os.path.basename(p))); n += 1
open(os.path.join(PUB, ".nojekyll"), "w").close()

tot = 0; cnt = 0
for root, _, files in os.walk(PUB):
    for f in files:
        tot += os.path.getsize(os.path.join(root, f)); cnt += 1
print(f"복사 {n}건 · 총 {cnt}개 파일 · {tot/1024/1024:.1f} MB")
for d in ["", "assets", "assets/sheets", "assets/highlights", "assets/verify", "assets/frames", "data"]:
    p = os.path.join(PUB, d)
    fs = [x for x in os.listdir(p) if os.path.isfile(os.path.join(p, x))]
    sz = sum(os.path.getsize(os.path.join(p, x)) for x in fs)
    print(f"  {d or '.':22s} {len(fs):4d} files  {sz/1024/1024:6.1f} MB")
