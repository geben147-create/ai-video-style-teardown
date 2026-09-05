import subprocess,sys,os,numpy as np,json
SP=os.environ["SP"]; V=sys.argv[1]
W,H,FPS=96,54,10
cmd=["ffmpeg","-hide_banner","-loglevel","error","-i",V,"-an",
     "-vf",f"fps={FPS},scale={W}:{H}:flags=area,format=rgb24",
     "-fps_mode","passthrough","-f","rawvideo","-pix_fmt","rgb24","-"]
p=subprocess.Popen(cmd,stdout=subprocess.PIPE,bufsize=10**8)
sz=W*H*3; frames=[]
while True:
    b=p.stdout.read(sz)
    if len(b)<sz: break
    frames.append(np.frombuffer(b,dtype=np.uint8).reshape(H,W,3))
p.stdout.close(); p.wait()
A=np.stack(frames).astype(np.float32)
print("frames",A.shape, "dur", A.shape[0]/FPS, file=sys.stderr)
np.save(os.path.join(SP,"an","rgb.npy"),A.astype(np.uint8))
