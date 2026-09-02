import sqlite3, numpy as np, json, time, collections
t0=time.time(); DFMIN,DFMAX=9,40; MINV=40; PER_SCENE=60; BK=8
c=sqlite3.connect('/tmp/fp4.db')
EXCLUDE={'bp:7460088'}   # degenerate fingerprint: weakly aligns with hundreds of records at the frame boundary (noise sweep / wash), not shared material
ids=[r[0] for r in c.execute("select track_id from fp_tracks order by rowid")]
NT=len(ids); pos={t:i for i,t in enumerate(ids)}
# sources: most recent PER_SCENE fingerprinted tracks per scene
c2=sqlite3.connect('/tmp/chk.db')
src=set()
for sc, in c2.execute("select distinct scene from track_scenes where week like '____-M__'"):
    rows=c2.execute("select track_id from track_scenes where scene=? and week like '____-M__' order by week desc",(sc,)).fetchall()
    n=0
    for (t,) in rows:
        if t in pos: src.add(pos[t]); n+=1
        if n>=PER_SCENE: break
EXCL_IDX=np.array([pos[t] for t in EXCLUDE if t in pos],dtype=np.int32)
src=np.array(sorted(x for x in src if x not in set(EXCL_IDX.tolist()))); print("sources:",len(src),flush=True)
acc={int(i):[] for i in src}   # per source: list of (keys int32, counts uint16)
for k in range(BK):
    Hs=[];Ts=[];Fs=[]
    for i,(hb,fb) in enumerate(c.execute("select hashes, frames from fp_tracks order by rowid")):
        H=np.frombuffer(hb,dtype=np.uint32); F=np.frombuffer(fb,dtype=np.uint16)
        m=(H>>29)==k
        if not m.any(): continue
        H,F=H[m],F[m]
        o=np.argsort(H,kind='stable'); H,F=H[o],F[o]
        keep=np.concatenate(([True],H[1:]!=H[:-1])); H,F=H[keep],F[keep]
        Hs.append(H);Ts.append(np.full(len(H),i,dtype=np.uint16));Fs.append(F)
    if not Hs: continue
    H=np.concatenate(Hs);T=np.concatenate(Ts);F=np.concatenate(Fs);Hs=Ts=Fs=None
    o=np.argsort(H,kind='stable');H,T,F=H[o],T[o],F[o];del o
    u,st,cnt=np.unique(H,return_index=True,return_counts=True)
    sel=(cnt>=DFMIN)&(cnt<=DFMAX); u,st,cnt=u[sel],st[sel],cnt[sel]
    # source postings within this bucket
    srcmask=np.isin(T,src)
    for i in np.unique(T[srcmask]):
        i=int(i)
        rows_i=np.flatnonzero((T==i)&srcmask)
        myH=H[rows_i]; myF=F[rows_i].astype(np.int32)
        p=np.searchsorted(u,myH); ok=(p<len(u))&(u[np.minimum(p,len(u)-1)]==myH)
        if not ok.any(): continue
        p,myF=p[ok],myF[ok]; s_=st[p]; n_=cnt[p]; tot=int(n_.sum())
        exp=np.repeat(s_,n_)+(np.arange(tot)-np.repeat(np.cumsum(n_)-n_,n_))
        oth=T[exp].astype(np.int32); dt=(np.repeat(myF,n_)-F[exp].astype(np.int32))
        m=(oth!=i)&(~np.isin(oth,EXCL_IDX))
        key=((oth[m].astype(np.int64)<<13)|(dt[m]+4096)).astype(np.int64)
        uk,ck=np.unique(key,return_counts=True)
        acc[i].append((uk,ck.astype(np.int32)))
    del H,T,F,u,st,cnt
    print(f"bucket {k} done {time.time()-t0:.0f}s",flush=True)
pairs={}
for i,parts in acc.items():
    if not parts: continue
    K=np.concatenate([p[0] for p in parts]); C=np.concatenate([p[1] for p in parts])
    o=np.argsort(K,kind='stable'); K,C=K[o],C[o]
    stt=np.flatnonzero(np.concatenate(([True],K[1:]!=K[:-1])))
    K2=K[stt]; C2=np.add.reduceat(C,stt)
    sm=C2.copy(); adj=(K2[1:]-K2[:-1]==1); sm[1:][adj]+=C2[:-1][adj]; sm[:-1][adj]+=C2[1:][adj]
    good=sm>=MINV
    for kk,v in zip(K2[good],sm[good]):
        j=int(kk>>13); d=int(kk&0x1fff)-4096
        key=(min(i,j),max(i,j))
        if key not in pairs or v>pairs[key][0]: pairs[key]=(int(v),d)
print(f"aligned commons pairs >= {MINV}: {len(pairs)}  {time.time()-t0:.0f}s",flush=True)
parent=list(range(NT))
def find(x):
    while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
    return x
for (i,j) in pairs:
    ri,rj=find(i),find(j)
    if ri!=rj: parent[ri]=rj
clus=collections.defaultdict(list)
for i in set(p for pr in pairs for p in pr): clus[find(i)].append(i)
print("clusters:",len(clus),"sizes:",sorted([len(v) for v in clus.values()],reverse=True)[:12])
json.dump({"params":{"dfmin":DFMIN,"dfmax":DFMAX,"minv":MINV,"sources":int(len(src)),"per_scene":PER_SCENE},
  "pairs":[[ids[i],ids[j],v,d] for (i,j),(v,d) in pairs.items()],
  "clusters":[[ids[i] for i in v] for v in sorted(clus.values(),key=len,reverse=True)]},open("/tmp/commons/commons_pass.json","w"))
print("saved")
