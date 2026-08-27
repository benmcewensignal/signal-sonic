"""LocalAnalyser tests on synthesized audio. Three engineered styles:

  house  — 124 BPM four-on-floor kick + deep sub bass, no melody
  breaks — 138 BPM shuffled/offset hits, minimal sub
  vocal  — sustained harmonic vowel-like tones over sparse rhythm

Assertions: tempo in range, bass_weight orders house > breaks,
swing orders breaks > house, vocal_presence orders vocal > house,
intra-style embedding distance < inter-style (the load-bearing one),
and too-short audio raises.

Run: python tests/test_local.py
"""
import os
import sys
import tempfile
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sonic.analyser_local import LocalAnalyser

SR = 22050
DUR = 12.0
PASS, FAIL = 0, []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)
    print(("  ok  " if cond else "  FAIL") + " " + name)


def _kick(t0, t, f0=150.0):
    """Pitched-down click: kick-ish."""
    dt = t - t0
    env = np.exp(-dt * 22) * (dt >= 0)
    return np.sin(2 * np.pi * (f0 * np.exp(-dt * 9) + 45) * dt) * env


def _hat(t0, t, seed):
    rs = np.random.RandomState(seed)
    dt = t - t0
    n = rs.randn(len(t)) * np.exp(-np.maximum(dt, 0) * 60) * (dt >= 0)
    return n * 0.25


def synth(style, variant):
    t = np.linspace(0, DUR, int(SR * DUR), endpoint=False)
    y = np.zeros_like(t)
    rs = np.random.RandomState(hash(style + str(variant)) % 2**31)
    if style == "house":
        beat = 60 / 124
        for i in range(int(DUR / beat)):
            y += _kick(i * beat, t)
        y += (0.35 + 0.2 * rs.rand()) * np.sin(2 * np.pi * (48 + variant * 9) * t) * (np.sin(2 * np.pi * t / (beat * 4)) * .5 + .5)
        # per-variant colouration: a continuous mid tone at a different pitch
        y += 0.12 * np.sin(2 * np.pi * (330 + variant * 55) * t)
        for i in range(int(DUR / beat)):
            y += _hat(i * beat + beat / 2, t, variant * 7 + i)
    elif style == "breaks":
        beat = 60 / 138
        offs = [0, .31, .48, .77, 1.13, 1.42, 1.69]  # uneven grid: swing
        bar = 2 * beat
        i = 0
        while i * bar < DUR:
            for o in offs:
                if rs.rand() > 0.12 * variant:  # variant-dependent dropouts
                    y += _kick(i * bar + o * beat, t, f0=170 + variant * 25 + rs.rand() * 40)
                y += _hat(i * bar + o * beat + .07, t, variant * 13 + i + int(o * 10))
            i += 1
    else:  # vocal-ish
        beat = 60 / 120
        for k, f in enumerate([220, 277, 330, 262]):
            seg = (t >= k * 3) & (t < (k + 1) * 3)
            for h, amp in [(1, 1), (2, .5), (3, .33), (4, .2)]:
                y += amp * np.sin(2 * np.pi * f * (1 + variant * .04) * h * t) * seg
            # formant-ish band emphasis
            y += .3 * np.sin(2 * np.pi * (f * 4 + 60 * np.sin(2 * np.pi * 5 * t)) * t) * seg
        for i in range(int(DUR / beat)):
            y += 0.3 * _kick(i * beat, t)
    y = y / (np.max(np.abs(y)) or 1)
    return y.astype(np.float32)


def main():
    an = LocalAnalyser()
    tmp = tempfile.mkdtemp()
    feats = {}
    for style in ("house", "breaks", "vocal"):
        feats[style] = []
        for v in range(3):
            p = os.path.join(tmp, f"{style}{v}.wav")
            sf.write(p, synth(style, v), SR)
            feats[style].append(an.analyse(p))

    h, b, vv = feats["house"], feats["breaks"], feats["vocal"]

    check("house tempo ≈ 124 (±6 or half/double)",
          any(abs(f.tempo - m) < 8 for f in h for m in (124, 62, 248)))
    check("bass_weight: house > breaks",
          np.mean([f.bass_weight for f in h]) > np.mean([f.bass_weight for f in b]))
    check("swing: breaks > house",
          np.mean([f.drum_swing for f in b]) > np.mean([f.drum_swing for f in h]))
    check("vocal_presence: vocal > house",
          np.mean([f.vocal_presence for f in vv]) > np.mean([f.vocal_presence for f in h]))
    check("energy curve normalised to [0,1] with a 1.0 peak",
          all(max(f.energy_curve) == 1.0 and min(f.energy_curve) >= 0 for f in h + b + vv))
    check("embedding is unit-norm EMBED_DIM",
          all(abs(sum(x * x for x in f.embedding) - 1) < 1e-6 for f in h + b + vv))

    def cd(a, bb):
        return 1 - sum(x * y for x, y in zip(a.embedding, bb.embedding))
    intra = np.mean([cd(g[i], g[j]) for g in (h, b, vv) for i in range(3) for j in range(i + 1, 3)])
    inter = np.mean([cd(x, y) for x in h for y in b] + [cd(x, y) for x in h for y in vv] +
                    [cd(x, y) for x in b for y in vv])
    check(f"styles separate: 0 < intra {intra:.3f} < inter {inter:.3f}, margin ≥ 2x",
          0 < intra and inter > intra * 2)
    check("analyser_id/vintage stamped",
          all(f.analyser_id == "local" and f.analyser_version == "1" for f in h))

    try:
        p = os.path.join(tmp, "short.wav")
        sf.write(p, np.zeros(2000, dtype=np.float32), SR)
        an.analyse(p)
        check("too-short audio raises", False)
    except ValueError:
        check("too-short audio raises", True)

    print(f"\n{PASS} passed, {len(FAIL)} failed" + (f": {FAIL}" if FAIL else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
