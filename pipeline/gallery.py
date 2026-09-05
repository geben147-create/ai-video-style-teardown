# -*- coding: utf-8 -*-
import os, json, glob
SP = os.environ["SP"]
PUB = os.path.join(SP, "pub")
sc = json.load(open(os.path.join(PUB, "data", "scenes_verified.json"), encoding="utf-8"))
def mmss(t): return "%d:%02d" % (t // 60, t % 60)

CSS = """
:root{--bg:#f6f4ef;--panel:#fff;--ink:#16191b;--mut:#5d6469;--line:#dcd8ce;--acc:#0f6a5c;--chip:#eeebe3}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111310;--panel:#191c19;--ink:#eae7dc;
--mut:#9aa09a;--line:#2f332e;--acc:#5ed3b8;--chip:#212520}}
:root[data-theme="dark"]{--bg:#111310;--panel:#191c19;--ink:#eae7dc;--mut:#9aa09a;--line:#2f332e;--acc:#5ed3b8;--chip:#212520}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 -apple-system,"Malgun Gothic",Pretendard,"Segoe UI",system-ui,sans-serif}
.w{max-width:1500px;margin:0 auto;padding:28px 26px 90px}
h1{font-size:27px;margin:0 0 4px;letter-spacing:-.3px}
h2{font-size:20px;margin:46px 0 8px;padding-top:12px;border-top:2px solid var(--ink)}
.sub{color:var(--mut);font-size:13.5px;margin:0 0 18px}
a{color:var(--acc)}
.nav a{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:20px;
padding:4px 13px;margin:3px 4px 3px 0;text-decoration:none;font-size:13px;color:var(--ink)}
.nav a:hover{border-color:var(--acc)}
figure{margin:14px 0}
figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:9px;display:block;background:var(--panel)}
figcaption{font-size:12.5px;color:var(--mut);margin-top:5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));gap:9px;margin:14px 0}
.grid a{display:block;text-decoration:none;color:var(--ink)}
.grid img{width:100%;height:auto;border:1px solid var(--line);border-radius:6px;display:block}
.grid span{display:block;font-size:11px;color:var(--mut);padding:3px 2px 0;line-height:1.4}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:15px 18px;margin:14px 0}
.card.hero{border:2px solid var(--acc)}
"""

frames = sorted(glob.glob(os.path.join(PUB, "assets", "frames", "s*.jpg")))
tiles = []
for p in frames:
    b = os.path.basename(p)
    i = int(b[1:4])
    s = next((x for x in sc if x["i"] == i), None)
    lab = "#%03d" % i
    tiles.append('<a href="assets/frames/%s" target="_blank"><img loading="lazy" src="assets/frames/%s" alt="%s">'
                 '<span>%s</span></a>' % (b, b, lab, lab))
GRID = "".join(tiles)

def figs(items):
    return "".join('<figure><img loading="lazy" src="%s" alt="%s"><figcaption>%s</figcaption></figure>' % (s, c, c)
                   for s, c in items)

SHEETS = figs([("assets/sheets/contact_sheet_%02d.jpg" % i,
                "컨택트 시트 %d / 8 — 1차 자동 검출 196 경계 기준. 각 타일에 시각·길이·전환 유형 표기" % i)
               for i in range(1, 9)])
HL = figs([
 ("assets/highlights/HI_A_cajamarca_ambush.jpg","하이라이트 A · 카하마르카 매복 13:13~14:05 · 1초 간격 53컷 — 서사 절정. 혼돈(블러 8초) → 차폐(실루엣 3초) → 정지(대칭 12초)"),
 ("assets/highlights/HI_B_smallpox_spread.jpg","하이라이트 B · 천연두 확산 10:40~11:32 · 1초 간격 53컷 — 영상에서 순색 붉은색이 등장하는 유일한 구간"),
 ("assets/highlights/HI_C_legacy_village.jpg","하이라이트 C · 유산 마을 16:20~17:12 · 1초 간격 53컷 — 난색 56.9%로 전체 최고점. 유일한 실내·현대 장면"),
 ("assets/highlights/HI_D_quipu.jpg","하이라이트 D · 키푸 4:58~6:08 · 1.4초 간격 51컷 — 인물을 가장 오래 쓰는 70초의 얼굴 회피 처리"),
])
FIGS = figs([
 ("assets/fig1_visual_style.jpg","도판 1 · 기본 비주얼 스타일 12컷 — 회화적 매트페인팅 · 대기 원근 · 인물 최소"),
 ("assets/fig5_character_handling.jpg","도판 5 · 인물 처리 8가지 — AI 얼굴 일관성 문제를 '얼굴을 안 보여줘서' 회피"),
 ("assets/fig6_props.jpg","도판 6 · 배경·소품·요소 — 모든 소품은 내레이션이 방금 말한 명사를 1:1로 받는다"),
 ("assets/fig9_transitions.jpg","도판 9 · 전환 6유형 · 경계 전후 4프레임(−0.4 / −0.1 / +0.15 / +0.5초)"),
])
VER = figs([
 ("assets/verify/transition_types_strip.jpg","검증 1 · 전환 6유형을 경계 전후 8프레임으로 판독. 숫자 분류를 프레임으로 재확인"),
 ("assets/verify/false_positive_check.jpg","검증 2 · '크로스 디졸브'로 분류된 14개 표본 판독 → 13개가 오검출(연속 카메라 이동)로 확인돼 1차 결과 196개를 폐기"),
] + [("assets/verify/boundary_pairs_%d.jpg" % i,
     "검증 3-%d · 최종 경계 98개 전부의 전후 프레임 쌍(−0.45초 / +0.45초). 붉은 선이 경계. 이 판독으로 정탐 71 / 오탐 27 확정" % i)
     for i in range(1, 5)]
 + [("assets/verify/recall_filmstrip_%d.jpg" % i,
     "검증 4-%d · 15초 이상 장면 25개 내부를 4초 간격 218컷으로 재판독 → 약 45개 경계 누락 확인" % i)
     for i in range(1, 7)])

H = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>캡처 갤러리 · 딥 호라이즌 「잉카」 프레임 전수 판독 자료</title>
<style>__CSS__</style></head><body><div class="w">
<h1>캡처 갤러리 — 프레임 전수 판독 자료</h1>
<p class="sub">딥 호라이즌 「잉카 문명은 왜 멸망했나?」 · 19분 29초 · 1280&times;720 · 60fps ·
프레임 70,134장 전수 계산 · 측정일 2026-09-05</p>
<p class="nav">
<a href="./">← 분석 보고서 (17절)</a>
<a href="#fig">보고서 도판</a>
<a href="#hl">하이라이트 4구간</a>
<a href="#cs">컨택트 시트 8장</a>
<a href="#ver">검증 자료 12장</a>
<a href="#fr">장면 프레임 196장</a>
<a href="#data">데이터 파일</a>
</p>
<div class="card hero"><b>이 페이지의 용도.</b>
보고서 본문(<a href="./">index.html</a>)에는 도판이 요약본으로 들어가 있다.
여기에는 <b>판독에 실제로 사용한 원본 해상도 이미지 전부</b>가 있다.
특히 <b>검증 자료</b>는 이 분석의 정확도(정밀도 72.4%)를 어떻게 확인했는지 보여주는 근거다.</div>

<h2 id="fig">보고서 도판</h2>
__FIGS__
<h2 id="hl">하이라이트 4구간 · 1초 간격 재캡처</h2>
<p class="sub">30초 창으로 <b>컷 밀도 &times; 모션량 &times; 밝기 변화</b>를 표준화 합산해 상위 구간을 뽑고,
서사 중요도를 함께 고려해 4구간을 확정한 뒤 다시 캡처했다.</p>
__HL__
<h2 id="cs">컨택트 시트 8장 · 영상 전체 시각 사전</h2>
<p class="sub">1차 자동 검출(196 경계) 결과를 그대로 배열한 것이다. 검증 결과 이 중 상당수가
"연속 카메라 이동 중의 변화"였으므로 <b>장면 수의 근거가 아니라 시각 사전으로</b> 사용한다.</p>
__SHEETS__
<h2 id="ver">검증 자료 12장 · 이 분석의 정확도 근거</h2>
__VER__
<h2 id="fr">장면 프레임 196장</h2>
<p class="sub">각 검출 구간의 대표 프레임. 클릭하면 원본이 열린다.</p>
<div class="grid">__GRID__</div>
<h2 id="data">데이터 파일</h2>
<ul>
<li><a href="data/scenes_verified.json">scenes_verified.json</a> — 육안 확정 장면 72개 (시작·끝·길이·진입 전환 유형)</li>
<li><a href="data/metrics.json">metrics.json</a> — 분당 색/명도/음량, 챕터별 지표, 팔레트, 장면별 카메라</li>
<li><a href="data/palette.json">palette.json</a> — k-means 10색 팔레트 (HEX · 점유율 · HSV)</li>
<li><a href="data/camera.json">camera.json</a> — 장면별 누적 팬/틸트 변위와 줌 비율</li>
<li><a href="data/transcript.json">transcript.json</a> — 영어 자막 531큐 타임코드</li>
</ul>
<p class="sub" style="margin-top:34px">캡처 이미지는 분석·비평 목적의 인용이며, 이 문서가 해당 영상·음악·이미지의
재사용 권리를 부여하지 않는다. 분석 원본:
<a href="https://www.youtube.com/watch?v=ncfT8EvuX24">youtube.com/watch?v=ncfT8EvuX24</a></p>
</div></body></html>"""

H = (H.replace("__CSS__", CSS).replace("__FIGS__", FIGS).replace("__HL__", HL)
      .replace("__SHEETS__", SHEETS).replace("__VER__", VER).replace("__GRID__", GRID))
out = os.path.join(PUB, "gallery.html")
open(out, "w", encoding="utf-8").write(H)
print("gallery.html", round(os.path.getsize(out) / 1024, 1), "KB ·", len(frames), "frame tiles")
