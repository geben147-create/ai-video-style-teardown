import json,base64,os,io
D=json.load(open("data.json"))
def b64(p):
    return "data:image/jpeg;base64,"+base64.b64encode(open(p,"rb").read()).decode()
def mmss(t): return f"{int(t//60)}:{int(t%60):02d}"

# ---------- SVG chart: warm ratio + loudness ----------
pm=D["perMinute"]
W,H=980,300; PL,PR,PT,PB=52,18,18,34
iw,ih=W-PL-PR,H-PT-PB
def x(i): return PL+iw*i/(len(pm)-1)
def yw(v): return PT+ih*(1-(v-0)/60)
def yd(v): return PT+ih*(1-(v+36)/24)
warm="".join(f"{'M' if i==0 else 'L'}{x(i):.1f},{yw(p['warm']):.1f}" for i,p in enumerate(pm))
loud="".join(f"{'M' if i==0 else 'L'}{x(i):.1f},{yd(p['db']):.1f}" for i,p in enumerate(pm))
grid="".join(f'<line x1="{PL}" y1="{yw(v):.1f}" x2="{W-PR}" y2="{yw(v):.1f}" class="g"/><text x="{PL-8}" y="{yw(v)+4:.1f}" class="ax" text-anchor="end">{v}%</text>' for v in (0,15,30,45,60))
xt="".join(f'<text x="{x(i):.1f}" y="{H-12}" class="ax" text-anchor="middle">{p["m"]}</text>' for i,p in enumerate(pm) if p["m"]%2==0)
bands=[(0,9.7,"제국의 생명력 — 난색 상승","#8a5a2b"),(9.7,15.8,"죽음·배신·몰락 — 한색 급락","#2c5566"),(15.8,17.0,"유산 — 난색 회복","#8a5a2b"),(17.0,19,"엔딩","#3a3f45")]
bd="".join(f'<rect x="{x(a):.1f}" y="{PT}" width="{x(min(b,19))-x(a):.1f}" height="{ih}" fill="{c}" opacity=".14"/>' for a,b,l,c in bands)
CHART1=f'''<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="분당 난색 비율과 라우드니스 곡선">
{bd}{grid}
<path d="{warm}" class="l1"/><path d="{loud}" class="l2"/>
{xt}<text x="{W//2}" y="{H-1}" class="ax" text-anchor="middle">경과 시간(분)</text>
<g class="lg"><rect x="{PL+8}" y="{PT+6}" width="11" height="3" class="s1"/><text x="{PL+24}" y="{PT+11}" class="ax">난색 픽셀 비율(%)</text>
<rect x="{PL+150}" y="{PT+6}" width="11" height="3" class="s2"/><text x="{PL+166}" y="{PT+11}" class="ax">평균 라우드니스(dBFS, 스케일 보정)</text></g></svg>'''

# ---------- SVG: scene-change density per chapter ----------
cr=D["chapterRows"]
W2,H2=980,260; PL2,PB2,PT2=52,74,16
ih2=H2-PT2-PB2; iw2=W2-PL2-18
bw=iw2/len(cr)
mx=max(c["per_min"] for c in cr)
bars=""
for i,c in enumerate(cr):
    h=ih2*c["per_min"]/mx*0.94
    bx=PL2+i*bw+bw*0.16; bwd=bw*0.68
    col="#c96a3c" if c["warm"]>=40 else ("#3f7f97" if c["warm"]<25 else "#7d8a75")
    bars+=f'<rect x="{bx:.1f}" y="{PT2+ih2-h:.1f}" width="{bwd:.1f}" height="{h:.1f}" fill="{col}" rx="2"/>'
    bars+=f'<text x="{bx+bwd/2:.1f}" y="{PT2+ih2-h-5:.1f}" class="ax" text-anchor="middle">{c["per_min"]}</text>'
    bars+=f'<text x="{bx+bwd/2:.1f}" y="{PT2+ih2+14:.1f}" class="ax" text-anchor="middle">{mmss(c["t0"])}</text>'
    nm=c["name"][:11]
    bars+=f'<text x="{bx+bwd/2:.1f}" y="{PT2+ih2+30:.1f}" class="axs" text-anchor="middle" transform="rotate(28 {bx+bwd/2:.1f} {PT2+ih2+30:.1f})">{nm}</text>'
CHART2=f'<svg viewBox="0 0 {W2} {H2}" class="chart" role="img" aria-label="챕터별 장면 전환 빈도"><line x1="{PL2}" y1="{PT2+ih2}" x2="{W2-18}" y2="{PT2+ih2}" class="g"/>{bars}<text x="{PL2-8}" y="{PT2+10}" class="ax" text-anchor="end">회/분</text></svg>'

# ---------- SVG: 화면 레이아웃 규격 ----------
LW,LH=560,315
CHART3=f'''<svg viewBox="0 0 {LW} {LH+26}" class="chart" role="img" aria-label="화면 안전영역과 자막 위치 규격">
<rect x="0" y="0" width="{LW}" height="{LH}" fill="#2b3a3f"/>
<rect x="{LW*0.05:.0f}" y="{LH*0.05:.0f}" width="{LW*0.90:.0f}" height="{LH*0.90:.0f}" fill="none" stroke="#7fb0a8" stroke-dasharray="5 4"/>
<rect x="0" y="{LH*0.876:.0f}" width="{LW}" height="{LH*0.046:.0f}" fill="#e8e2d2" opacity=".92"/>
<text x="{LW/2:.0f}" y="{LH*0.912:.0f}" text-anchor="middle" font-size="11" fill="#1b2226">자막 밴드 · 화면 높이 87.6%~92.2% · 중앙 정렬</text>
<rect x="{LW*0.855:.0f}" y="{LH*0.022:.0f}" width="{LW*0.125:.0f}" height="{LH*0.063:.0f}" fill="#dfe6e4" opacity=".55"/>
<text x="{LW*0.917:.0f}" y="{LH*0.062:.0f}" text-anchor="middle" font-size="9" fill="#1b2226">워터마크</text>
<text x="{LW*0.5:.0f}" y="{LH*0.45:.0f}" text-anchor="middle" font-size="13" fill="#cfe0da">피사체 존 — 지평선을 화면 55~70% 높이에</text>
<line x1="0" y1="{LH*0.62:.0f}" x2="{LW}" y2="{LH*0.62:.0f}" stroke="#cfe0da" stroke-dasharray="3 5" opacity=".6"/>
<text x="4" y="{LH+18}" font-size="11" fill="#8d9a94">1280×720 · 60fps · 레터박스 없음 · 안전영역(점선) 5% · 자막 캡높이 ≈ 화면 높이 3.8%</text></svg>'''

# ---------- palette swatches ----------
pal=D["palette"]
sw="".join(f'<div class="sw"><i style="background:{p["hex"]}"></i><b>{p["hex"]}</b><span>{p["share"]}% · H{p["H"]}° S{p["S"]}% V{p["V"]}%</span></div>' for p in pal)

json.dump(dict(CHART1=CHART1,CHART2=CHART2,CHART3=CHART3,SW=sw),open("charts.json","w"),ensure_ascii=False)
print("charts built")
