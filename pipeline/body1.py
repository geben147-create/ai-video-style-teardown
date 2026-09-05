# -*- coding: utf-8 -*-
import json
D=json.load(open("data.json")); C=json.load(open("charts.json")); FIG=json.load(open("_fig.json"))
def mmss(t): return f"{int(t//60)}:{int(t%60):02d}"
cr=D["chapterRows"]
chrows="".join(f"<tr><td>{mmss(c['t0'])}–{mmss(c['t1'])}</td><td>{c['name']}</td><td>{c['t1']-c['t0']}초</td>"
  f"<td>{c['scenes']}</td><td>{c['per_min']}</td><td>{c['warm']}%</td><td>{c['V']}%</td><td>{c['db']}</td></tr>" for c in cr)

B=f"""
<h1>구름 위의 제국 — AI 역사 다큐 영상 제작 원리 해부</h1>
<p class="sub">분석 대상 1편 · <b>딥 호라이즌</b> 「잉카 문명은 왜 멸망했나?」 · 19분 29초 · 1280×720 · 60fps · 조회 52.0만 · 구독 5.51만<br>
측정 기준일 2026-09-05 · 프레임 70,134장 / 10fps 시계열 11,689 샘플 전수 계산 · 경계 98개 육안 전수 검증</p>

<h2 id="s0">0. 결론과 이 문서 사용법</h2>
<div class="card hero">
<h3 style="margin-top:0">한 줄 결론</h3>
<p><b>이 채널은 "AI가 잘 못하는 것을 화면에 넣지 않는 방식"으로 완성도를 만든다.</b>
얼굴·손가락·복잡한 액션·긴 연속 동작 — AI 영상의 4대 실패 지점을 전부 회피하고,
AI가 압도적으로 잘하는 <b>회화적 풍경 + 느린 카메라 드리프트 + 대기 원근</b>만으로 19분을 채운다.
서사는 <b>색온도</b>가 끌고 간다.</p>
</div>
<div class="kpi">
<div><b>72</b><span>육안 확정 장면 전환 수</span></div>
<div><b>~115</b><span>누락 보정 추정 실제 장면 수</span></div>
<div><b>11.9초</b><span>확정 장면 길이 중앙값</span></div>
<div><b>85초</b><span>챕터 1개 평균 길이 (12챕터)</span></div>
<div><b>52.1%</b><span>하드컷 비율</span></div>
<div><b>25.4%</b><span>통과 전환(구름·아치·차폐물)</span></div>
<div><b>64.0%</b><span>내레이션 점유율</span></div>
<div><b>189</b><span>단어/분 발화 속도</span></div>
<div><b>7.1%</b><span>카메라가 거의 멈춘 장면 비율</span></div>
<div><b>5회</b><span>영상 전체의 정면 얼굴 컷 수</span></div>
</div>

<h3>이 문서를 다른 주제에 적용하는 법</h3>
<ol>
<li><b>§1~§3</b>은 화면 규격이다. 주제가 바뀌어도 그대로 복사한다.</li>
<li><b>§4~§6</b>은 시간 설계다. 주제의 "챕터 12개 × 85초" 골격만 새로 짜면 된다.</li>
<li><b>§7·§9·§12</b>는 전환·카메라 문법이다. 소재가 산이든 도시든 우주든 동일하게 작동한다.</li>
<li><b>§10·§11</b>이 이 스타일의 핵심 방어선이다. 인물을 늘리고 싶은 유혹을 여기서 막는다.</li>
<li><b>§13</b>의 프롬프트 틀에 주제 명사만 갈아끼우면 1차 생성이 나온다.</li>
<li><b>§14 금지사항</b>을 QC 게이트로 그대로 쓴다.</li>
</ol>
<div class="card warn"><b>읽는 기준.</b> 이 문서의 숫자는 전부 이 영상 1편을 직접 디코딩해 계산한 값이다.
제작진의 실제 워크플로·사용 모델·프롬프트는 공개된 바 없으므로, 아래의 "왜 이렇게 했는가"는
<b>화면에서 관측되는 결과로부터의 역추론</b>이다. 확정 사실과 추론을 §17에서 분리해 표기했다.</div>

<h2 id="s1">1. 기본 비주얼 스타일</h2>
<figure><img src="{FIG['fig1']}" alt="기본 비주얼 스타일 12컷"><figcaption>도판 1 · 영상 전체를 관통하는 12컷. 인물이 화면의 3% 이상을 차지하는 컷이 거의 없다.</figcaption></figure>
<h3>스타일 정의</h3>
<table><tr><th>항목</th><th>관측값</th><th>재현용 지시어</th></tr>
<tr><td>매체감</td><td>디지털 유화/과슈. 붓 결이 보이되 뭉개지지 않고, 윤곽선이 없다</td><td><code>painterly matte painting, gouache texture, no linework</code></td></tr>
<tr><td>디테일 밀도</td><td>근경만 디테일, 중경부터 급격히 단순화 (면으로 처리)</td><td><code>detailed foreground, simplified midground planes</code></td></tr>
<tr><td>원근</td><td>대기 원근 3~5겹. 먼 산일수록 채도↓ 명도↑ 청색↑</td><td><code>strong atmospheric perspective, 4 haze layers</code></td></tr>
<tr><td>광원</td><td>거의 항상 낮은 각도의 측광/역광. 정오광 없음</td><td><code>low golden-hour sun, rim light, long shadows</code></td></tr>
<tr><td>대비</td><td>중간 대비. 순수 검정(0)·순수 흰색(255) 거의 없음</td><td><code>lifted blacks, soft highlights, filmic curve</code></td></tr>
<tr><td>질감 오버레이</td><td>전 화면에 미세 종이/캔버스 그레인. 60fps에서도 고정 패턴</td><td><code>subtle paper grain overlay</code></td></tr>
<tr><td>구름</td><td>전체 장면의 절반 이상에 등장하는 <b>주인공급 요소</b></td><td><code>volumetric cumulus below the horizon line</code></td></tr>
</table>
<div class="card ok"><b>왜 이 스타일인가 (역추론).</b> 회화 스타일은 AI 생성물의 3대 약점을 구조적으로 숨긴다 —
① 손가락·치아 같은 해부학 오류가 "붓 터치"로 읽힌다 ② 프레임 간 미세 떨림이 "종이 질감"에 묻힌다
③ 사진 리얼리즘이면 즉시 이상해 보일 원근 오류가 회화에서는 양식으로 통과된다.
<b>사실 사진을 못 구하는 주제(고대사)에서 이 선택은 비용이 아니라 무기다.</b></div>

<h2 id="s2">2. 색 설계 — 팔레트와 색온도 서사</h2>
<h3>2-1. 측정된 팔레트 (전체 프레임 k-means 10색)</h3>
<div class="pal">{C['SW']}</div>
<p>상위 5색이 전부 <b>청록–슬레이트 계열(H 173°~191°)</b>로 화면의 <b>64.7%</b>, 나머지 5색이
<b>황토–테라코타 계열(H 30°~51°)</b>로 <b>35.3%</b>다. 두 계열의 색상 거리는 약 <b>156°</b> —
교과서적인 <b>보색 2색 팔레트(teal &amp; orange)</b>이되, 점유율 가중 평균 채도가 <b>약 41%</b>로 강하게 탈채도돼 있다.</p>
<table><tr><th>역할</th><th>색</th><th>담당 의미</th></tr>
<tr><td>지배색(한색)</td><td><code>#1B3539</code> <code>#33555D</code> <code>#5B767B</code></td><td>고도·거리·시간·죽음</td></tr>
<tr><td>강조색(난색)</td><td><code>#B48352</code> <code>#DFBF8F</code> <code>#7A5F3E</code></td><td>석조·인간의 노동·생명</td></tr>
<tr><td>중성 브릿지</td><td><code>#8C9D9B</code></td><td>구름 — 두 계열을 잇는 유일한 고명도 면</td></tr>
<tr><td>예약색</td><td>순색 적색 · 순색 금색</td><td>각각 <b>전염(10:52~11:27)</b>과 <b>황금 몸값(14:16~14:46)</b>에만 등장</td></tr>
</table>
<div class="card"><b>예약색 원칙.</b> 이 영상에서 <b>채도 높은 붉은색과 금색은 전체 19분 중 단 두 구간에만 나온다.</b>
평소 화면을 41% 채도로 눌러 두었기 때문에, 그 두 구간이 등장할 때 시청자는 별도의 연출 없이도 "사건이 벌어졌다"고 읽는다.
색을 아껴 쓰는 것 자체가 편집이다.</div>

<h3>2-2. 색온도 서사 곡선 — 이 영상 최대의 설계</h3>
{C['CHART1']}
<p>난색 픽셀 비율은 서사와 <b>정확히 함께 움직인다.</b></p>
<table><tr><th>구간</th><th>서사</th><th>난색 비율</th><th>해석</th></tr>
<tr><td>0~4분</td><td>수수께끼 제시</td><td>32.7% → 43.2%</td><td>중립에서 시작해 서서히 데운다</td></tr>
<tr><td>5~9분</td><td>제국의 세 가지 비밀 · 굶는 이 없던 나라</td><td><b>50~57%</b> (최고)</td><td>난색 최대 = 제국의 생명력</td></tr>
<tr><td>10~15분</td><td>천연두 → 내전 → 매복 → 몰락</td><td><b>7.8~19.7%</b> (최저)</td><td>6분간 화면에서 온기를 걷어낸다</td></tr>
<tr><td>16분</td><td>유산 — 지금도 사는 사람들</td><td><b>56.9%</b> (재상승)</td><td>단 1분간의 온기 복귀 = 감정 해소점</td></tr>
<tr><td>17~19분</td><td>마무리·엔드카드</td><td>27.1% → 10.9%</td><td>다시 식히며 닫는다</td></tr>
</table>
<div class="card hero"><b>복제 가능한 규칙.</b> 주제가 무엇이든 <b>"난색 비율"이라는 단일 수치 하나를 서사 곡선으로 설계</b>하라.
상승 → 정점 → 급락(6분 이상 유지) → 1분간 재상승 → 소멸. 이 곡선만 지키면 개별 컷이 평범해도 영상 전체가 감정을 갖는다.
실무적으로는 <b>이미지 생성 프롬프트에 구간별 광원 지시를 못 박는 것</b>으로 구현된다
(황금기 = <code>warm golden hour</code>, 몰락기 = <code>cold overcast, blue hour</code>).</div>

<h3>2-3. 챕터별 색·밝기·음량</h3>
<table><tr><th>구간</th><th>챕터</th><th>길이</th><th>장면</th><th>회/분</th><th>난색</th><th>명도</th><th>dBFS</th></tr>{chrows}</table>

<h2 id="s3">3. 화면 구성 · 프레이밍 · 자막 규격</h2>
{C['CHART3']}
<table><tr><th>항목</th><th>측정값</th></tr>
<tr><td>해상도 · 프레임레이트</td><td>1280×720 / 60fps / 레터박스 없음 / 16:9 정확히 1.7778</td></tr>
<tr><td>자막 밴드</td><td>화면 높이 <b>87.6%~92.2%</b> 구간, 두께 34px(=화면의 4.7%), <b>중앙 정렬</b></td></tr>
<tr><td>자막 글자 크기</td><td>캡 높이 약 24~28px @720p = <b>화면 높이의 3.8%</b> (1080p 환산 약 41px)</td></tr>
<tr><td>자막 스타일</td><td>흰색 + 얇은 검정 외곽선. 배경 박스 없음. 1줄 원칙</td></tr>
<tr><td>워터마크</td><td>우상단, 화면 높이 2.2%~8.5% 위치, 우측 여백 1.3%, 불투명도 낮음</td></tr>
<tr><td>지평선</td><td>대부분 화면 높이 <b>55~70%</b>에 배치 (하늘을 넓게 씀)</td></tr>
<tr><td>주 피사체 위치</td><td>중앙 또는 3분할 교차점. 화면 가장자리 12% 이내에는 정보를 두지 않음</td></tr>
</table>
<h3>샷 사이즈 분포 (육안 분류, 확정 장면 72개 기준)</h3>
<table><tr><th>사이즈</th><th>비중</th><th>용례</th></tr>
<tr><td>ELS / LS (익스트림 롱·롱)</td><td><b>약 68%</b></td><td>산맥·도시·계단식 밭. 이 영상의 기본값</td></tr>
<tr><td>FS / MFS (전신·무릎)</td><td>약 14%</td><td>달리는 전령, 마을 거리</td></tr>
<tr><td>MS / MCU (허리·가슴)</td><td>약 8%</td><td>키푸를 든 관리, 황제</td></tr>
<tr><td>CU / ECU (근접·매크로)</td><td><b>약 10%</b></td><td>매듭·감자·황금·석재 이음매 — <b>전부 사물, 사람 얼굴 아님</b></td></tr>
</table>
<div class="card warn"><b>주의.</b> 일반 영화 문법은 "같은 사이즈를 반복하지 말라"고 하지만
<b>이 영상은 의도적으로 ELS/LS를 68% 반복한다.</b> 대신 리듬을 <b>고도·색온도·카메라 방향</b>으로 만든다.
AI 영상에서는 사이즈 변주보다 <b>사이즈 고정 + 다른 축의 변주</b>가 실패율이 낮다.</div>
"""
open("_b1.html","w",encoding="utf-8").write(B); print("body1",len(B)//1024,"KB")
