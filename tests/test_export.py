import json, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from sonic.store import Store
from sonic.export_demand import compute, SITE

PASS=0; FAIL=[]
def check(name, cond):
    global PASS
    print(("  ok   " if cond else "  FAIL ")+name)
    if cond: PASS+=1
    else: FAIL.append(name)

def main():
    db=os.path.join(tempfile.mkdtemp(),"e.db")
    st=Store(db)
    rs=np.random.RandomState(0)
    import datetime as _dt
    _now=_dt.date.today()
    months=[]
    for back in (3,2,1,0):
        mm=_now.month-back; yy=_now.year
        while mm<1: mm+=12; yy-=1
        months.append(f"{yy}-M{mm:02d}")
    for scene in ("house","deep-house","uk-garage-speed-garage"):
        base=rs.randn(32)
        for mi,m in enumerate(months):
            for i in range(12):
                e=base+rs.randn(32)*0.05+(mi*0.3 if scene=="uk-garage-speed-garage" else 0)
                e=(e/np.linalg.norm(e)).tolist()
                tid=f"{scene}-{m}-{i}"
                st.conn.execute("INSERT OR REPLACE INTO tracks(track_id,analyser_id,analyser_ver,features,source,first_seen,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
                    (tid,"local","t",json.dumps({"embedding":e}),"test",m))
                st.conn.execute("INSERT OR REPLACE INTO track_scenes(track_id,scene,weight,chart_rank,source,week) VALUES(?,?,?,?,?,?)",
                    (tid,scene,1.0,i+1,"test",m))
            st.conn.execute("INSERT OR REPLACE INTO scene_weeks(scene,week,analyser_id,weighting,fingerprint,n_tracks,created_at) VALUES(?,?,?,'chart',?,?,datetime('now'))",(scene,m,"local","{}",12))
    st.conn.commit()
    p=compute(st)
    check("payload produced", p is not None)
    check("contract keys", all(k in p for k in ("week","analyser","mapping_version","scenes")))
    hs=p["scenes"].get("House — Classic/Deep")
    check("house charts merged", hs is not None and hs["n"]==24)
    g=p["scenes"].get("UK Garage / Speed Garage")
    check("mover scores higher than stayer", g["sonic"]>hs["sonic"])
    check("sonic within 0-100", all(0<=r["sonic"]<=100 for r in p["scenes"].values()))
    print(f"\n{PASS} passed, {len(FAIL)} failed"+(f": {FAIL}" if FAIL else ""))
    if FAIL: sys.exit(1)

if __name__=="__main__":
    main()
