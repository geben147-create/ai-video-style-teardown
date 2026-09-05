# -*- coding: utf-8 -*-
import json
FIG=json.load(open("_fig.json"))
B="""
<h2 id="s12">12. 편집 · 효과 · 카메라</h2>
<h3>12-1. 편집 타임라인 구조 (역추론)</h3>
<pre>트랙 1  생성 클립  (100~120개 · 각 5~10초)
트랙 2  크로스 디졸브 / 통과 전환  (0.3~1.6초, 클립 경계마다)
트랙 3  자막  (531큐 · 평균 1.41초 · 화면 높이 87.6~92.2%)
트랙 4  워터마크  (우상단 고정, 전 구간)
트랙 5  내레이션  (64% 점유 · 마스터 클록)
트랙 6  음악  (−5.6dB 덕킹 · 저역 중심)
트랙 7  추상 그래픽 오버레이  (붉은 감염망 · 황금 도로망 — 총 6% 구간)</pre>
<div class="card"><b>핵심.</b> <b>내레이션이 마스터 클록</b>이다. 그림이 아니라 말이 타임라인을 정한다.
장면 길이(중앙값 11.9초)가 자막 큐(1.41초)의 정수배가 아닌 것은,
컷을 문장이 아니라 <b>문단</b>에 맞췄다는 뜻이다. 대략 <b>자막 8개 = 장면 1개</b>다.</div>

<h3>12-2. 사용된 효과 목록 (관측된 것만)</h3>
<table><tr><th>효과</th><th>사용 여부</th><th>용례</th></tr>
<tr><td>크로스 디졸브</td><td class="yes">사용</td><td>전환의 18.3%</td></tr>
<tr><td>화이트/블랙 통과</td><td class="yes">사용</td><td>전환의 19.8%</td></tr>
<tr><td>모션 블러 강조</td><td class="yes">사용</td><td>13:31~13:38 단 8초</td></tr>
<tr><td>색 오버레이 (붉은 감염망)</td><td class="yes">사용</td><td>10:52~11:27</td></tr>
<tr><td>발광 라인 (황금 도로망)</td><td class="yes">사용</td><td>15:08~15:25</td></tr>
<tr><td>필름 그레인 / 종이 질감</td><td class="yes">사용</td><td>전 구간 상시</td></tr>
<tr><td>비네팅</td><td class="yes">약하게 사용</td><td>어두운 실내 장면</td></tr>
<tr><td>화면 분할 · PIP</td><td class="no">미사용</td><td>—</td></tr>
<tr><td>줌 펀치 · 셰이크 · 글리치</td><td class="no">미사용</td><td>—</td></tr>
<tr><td>슬라이드 / 와이프 / 회전 전환</td><td class="no">미사용</td><td>—</td></tr>
<tr><td>텍스트 애니메이션 · 카운터 · 지도 핀</td><td class="no">미사용</td><td>—</td></tr>
<tr><td>효과음(SFX) 강조</td><td class="no">거의 미사용</td><td>—</td></tr>
</table>
<div class="card ok"><b>규칙 — 효과는 7종으로 끝.</b> 이 영상이 고급스러워 보이는 이유는
효과를 많이 써서가 아니라 <b>안 쓴 것이 훨씬 많기 때문</b>이다.
"AI 티"는 대개 생성 품질이 아니라 <b>편집 효과 남용</b>에서 나온다.</div>

<h3>12-3. 카메라 지시 4요소 (보유 가이드 💗03 적용형)</h3>
<pre>MOVEMENT  하강 틸트 (기본값) / 좌우 팬 / 느린 푸시인 / 풀백
          — 한 클립에 하나만
SPEED     매우 느림. 화면폭의 0.5~0.7% / 프레임. 가감속 없음
FRAMING   이동 중 지평선을 화면 55~70% 높이에 고정.
          피사체를 중앙 1/3 밖으로 내보내지 않음
END       마지막 0.5초에 감속해 정지. 차폐물 통과 전환이면
          END = "화면이 100% 덮인 상태"

감정별 선택
  경외·규모  → 느린 풀백 + 하강 틸트
  압박·불안  → 아주 느린 푸시인 (줌 아님)
  이동·진행  → 사선 팬 (길의 방향과 일치)
  공개·반전  → 차폐물 통과 후 즉시 정지
  종결      → 상승 틸트로 하늘을 비우며 끝</pre>

<h2 id="s13">13. IMAGE / VIDEO PROMPT 작성법</h2>
<h3>13-1. 이미지 프롬프트 6블록 템플릿</h3>
<pre>[1 STYLE LOCK]  (전 컷 동일 · 절대 바꾸지 않음)
painterly matte painting, gouache and oil texture, no linework,
muted desaturated palette, strong atmospheric perspective with 4 haze layers,
low-angle golden light, lifted blacks, subtle paper grain, cinematic 16:9

[2 PALETTE]  (구간별로만 교체)
황금기 : dominant warm ochre #B48352 and terracotta #7A5F3E,
         teal shadows #33555D, warm golden hour
몰락기 : dominant slate teal #1B3539 and #33555D,
         minimal warm accent, cold overcast blue hour
예약색 : (전염 구간에만) glowing crimson veins as the ONLY saturated color

[3 SUBJECT]  (컷마다 교체 — 대본의 구체 명사 1개)
massive polygonal stone wall, mortarless joints, moss in the seams

[4 SCENE]
high andean ridge above a sea of clouds, terraced slopes descending,
a winding stone road threading the ridgeline

[5 COMPOSITION]
extreme long shot, horizon at 62% frame height,
human figure occupying less than 5% of frame, centered subject

[6 NEGATIVE]
no text, no logo, no watermark, no modern objects, no lens flare,
no close-up faces, no multiple identical people, no extra fingers,
no oversaturated colors, no harsh midday sun, no photorealism</pre>

<h3>13-2. 영상 프롬프트 8필드</h3>
<pre>SUBJECT + ACTION   : 무엇이 화면에 있고 무엇을 하는가 (동작 1개만)
SCENE + ENVIRONMENT: 장소·시간·날씨·대기
VISUAL STYLE       : [1 STYLE LOCK] 전문 붙여넣기
CAMERA MOVEMENT    : 무브 1개 (예: slow downward tilt)
CAMERA SPEED       : "very slow, constant, no acceleration"
FRAMING LOCK       : "keep horizon at 62% height, subject centered"
END STATE          : "settle to a stop, hold last 12 frames"
                     (통과 전환이면 "frame fully filled with white mist")
DURATION           : 목적별 — 인서트 3~4초 / 풍경 6~10초 / 통과 전환 클립 5~7초

NEGATIVE           : no camera shake, no zoom, no rotation,
                     no new objects appearing, no people entering frame</pre>

<h3>13-3. 컷 발주표 필드 (자동화용)</h3>
<pre>scene_id | chapter | narration_text | key_noun | scene_type(1~8)
| shot_size | palette_mode(warm/cool/reserved) | camera_move | camera_end
| transition_in(A~F) | duration_s | image_prompt | video_prompt
| status(ACCEPT / EDIT_ONLY / REGENERATE) | qc_note</pre>
<div class="card ok"><b>작업 순서.</b> ① 대본 확정 → ② 구체 명사에 밑줄 → ③ 명사마다 scene_id 발급
→ ④ 챕터별 palette_mode 지정 → ⑤ 이미지 생성 → ⑥ 승인된 이미지만 영상 생성
→ ⑦ 전환 유형 지정 후 편집 → ⑧ 내레이션 기준 정렬.
<b>내레이션 녹음이 먼저다.</b> 실측 발화 길이가 없으면 컷 길이를 정할 수 없다.</div>

<h2 id="s14">14. 금지사항</h2>
<div class="card bad">
<h4 style="margin-top:0">화면</h4>
<ul>
<li><span class="no">금지</span> 얼굴 클로즈업(CU/BCU/ECU). 이 영상에 0회다.</li>
<li><span class="no">금지</span> 한 화면에 식별 가능한 얼굴 2개 이상.</li>
<li><span class="no">금지</span> 인물의 복잡한 손동작 근접(물건 조작·글쓰기·전투).</li>
<li><span class="no">금지</span> 화면 안 텍스트 생성. 글자는 전부 후편집 오버레이.</li>
<li><span class="no">금지</span> 채도 높은 색을 아무 데나 사용. 순색 적·금은 예약 구간에만.</li>
<li><span class="no">금지</span> 정오광·강한 직사광. 광원은 항상 낮은 각도.</li>
<li><span class="no">금지</span> 순수 검정·순수 흰색 영역 (통과 전환 순간 제외).</li>
</ul>
<h4>카메라</h4>
<ul>
<li><span class="no">금지</span> 한 클립에 무브 2개 이상 (회전+줌, 팬+상승 등).</li>
<li><span class="no">금지</span> 빠른 카메라. 화면폭 1%/프레임을 넘기지 않는다.</li>
<li><span class="no">금지</span> 핸드헬드 흔들림·줌 펀치·글리치.</li>
<li><span class="no">금지</span> END 상태 미지정. 끝 1초가 붕괴하는 주원인.</li>
</ul>
<h4>편집</h4>
<ul>
<li><span class="no">금지</span> 슬라이드·와이프·회전·큐브 등 템플릿 전환.</li>
<li><span class="no">금지</span> 같은 화제 안에서 하드컷 남발. 하드컷은 화제 전환에만.</li>
<li><span class="no">금지</span> 컷을 3초 미만으로 잘게 쪼개기. 이 포맷은 말이 빠르고 그림이 느리다.</li>
<li><span class="no">금지</span> 자막을 2줄 이상으로. 1큐 1줄, 평균 4.4단어.</li>
<li><span class="no">금지</span> 침묵 구간을 음악이나 효과음으로 메우기.</li>
</ul>
<h4>대본</h4>
<ul>
<li><span class="no">금지</span> 그림이 안 되는 추상어("번영했다", "위대했다"). 구체 명사로 치환한다.</li>
<li><span class="no">금지</span> 결말을 끝까지 숨기기. 후크에서 결말을 먼저 말한다.</li>
<li><span class="no">금지</span> 심어 놓은 복선을 회수하지 않기.</li>
<li><span class="no">금지</span> 한 챕터 120초 초과. 85초가 기준이다.</li>
</ul>
<h4>생성</h4>
<ul>
<li><span class="no">금지</span> 모델 최대 길이로 채워 발주하기. 단일 사건 길이로 짧게 끊는다.</li>
<li><span class="no">금지</span> 실패 컷 때문에 긴 클립 전체를 재생성. 실패한 짧은 컷만 교체.</li>
<li><span class="no">금지</span> 스타일 잠금 문장을 컷마다 바꾸기. 전 컷 동일 문자열이어야 한다.</li>
<li><span class="no">금지</span> 유료 전환·크레딧 차감을 확인 없이 진행. 견적을 제출 직전 입력으로 재확인한다.</li>
</ul>
</div>
"""
open("_b4.html","w",encoding="utf-8").write(B)
print("body4 ok",len(B)//1024,"KB")
