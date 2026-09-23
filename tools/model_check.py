"""The scene model on real previews: the analyser's 75 numbers against the device's own, through the
deployed model file. Agreement, accuracy against the tag, and how often each confidence tier is right."""
import json, pickle, sys, numpy as np
M = pickle.load(open(sys.argv[1], "rb")); O = json.load(open("portcheck/oracle.json")); dev = json.load(open("portcheck/device.json"))
def vec(o):
    tp = o["tempo"]; tp = tp * 2 if tp < 100 else tp
    return np.array(o["embedding"] + [np.log2(tp)] + o["energy_curve"] + [o["loudness"], o["bass_weight"], o["drum_density"], o["vocal_presence"], o["drum_swing"]] + o["rhythm_vector"], float)
def call(x):
    p = M["model"].predict_proba(((np.array(x, float) - np.array(M["mu"])) / np.array(M["sd"]))[None, :])[0]; k = int(np.argmax(p)); return M["classes"][k], float(p[k])
A = [call(vec(o)) for o in O]; D = [call(x) for x in dev]; tag = [o["scene"] for o in O]
same = np.mean([a[0] == d[0] for a, d in zip(A, D)]); accA = np.mean([a[0] == t for a, t in zip(A, tag)]); accD = np.mean([d[0] == t for d, t in zip(D, tag)])
maxdiff = max(float(np.max(np.abs(vec(o) - np.array(x)))) for o, x in zip(O, dev))
tiers = []
for lo, hi in ((0.6, 1.01), (0.4, 0.6), (0, 0.4)):
    k = [i for i, d in enumerate(D) if lo <= d[1] < hi]
    tiers.append(f"{lo:.1f}+: {len(k)} calls, right {np.mean([D[i][0] == tag[i] for i in k])*100:.0f}%" if k else f"{lo:.1f}+: none")
msg = (f"{len(O)} real previews: device and analyser numbers give the same call {same*100:.0f}%; right against the tag, analyser {accA*100:.0f}%, device {accD*100:.0f}% "
       f"(model's held-out figure {M.get('held_out_accuracy', 0)*100:.0f}%); largest input difference {maxdiff:.1e}; by the device's confidence: " + "; ".join(tiers))
print(msg); print(f"::notice title=scene model on real previews::{msg}")
