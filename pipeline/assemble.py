# -*- coding: utf-8 -*-
import io,os
CSS=open("_css.txt",encoding="utf-8").read()
NAV=open("_nav.txt",encoding="utf-8").read()
parts=[open(f"_b{i}.html",encoding="utf-8").read() for i in (1,2,3,4,5)]
HEAD = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 역사 다큐 영상 제작 원리 해부 · 딥 호라이즌 「잉카」 1편 정밀분석</title>
<style>__CSS__</style></head><body>
<div class="wrap">
<nav>
<h4>AI VIDEO / STYLE TEARDOWN</h4>
<p class="meta">딥 호라이즌 「잉카 문명은 왜 멸망했나?」<br>19분 29초 · 1280&times;720 · 60fps<br>
프레임 70,134 전수 계산<br>경계 98개 육안 전수 검증<br>측정일 2026-09-05</p>
__NAV__
<p class="meta" style="margin-top:16px">
<button id="tg" style="width:100%;padding:7px;border-radius:7px;border:1px solid var(--line);
background:var(--chip);color:var(--ink);cursor:pointer;font-size:12.5px">라이트 / 다크 전환</button></p>
</nav>
<main>
"""
FOOT = """
<hr style="margin:60px 0 20px;border:0;border-top:1px solid var(--line)">
<p class="sub">이 문서의 모든 수치는 원본 영상 파일을 직접 디코딩해 계산한 값이다.
원본 영상·전체 자막·캡처 이미지는 분석 목적의 인용이며, 이 문서가 해당 영상·음악·이미지의
재사용 권리를 부여하지 않는다. 스타일과 제작 원리는 저작 대상이 아니지만,
개별 장면·대본·음성의 복제는 별개 문제다.</p>
</main></div>
<script>
(function(){var b=document.getElementById('tg');if(!b)return;
b.addEventListener('click',function(){var r=document.documentElement;
var cur=r.getAttribute('data-theme');
if(!cur){cur=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
r.setAttribute('data-theme',cur==='dark'?'light':'dark');});})();
</script>
</body></html>"""
html = HEAD.replace("__CSS__",CSS).replace("__NAV__",NAV) + "".join(parts) + FOOT
out=os.path.abspath("AI_VIDEO_STYLE_TEARDOWN_INCA.html")
open(out,"w",encoding="utf-8").write(html)
print("WROTE",out, round(os.path.getsize(out)/1024/1024,2),"MB")
