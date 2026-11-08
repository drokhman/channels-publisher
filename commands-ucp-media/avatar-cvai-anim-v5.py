from PIL import Image, ImageFilter, ImageDraw
import numpy as np, math, os, shutil
src=Image.open('cur.jpg').convert('RGB'); a=np.array(src).astype(float)
S=800; IMG=600; off=100
yy,xx=np.mgrid[0:S,0:S]; r=np.clip(np.hypot(xx-S/2,yy-S/2)/(S/2),0,1)
edge=np.array([1,15,37.]); cen=a[150:170,20:30].reshape(-1,3).mean(0)
bg=cen*(1-r[...,None])+edge*r[...,None]
big=np.array(src.resize((IMG,IMG),Image.LANCZOS).filter(ImageFilter.UnsharpMask(2,60,2))).astype(float)
mi=Image.new('L',(IMG,IMG),0); ImageDraw.Draw(mi).rectangle((40,40,IMG-40,IMG-40),fill=255)
fm=np.array(mi.filter(ImageFilter.GaussianBlur(30)))/255.
base=bg.copy(); sl=(slice(off,off+IMG),slice(off,off+IMG))
base[sl]=big*fm[...,None]+bg[sl]*(1-fm[...,None])
# вырезаем коня (с ореолом)
d=np.abs(base-bg).sum(2)
alpha=np.clip((d-12)/60,0,1)
alpha=np.array(Image.fromarray((alpha*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1)))/255.
K=np.where(alpha[...,None]>0.02,(base-bg*(1-alpha[...,None]))/np.maximum(alpha[...,None],1e-3),0)
K=np.clip(K,0,255)
cyan=np.clip((base[...,2]-base[...,0]-60)/120,0,1)*np.clip((base.sum(2)-150)/300,0,1); cyan[:,:S//2-10]=0
glow=np.array(Image.fromarray((cyan*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)))/255.
ys,xs=np.where(alpha>0.3); cx=(xs.min()+xs.max())/2; foot=ys.max()
print('knight box',xs.min(),xs.max(),ys.min(),ys.max())
def ease(u): u=min(max(u,0),1); return u*u*(3-2*u)
fps=30; T=8.0; N=int(T*fps)
STEP=48
# ход: (0,0)->(0,-2)->(+1,-2) потом обратно (+1,-2)->(+1,0)->(0,0)
def move(u, p0, p1):  # прыжок по Г
    pa=np.array(p0,float); pb=np.array(p1,float)
    mid=np.array([pa[0],pb[1]]) if abs(pb[1]-pa[1])>=abs(pb[0]-pa[0]) else np.array([pb[0],pa[1]])
    if p0==(1,-2): mid=np.array([pb[0]+1,pa[1]+0.0]); mid=np.array([pa[0],pb[1]])  # обратно: сначала вниз
    v=ease(u)
    if v<0.66: p=pa+(mid-pa)*(v/0.66)
    else: p=mid+(pb-mid)*((v-0.66)/0.34)
    lift=math.sin(math.pi*u)
    return p*STEP, lift
os.makedirs('fr2',exist_ok=True); shutil.rmtree('fr2'); os.makedirs('fr2')
for i in range(N):
    t=i/fps
    dx=dy=0.; lift=0.; ang=0.; board=0.
    if 0.6<=t<1.8: (dx,dy),lift=move((t-0.6)/1.2,(0,0),(1,-2))
    elif 1.8<=t<2.2: dx,dy=STEP,-2*STEP
    elif 2.2<=t<3.4: (dx,dy),lift=move((t-2.2)/1.2,(1,-2),(0,0))
    if 0.4<=t<3.6: board=math.sin(math.pi*(t-0.4)/3.2)
    if 3.8<=t<6.0: ang=2*math.pi*ease((t-3.8)/2.2)
    # фон + доска
    f=bg.copy()
    if board>0:
        q=STEP; gx=((xx-cx)//q).astype(int); gy=((yy-foot+8)//q).astype(int)
        chk=((gx+gy)%2==0)*1.0
        fade=np.clip(1-np.hypot(xx-cx-STEP/2,yy-foot+STEP)/330,0,1)
        f+= (chk*0.22+0.04)[...,None]*np.array([40,120,180.])*fade[...,None]*board
    # трансформ коня: поворот вокруг вертикальной оси + подъём
    c=math.cos(ang); sx=max(abs(c),0.03); mirror=c<0
    sc=1+0.06*lift
    kimg=Image.fromarray(np.dstack([K,alpha*255]).astype(np.uint8),'RGBA')
    if mirror: kimg=kimg.transpose(Image.FLIP_LEFT_RIGHT)
    w=int(S*sx*sc); h=int(S*sc)
    kimg=kimg.resize((max(w,1),h),Image.LANCZOS)
    # тень
    fr=Image.fromarray(np.clip(f,0,255).astype(np.uint8))
    sh=Image.new('L',(S,S),0); sw=int(150*sx+40)*(1-0.3*lift)
    sxc=cx+dx; syc=foot+dy+6
    ImageDraw.Draw(sh).ellipse((sxc-sw,syc-10,sxc+sw,syc+10),fill=int(140*(1-0.5*lift)))
    sh=sh.filter(ImageFilter.GaussianBlur(10))
    fr=Image.composite(Image.new('RGB',(S,S),(0,4,12)),fr,sh)
    px=int(cx+dx - (cx)*sx*sc) if not mirror else int(cx+dx-(S-cx)*sx*sc)
    py=int(foot+dy-lift*28 - foot*sc)
    # яркость при повороте ребром
    kk=np.array(kimg).astype(float); shade=0.75+0.25*sx
    kk[...,:3]*=shade
    fr.paste(Image.fromarray(kk.astype(np.uint8),'RGBA'),(px,py),Image.fromarray(kk[...,3].astype(np.uint8)))
    out=np.array(fr).astype(float)
    # импульсы и блик только когда конь на месте и не повёрнут
    still = (dx==0 and dy==0 and ang==0)
    if still:
        env=1.0 if t>6.0 else (0 if t<0.6 else 0)
        if t>=6.0:
            u=(t-6.0)/(T-6.0); e=math.sin(math.pi*u)**2
            yc=S*(1.05-1.2*u); band=np.exp(-((yy-yc)/45)**2)
            out+=((cyan*2.2+glow*1.6)*band*e)[...,None]*np.array([60,200,255.])
        if t<0.6:
            u=t/0.6; e=math.sin(math.pi*u)**2
            out+=(glow*0.7*e)[...,None]*np.array([60,200,255.])
    Image.fromarray(np.clip(out,0,255).astype(np.uint8)).save(f'fr2/f{i:03d}.png')
