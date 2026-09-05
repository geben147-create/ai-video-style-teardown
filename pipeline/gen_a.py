# -*- coding: utf-8 -*-
import json,base64,os
D=json.load(open("data.json")); C=json.load(open("charts.json"))
def b64(p): return "data:image/jpeg;base64,"+base64.b64encode(open(p,"rb").read()).decode()
def mmss(t): return f"{int(t//60)}:{int(t%60):02d}"
FIG={k:b64(f"fig/{k}.jpg") for k in ["fig1","fig5","fig6","fig9","hiA","hiB","hiC","hiD"]+[f"cs{i}" for i in range(1,9)]}
json.dump({"note":"figs embedded at build"},open("_figs_ok.json","w"))

SECS=[("s0","0. 결론과 이 문서 사용법"),("s1","1. 기본 비주얼 스타일"),("s2","2. 색 설계 — 팔레트와 색온도 서사"),
("s3","3. 화면 구성 · 프레이밍 · 자막 규격"),("s4","4. 장면 전개 원칙"),("s5","5. 장면 유형"),
("s6","6. 속도 — 측정 데이터"),("s7","7. 애니메이션 원칙"),("s8","8. 사운드 디자인"),
("s9","9. 전환 애니메이션"),("s10","10. 캐릭터 시트 · 인물 처리"),("s11","11. 배경 · 소품 · 요소"),
("s12","12. 편집 · 효과 · 카메라"),("s13","13. IMAGE / VIDEO PROMPT 작성법"),("s14","14. 금지사항"),
("s15","15. 하이라이트 4구간 정밀분석 (재캡처)"),("s16","16. 전체 장면 지표"),("s17","17. 측정 방법과 한계"),
("s18","부록. 보유 가이드(ai-video-production-guide)와의 대조")]
NAV="".join(f'<a href="#{i}">{t}</a>' for i,t in SECS)

CSS = """
:root{--bg:#f6f4ef;--panel:#fff;--ink:#16191b;--mut:#5d6469;--line:#dcd8ce;--acc:#0f6a5c;--acc2:#9a4a20;
--warm:#c96a3c;--cool:#3f7f97;--red:#b3283f;--amber:#9a7318;--chip:#eeebe3;--code:#f1eee6;
--sh:0 1px 2px rgba(0,0,0,.06),0 10px 26px rgba(0,0,0,.05)}
:root:not([data-theme="light"]) {}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111310;--panel:#191c19;--ink:#eae7dc;--mut:#9aa09a;
--line:#2f332e;--acc:#5ed3b8;--acc2:#e0a06a;--warm:#e08a55;--cool:#6fb2ca;--red:#ff7189;--amber:#e0b64a;
--chip:#212520;--code:#1c1f1c;--sh:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35)}}
:root[data-theme="dark"]{--bg:#111310;--panel:#191c19;--ink:#eae7dc;--mut:#9aa09a;--line:#2f332e;--acc:#5ed3b8;
--acc2:#e0a06a;--warm:#e08a55;--cool:#6fb2ca;--red:#ff7189;--amber:#e0b64a;--chip:#212520;--code:#1c1f1c;
--sh:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15.5px/1.75 -apple-system,"Malgun Gothic",Pretendard,"Segoe UI",system-ui,sans-serif}
.wrap{display:grid;grid-template-columns:266px minmax(0,1fr);max-width:1620px;margin:0 auto}
nav{position:sticky;top:0;height:100vh;overflow-y:auto;padding:20px 14px;border-right:1px solid var(--line);background:var(--panel)}
nav h4{margin:0 0 4px;font-size:11px;letter-spacing:.16em;color:var(--mut);text-transform:uppercase}
nav .meta{font-size:12px;color:var(--mut);margin:0 0 14px;line-height:1.5}
nav a{display:block;padding:6px 9px;margin:1px 0;border-radius:7px;color:var(--ink);text-decoration:none;font-size:13.2px;line-height:1.45}
nav a:hover{background:var(--chip)}
main{padding:30px 38px 120px;min-width:0}
h1{font-size:29px;line-height:1.3;margin:0 0 6px;letter-spacing:-.4px}
h2{font-size:22px;margin:54px 0 12px;padding-top:13px;border-top:2px solid var(--ink);letter-spacing:-.2px;scroll-margin-top:12px}
h3{font-size:17px;margin:28px 0 8px;color:var(--acc)}
h4{font-size:14.5px;margin:18px 0 6px;color:var(--acc2)}
p{margin:9px 0}
.sub{color:var(--mut);font-size:13.5px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0;box-shadow:var(--sh)}
.card.hero{border:2px solid var(--acc)}
.card.warn{border-left:5px solid var(--amber)}
.card.bad{border-left:5px solid var(--red)}
.card.ok{border-left:5px solid var(--acc)}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13.2px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:var(--chip);font-weight:700;white-space:nowrap}
tr:nth-child(even) td{background:color-mix(in srgb,var(--panel) 92%,var(--chip))}
code{background:var(--code);padding:1.5px 5px;border-radius:4px;font-family:"Cascadia Mono",Consolas,monospace;font-size:12.5px}
pre{background:var(--code);border:1px solid var(--line);border-radius:10px;padding:14px 15px;overflow-x:auto;
font-family:"Cascadia Mono",Consolas,monospace;font-size:12.4px;line-height:1.62;white-space:pre}
figure{margin:16px 0}
figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:9px;display:block}
figcaption{font-size:12.4px;color:var(--mut);margin-top:6px}
.chart{width:100%;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px}
.chart .g{stroke:var(--line);stroke-width:1}
.chart .l1{fill:none;stroke:var(--warm);stroke-width:2.4}
.chart .l2{fill:none;stroke:var(--cool);stroke-width:2.1;stroke-dasharray:6 4}
.chart .s1{fill:var(--warm)}.chart .s2{fill:var(--cool)}
.chart .ax{font-size:10.5px;fill:var(--mut)}
.chart .axs{font-size:9px;fill:var(--mut)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:10px;margin:14px 0}
.kpi div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.kpi b{display:block;font-size:22px;line-height:1.2;color:var(--acc)}
.kpi span{font-size:11.8px;color:var(--mut)}
.pal{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:9px;margin:12px 0}
.sw{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:8px 10px;font-size:12px}
.sw i{display:block;height:36px;border-radius:6px;margin-bottom:6px;border:1px solid rgba(0,0,0,.15)}
.sw b{display:block;font-family:Consolas,monospace;font-size:12.5px}
.sw span{color:var(--mut);font-size:11px}
.tag{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:20px;
padding:2px 10px;font-size:11.8px;margin:2px 3px 2px 0;color:var(--mut)}
.no{color:var(--red);font-weight:700}.yes{color:var(--acc);font-weight:700}
ul,ol{margin:8px 0 8px 20px;padding:0}li{margin:4px 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:1000px){.wrap{grid-template-columns:1fr}nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}
main{padding:20px 16px 80px}.two{grid-template-columns:1fr}}
"""
open("_css.txt","w",encoding="utf-8").write(CSS)
open("_nav.txt","w",encoding="utf-8").write(NAV)
json.dump(FIG,open("_fig.json","w"))
print("assets ready", sum(len(v) for v in FIG.values())//1024,"KB base64")
