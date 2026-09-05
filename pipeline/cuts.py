import re,sys,json,os
# 이 단계는 3단계 ffmpeg 덤프만 읽는다. 원본 영상 파일은 필요 없다.
SP=os.environ["SP"]
scd=os.path.join(SP,"an","scd.txt"); yav=os.path.join(SP,"an","yavg.txt")

def parse(path,key):
    t=None; out=[]
    pat_t=re.compile(r"pts_time:([0-9.]+)")
    pat_v=re.compile(re.escape(key)+r"=([0-9.\-eE]+)")
    with open(path,encoding="utf-8",errors="ignore") as f:
        for ln in f:
            m=pat_t.search(ln)
            if m: t=float(m.group(1)); continue
            m=pat_v.search(ln)
            if m and t is not None: out.append((t,float(m.group(1))))
    return out

scores=parse(scd,"lavfi.scd.score")
lumas =parse(yav,"lavfi.signalstats.YAVG")
print(f"frames scored={len(scores)} luma={len(lumas)}",file=sys.stderr)
lm={round(t,3):v for t,v in lumas}
ts=[t for t,_ in scores]; sv=[v for _,v in scores]
n=len(sv)
# ---- cut detection: spike above absolute floor AND above local median*k
def median(a):
    b=sorted(a); m=len(b)//2
    return b[m] if len(b)%2 else (b[m-1]+b[m])/2
W=60
cuts=[]
for i in range(1,n):
    v=sv[i]
    if v<12: continue
    lo=max(0,i-W); hi=min(n,i+W)
    win=sv[lo:hi]
    med=median(win)+1e-6
    if v>=28 or (v>=12 and v>med*4.0):
        cuts.append(i)
# suppress neighbours within 6 frames -> keep max
keep=[]
for i in cuts:
    if keep and i-keep[-1][0]<=6:
        if sv[i]>sv[keep[-1][0]]: keep[-1]=(i,sv[i])
    else: keep.append((i,sv[i]))
cuts=[i for i,_ in keep]
# ---- classify transition at each cut
def cls(i):
    v=sv[i]
    pre=sv[max(0,i-6):i]; post=sv[i+1:i+7]
    prem=sum(pre)/max(1,len(pre)); postm=sum(post)/max(1,len(post))
    y=[lm.get(round(t,3)) for t in ts[max(0,i-10):min(n,i+10)]]
    y=[q for q in y if q is not None]
    ymin=min(y) if y else None
    kind="HARD_CUT"; conf="high"
    if ymin is not None and ymin<18: kind="FADE_THROUGH_BLACK"
    elif prem>5 and postm>5 and v<40: kind="DISSOLVE/BLEND"; conf="med"
    elif prem>3.5 or postm>3.5: kind="SOFT_CUT(motion)"; conf="med"
    return kind,round(v,1),round(prem,2),round(postm,2),(round(ymin,1) if ymin is not None else None)
shots=[]
bounds=[0]+cuts+[n-1]
for k in range(len(bounds)-1):
    a=bounds[k]; b=bounds[k+1]
    st=ts[a]; en=ts[b]
    if en-st<0.10: continue
    kind,v,pm,qm,ym = cls(a) if a>0 else ("OPEN",0,0,0,None)
    seg=sv[a+2:b-1] if b-a>4 else sv[a:b]
    motion=round(sum(seg)/max(1,len(seg)),2)
    ys=[lm.get(round(t,3)) for t in ts[a:b]]; ys=[q for q in ys if q is not None]
    shots.append(dict(idx=len(shots)+1,start=round(st,3),end=round(en,3),dur=round(en-st,3),
        in_transition=kind,cut_score=v,pre=pm,post=qm,minY=ym,
        motion=motion,meanY=round(sum(ys)/len(ys),1) if ys else None))
json.dump(shots,open(os.path.join(SP,"an","shots.json"),"w"),ensure_ascii=False,indent=1)
tot=shots[-1]["end"] if shots else 0
print(f"SHOTS={len(shots)} totaldur={tot:.1f}s avg={tot/max(1,len(shots)):.2f}s")
from collections import Counter
print(Counter(s["in_transition"] for s in shots))
import statistics
d=[s["dur"] for s in shots]
print("median",round(statistics.median(d),2),"p10",round(sorted(d)[len(d)//10],2),"p90",round(sorted(d)[len(d)*9//10],2),"max",round(max(d),2))
