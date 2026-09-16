"""Loud parts and quiet parts, measured apart.

Every number in the corpus is an average over the whole preview. A record's drop and its
breakdown are different music, and the average of them is neither: it loses exactly the
contrast a listener uses to place a record. A trance breakdown is what makes it trance.

So split the preview by loudness, measure the loudest eighth and the quietest eighth
separately, and report both plus the gap between them. The gap is the part that is new: a
record whose loud and quiet halves sound alike is a different kind of record from one where
they do not, and nothing we hold today can tell them apart.
"""
import numpy as np


def segments(y, sr, n_mfcc=6):
    import librosa
    try:
        hop = 512
        rms = librosa.feature.rms(y=y, hop_length=hop)[0]
        if rms.size < 24:
            return {"seg_error": "too short"}
        # Coefficient zero is log energy, so a loud section differs from a quiet one on it
        # whatever the music does. Keeping it made every record look like it had a breakdown.
        # Take the shape coefficients only, and normalise each frame so the comparison is
        # about timbre rather than level.
        mf = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc + 1, hop_length=hop)[1:]
        con = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=hop)
        n = min(rms.size, mf.shape[1], con.shape[1])
        rms, mf, con = rms[:n], mf[:, :n], con[:, :n]

        # Ranking single frames compares hits against the silence between them, which every
        # record has: a track that never breaks down scored the same gap as one that does.
        # Smooth over about two seconds first, so the ranking is over sections of music.
        win = max(3, int(round(2.0 * sr / hop)))
        pad = np.pad(rms, (win // 2, win // 2), mode="edge")
        smooth = np.convolve(pad, np.ones(win) / win, mode="valid")[:n]
        k = max(4, n // 8)
        order = np.argsort(smooth)
        quiet, loud = order[:k], order[-k:]

        def block(ix):
            seg = mf[:, ix]
            nrm = seg / (np.abs(seg).sum(axis=0, keepdims=True) + 1e-9)   # shape, not level
            return (list(np.round(nrm.mean(axis=1), 4)) +
                    [round(float(con[:, ix].mean()), 4)])

        lo, hi = block(quiet), block(loud)
        gap = [round(float(a - b), 4) for a, b in zip(hi, lo)]
        # how much of the record is loud at all: a track that is loud throughout and one that
        # spends half its time in a breakdown can share every average we hold
        share = float(np.mean(smooth > (smooth.min() + 0.5 * (smooth.max() - smooth.min()))))
        return {"seg_loud": hi, "seg_quiet": lo, "seg_gap": gap,
                "seg_loud_share": round(share, 4)}
    except Exception as e:
        return {"seg_error": f"{type(e).__name__}: {str(e)[:50]}"}
