"""Step one of the classification: what the sound can and cannot separate among all 36 Beatport tags.

1. The mix-only model on all 36 tags, artists held out: per-tag accuracy and the confusion matrix.
   Pairs confused both ways are one sound with two names.
2. Clusters drawn from the sound alone (k-means on the standardised inputs), compared with the tags.
3. Persistence: a model trained on the earlier half of the listing months, tested on the later half.
Writes data/classification/step1.json and prints a summary.
"""
import sqlite3, json, sys, os, numpy as np, collections, warnings; warnings.filterwarnings("ignore")
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics import adjusted_mutual_info_score, confusion_matrix
db = sys.argv[1]; c = sqlite3.connect(db)
sc, wk = {}, {}
for t, s, w in c.execute("select track_id, scene, week from track_scenes where week like '____-M__' order by week"):
    sc.setdefault(t, s); wk.setdefault(t, w)
art = {}
for t, a in c.execute("select track_id, artists from track_meta"):
    try: v = (json.loads(a) if a and a.startswith("[") else [a])[0]
    except Exception: v = None
    art[t] = v if isinstance(v, str) and v else ""
KEYS = ("loudness", "bass_weight", "drum_density", "vocal_presence", "drum_swing"); X, S, A, W = [], [], [], []
for t, f in c.execute("select track_id, features from tracks where analyser_id='local' and analyser_ver like '3.0%'"):
    if t not in sc: continue
    j = json.loads(f); e = j.get("embedding"); tp = j.get("tempo"); ec = j.get("energy_curve"); rv = j.get("rhythm_vector"); sca = [j.get(k) for k in KEYS]
    if e and len(e) == 45 and tp and ec and len(ec) == 8 and isinstance(rv, list) and len(rv) == 16 and all(isinstance(x, (int, float)) for x in sca):
        X.append(e + [float(np.log2(tp * 2 if tp < 100 else tp))] + ec + sca + rv); S.append(sc[t]); A.append(art.get(t, "")); W.append(wk[t])
X = np.array(X, np.float32); S = np.array(S); A = np.array(A); W = np.array(W); tags = sorted(set(S))
print(f"{len(X):,} records across {len(tags)} tags", flush=True)
rng = np.random.default_rng(0); arts = np.array(sorted(set(A[A != ""]))); rng.shuffle(arts); hold = set(arts[:len(arts) // 5])
# named records are held out by artist; unnamed ones (most of the new genres) at random, so every tag is tested
coin = rng.random(len(X)); test_mask = np.array([(A[i] in hold) if A[i] else (coin[i] < 0.2) for i in range(len(X))])
te = np.where(test_mask)[0]; tr = np.where(~test_mask)[0]
mu, sd = X.mean(0), X.std(0) + 1e-9; Z = (X - mu) / sd
P = dict(max_iter=200, learning_rate=0.1, max_leaf_nodes=31, early_stopping=False, random_state=0)
m = HistGradientBoostingClassifier(**P).fit(Z[tr], S[tr]); pred = m.predict(Z[te])
acc = float(np.mean(pred == S[te])); ntest = {t: int(np.sum(S[te] == t)) for t in tags}
per = {t: round(float(np.mean(pred[S[te] == t] == t)), 3) for t in tags if ntest[t] >= 50}
cm = confusion_matrix(S[te], pred, labels=tags).astype(float); row = cm / cm.sum(1, keepdims=True)
pairs = []
for i, a in enumerate(tags):
    for j, b in enumerate(tags):
        if j <= i: continue
        both = min(row[i, j], row[j, i]); pairs.append((round(float(both), 3), round(float(row[i, j]), 3), round(float(row[j, i]), 3), a, b))
pairs.sort(reverse=True)
# 2. the sound's own clusters, against the tags
ami = {}
for k in (8, 12, 16, 20, 24, 30, 36):
    lab = MiniBatchKMeans(n_clusters=k, random_state=0, n_init=3, batch_size=4096).fit_predict(Z)
    ami[k] = round(float(adjusted_mutual_info_score(S, lab)), 3)
# 3. persistence: earlier listing months against later
# persistence within the year every tag shares (2025-09 to 2026-08), split at 2026-03, tags present in both halves only
shared = (W >= "2025-M09") & (W <= "2026-M08"); cut = "2026-M03"; early = shared & (W < cut); late = shared & (W >= cut)
both = set(S[early]) & set(S[late]); early &= np.isin(S, list(both)); late &= np.isin(S, list(both))
m2 = HistGradientBoostingClassifier(**P).fit(Z[early], S[early]); p2 = m2.predict(Z[late]); pers = float(np.mean(p2 == S[late]))
m3 = HistGradientBoostingClassifier(**P).fit(Z[late], S[late]); p3 = m3.predict(Z[early]); pers_back = float(np.mean(p3 == S[early]))
out = {"records": len(X), "tags": tags, "artists_held_out": len(hold), "accuracy_36": acc, "per_tag": per, "test_records_per_tag": ntest, "confused_pairs": pairs[:40],
       "cluster_agreement_ami": ami, "persistence": {"months_split_at": cut, "train_early_test_late": pers, "train_late_test_early": pers_back},
       "row_normalised_confusion": {a: {b: round(float(row[i, j]), 3) for j, b in enumerate(tags) if row[i, j] >= 0.03} for i, a in enumerate(tags)}}
os.makedirs("data/classification", exist_ok=True); json.dump(out, open("data/classification/step1.json", "w"), indent=1)
worst = sorted(per.items(), key=lambda kv: kv[1])[:8]; best = sorted(per.items(), key=lambda kv: -kv[1])[:6]
msg = (f"36 tags, {len(X):,} records, {len(hold):,} artists held out: right first time {acc*100:.1f}%. Least separable tags: " + ", ".join(f"{t} {v*100:.0f}%" for t, v in worst)
       + ". Most: " + ", ".join(f"{t} {v*100:.0f}%" for t, v in best) + ". Confused both ways: " + "; ".join(f"{a}<->{b} {x*100:.0f}%" for x, _, _, a, b in pairs[:8])
       + f". Cluster agreement with tags (AMI) k=8..36: {ami}. Persistence early->late {pers*100:.0f}%, late->early {pers_back*100:.0f}%.")
print(msg); print(f"::notice title=classification step 1::{msg[:1800]}")
