"""Swing: where the hi-hat lands between the beats.

Two earlier attempts failed on real records, each in an instructive way. Taking the median of
every onset in the middle of the beat returned the centre of its own search window, because a
real record carries a dozen onsets a beat and the median of a crowd is a property of the
crowd. Picking the loudest hat per beat scattered, because on most beats something else is
louder.

What works is to fold every beat onto one and look at where the energy in the hi-hat band
actually sits. One noisy beat cannot move it, and a position the record keeps returning to
shows up as a peak.

Two details matter. The band: swing is carried above about four kilohertz, and below that the
kick and the bass dominate any onset detector. And the envelope: plain spectral flux on that
band, not a decibel-scaled onset strength, which compresses the hat until it reads six
hundredths of a beat late.
"""
import numpy as np


def swing(y, sr, bpm=None):
    import librosa
    try:
        S = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
        band = S[freqs >= 4000]
        if band.size == 0:
            return {"swing_error": "no high band"}
        flux = np.maximum(0.0, np.diff(np.r_[0.0, band.sum(0)]))
        if flux.max() <= 0:
            return {"swing_error": "no energy in the hat band"}
        times = librosa.frames_to_time(np.arange(len(flux)), sr=sr, hop_length=256)

        full = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
        if bpm is None or not (60 < float(bpm) < 220):
            bpm = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=full, sr=sr))[0])
        if not (60 < bpm < 220):
            return {"swing_error": "no usable tempo"}
        _, beats = librosa.beat.beat_track(onset_envelope=full, sr=sr, bpm=bpm, units="time")
        beats = np.asarray(beats, float)
        if len(beats) < 8:
            return {"swing_error": "too few beats"}
        gaps = np.diff(beats)
        ok = (gaps > 0.15) & (gaps < 1.5)
        if ok.sum() < 6:
            return {"swing_error": "beat grid unstable"}

        # fold: every beat laid on top of every other, in sixty slices
        B = 60
        prof = np.zeros(B)
        hits = np.zeros(B)
        used = 0
        for b0, g, good in zip(beats[:-1], gaps, ok):
            if not good:
                continue
            used += 1
            sel = (times >= b0) & (times < b0 + g)
            if sel.sum() < B // 3:
                continue
            ph = ((times[sel] - b0) / g * B).astype(int).clip(0, B - 1)
            for k, v in zip(ph, flux[sel]):
                prof[k] += v
                hits[k] += 1
        if used < 6 or hits.sum() == 0:
            return {"swing_error": "too few usable beats"}
        prof = prof / np.maximum(hits, 1)

        # Rotate so the loudest slice sits at zero: beat_track finds the rate reliably and
        # the phase only sometimes. But the loudest slice is not always the kick, and on a
        # record with a heavy hat it is the hat, which puts the kick at one minus the swing
        # and reads every shuffle as its own mirror. Swing is late by definition, so a
        # reading before the halfway point means we anchored on the offbeat: take the
        # complement, which is where the beat actually was.
        prof = np.roll(prof, -int(np.argmax(prof)))
        lo, hi = int(B * 0.22), int(B * 0.80)
        seg = prof[lo:hi]
        if seg.size < 4:
            return {"swing_error": "window too small"}
        j = int(np.argmax(seg))
        sw = (lo + j + 0.5) / B
        if sw < 0.5:
            sw = 1.0 - sw
        base = float(np.median(prof))
        lift = float((seg[j] - base) / (base + 1e-9))
        # a straight record has no offbeat peak worth the name; calling its noise a shuffle
        # is the failure this measure exists to avoid
        if lift < 0.15:
            return {"swing": 0.5, "swing_grip": 0.0, "swing_lift": round(lift, 4),
                    "swing_n": int(used)}
        return {"swing": round(float(sw), 4), "swing_grip": round(min(lift / 1.5, 1.0), 3),
                "swing_lift": round(lift, 4), "swing_n": int(used)}
    except Exception as e:
        return {"swing_error": f"{type(e).__name__}: {str(e)[:50]}"}
