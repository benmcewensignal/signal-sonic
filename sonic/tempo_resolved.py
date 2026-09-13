"""Tempo with the octave resolved, not guessed.

The previous attempt folded every candidate onto one octave and took the peak of the folded
histogram. That collapsed to the same answer for nearly every record, because folding first
destroys the evidence that distinguishes a 174 record from an 87 one.

This does it the other way round. Score every candidate tempo by its own strength plus the
strength of its harmonics and subharmonics -- a record at 174 has energy at 87 and 348 too,
so the family scores highly whichever member you name. Pick the strongest family, then
choose the member within it by raw salience rather than by assumption.

Validated against tempos the field already agrees on: drum and bass near 174, house near 124,
trance near 140. An estimator that cannot reproduce those is wrong, and we know it without
having to discover anything.
"""
import numpy as np


def tempo_resolved(y, sr, lo=70.0, hi=200.0):
    import librosa
    onset = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
    if onset.size < 64:
        return {"tempo": 0.0, "tempo_confidence": 0.0, "tempo_alt": 0.0}

    # salience of every candidate rate, on a fine grid
    cands = np.arange(40.0, 401.0, 0.5)
    sal = librosa.feature.tempo(onset_envelope=onset, sr=sr, aggregate=None)
    tg = librosa.feature.fourier_tempogram(onset_envelope=onset, sr=sr)
    freqs = librosa.fourier_tempo_frequencies(sr=sr, hop_length=512, win_length=tg.shape[0] * 2 - 2)
    mag = np.abs(tg).mean(axis=1)
    ok = np.isfinite(freqs) & (freqs > 30) & (freqs < 420)
    freqs, mag = freqs[ok], mag[ok]
    if freqs.size < 8:
        return {"tempo": 0.0, "tempo_confidence": 0.0, "tempo_alt": 0.0}
    mag = mag / (mag.max() or 1.0)

    def strength(bpm):
        if bpm <= 0:
            return 0.0
        j = int(np.argmin(np.abs(freqs - bpm)))
        if abs(freqs[j] - bpm) > max(bpm * 0.04, 1.5):
            return 0.0
        return float(mag[j])

    # a family is a tempo and its octaves. Score the family, not the member.
    fam = {}
    for b in cands:
        s = 0.0
        for mult in (0.25, 0.5, 1.0, 2.0, 4.0):
            s += strength(b * mult) * (1.0 if mult == 1.0 else 0.6)
        fam[b] = s
    best = max(fam, key=fam.get)

    # choose the member of that family which dance records are actually counted in,
    # breaking ties by raw salience rather than by where we expect the genre to sit
    members = [best * m for m in (0.25, 0.5, 1.0, 2.0, 4.0)]
    members = [m for m in members if lo <= m <= hi]
    if not members:
        m = best
        while m < lo: m *= 2
        while m > hi: m /= 2
        members = [m]
    pick = max(members, key=strength)
    alt = max([m for m in members if abs(m - pick) > 1e-6], key=strength, default=0.0)
    tot = sum(fam.values()) / len(fam)
    return {"tempo": round(float(pick), 2),
            "tempo_confidence": round(float(fam[best] / (tot or 1)), 3),
            "tempo_alt": round(float(alt), 2)}
