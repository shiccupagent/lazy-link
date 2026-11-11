"""fit_music.py - make a generated music track exactly N seconds long and loop-ready, without silence.

  python fit_music.py IN OUT SECONDS [--json]     (OUT .mp3, V0; needs ffmpeg: $FFMPEG or on the PATH, and numpy)

Music models choose their own length (MiniMax Music 3 ends the song when it decides to). Games ask for a bed of an
exact length that repeats. This:
  1. keeps the "body": from the first to the last half second at a third of the track's typical loudness (no
     silence, soft intro or fade-out), so the loop never dips;
  2. finds where the track best repeats itself near SECONDS: the lag L within 5% of SECONDS whose next few seconds
     match the first few in rhythm (onset strength) and harmony (chroma). Matching both means the crossfade below
     blends the same beat and the same chord, so there is no flam and no clash;
  3. changes the tempo by L/SECONDS (pitch kept, ffmpeg atempo; at most 5%, usually under 2%) so that lag is exactly
     SECONDS, and folds: the audio after SECONDS is crossfaded (equal power, 1.5 s) into the start. The result is
     SECONDS long and its end runs straight into its start;
  4. a track shorter than SECONDS plus the crossfade is first folded into its own best loop (a lag of at least half
     its body), and that loop is repeated: whole phrases repeat, never silence.
Report (stdout, --json): seconds in/out, body, lag, tempo change, whether it repeated, and the seam score (0-1, how
well the blended parts matched; under 0.5 means an audible seam is likely).
"""
import argparse, json, os, shutil, subprocess, sys
import numpy as np

SR = 48000
XF = 1.5            # crossfade seconds; with a known tempo, one bar (1.5-3 s): a longer blend hides the join in soft music
HOP = 512


def ffmpeg():
    return os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def load(path):
    raw = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def tempo_change(x, factor):
    """Play x `factor` times as fast (pitch kept)."""
    if abs(factor - 1) < 1e-4:
        return x
    r = subprocess.run([ffmpeg(), "-v", "error", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-i", "-",
                        "-af", f"atempo={factor:.6f}", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                       input=x.astype(np.float32).tobytes(), capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).copy()


def save(x, path):
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ac", "2", "-ar", str(SR), "-i", "-",
                    "-c:a", "libmp3lame", "-q:a", "0", str(path)], input=x.astype(np.float32).tobytes(), check=True)


def features(x):
    """Per hop: onset strength (spectral flux) and a 12-bin chroma."""
    mono = x.mean(axis=1)
    n = 2048
    if len(mono) < n * 2:
        return np.zeros(1), np.zeros((1, 12))
    frames = np.lib.stride_tricks.sliding_window_view(mono, n)[::HOP] * np.hanning(n)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    logm = np.log1p(mag * 100)
    flux = np.maximum(np.diff(logm, axis=0, prepend=logm[:1]), 0).sum(axis=1)
    k = max(1, int(SR / HOP))  # remove the slow trend (about a second) so loud passages don't dominate
    onset = flux - np.convolve(flux, np.ones(k) / k, mode="same")
    freqs = np.fft.rfftfreq(n, 1 / SR)
    ok = (freqs > 55) & (freqs < 5000)
    pc = np.round(12 * np.log2(freqs[ok] / 440.0)).astype(int) % 12
    chroma = np.zeros((mag.shape[0], 12))
    for c in range(12):
        chroma[:, c] = (mag[:, ok][:, pc == c] ** 2).sum(axis=1)
    chroma /= np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-9
    return onset, chroma


def match(onset, chroma, a, b, w):
    """How alike the w hops from a and from b are: rhythm correlation and mean chroma cosine, 0-1."""
    if b + w > len(onset) or a + w > len(onset):
        return -1.0
    o1, o2 = onset[a:a + w], onset[b:b + w]
    d = (np.linalg.norm(o1 - o1.mean()) * np.linalg.norm(o2 - o2.mean())) + 1e-9
    rhythm = float(((o1 - o1.mean()) * (o2 - o2.mean())).sum() / d)
    harmony = float((chroma[a:a + w] * chroma[b:b + w]).sum(axis=1).mean())
    return 0.5 * max(rhythm, 0) + 0.5 * harmony


def beat_period(onset, bpm=None):
    """The beat length in hops (fractional), or None when the rhythm is too weak to say: the strongest onset
    autocorrelation within 6% of the caption's BPM (60-180 BPM without one), refined at 8 beats (8x finer than one hop)."""
    hps = SR / HOP
    o = onset - onset.mean()
    if len(o) < hps * 8 or not o.any():
        return None
    ac = np.correlate(o, o, mode="full")[len(o) - 1:] / (o @ o)
    lo, hi = (int(hps * 60 / (bpm * 1.06)), int(hps * 60 / (bpm * 0.94)) + 1) if bpm else (int(hps * 60 / 180), int(hps * 60 / 60))
    p0 = lo + int(np.argmax(ac[lo:hi]))
    if ac[p0] < 0.15:
        return None
    k = 8 if p0 * 8 + 2 < len(ac) // 2 else 4
    c = int(round(p0 * k))
    j = c - 3 + int(np.argmax(ac[c - 3:c + 4]))
    a, b, d = ac[j - 1], ac[j], ac[j + 1]
    off = 0.5 * (a - d) / (a - 2 * b + d) if (a - 2 * b + d) != 0 else 0.0
    return (j + off) / k


def best_lag(x, lo_s, hi_s, target_s=None, bpm=None, meter=4):
    """The lag (samples) in [lo_s, hi_s] seconds where the track best repeats its start; and its score."""
    onset, chroma = features(x)
    hps = SR / HOP
    w = int(min(6.0, max(2.0, (len(x) / SR - hi_s) * 0.9)) * hps)
    lo, hi = int(lo_s * hps), int(hi_s * hps)
    beat = beat_period(onset, bpm) if bpm else None
    if beat:   # only whole beats, so the wrap stays on the beat grid; whole bars score a little higher
        lags = [(k * beat, 0.05 if k % meter == 0 else 0.0) for k in range(int(np.ceil(lo / beat)), int(hi / beat) + 1)]
    else:
        lags = [(float(l), 0.0) for l in range(lo, hi + 1)]
    best, score = None, -2.0
    for lag, bonus in lags:
        s = match(onset, chroma, 0, int(round(lag)), w) + bonus
        if target_s:   # a small pull toward no tempo change
            s -= 2.0 * abs(lag / hps - target_s) / target_s
        if s > score:
            best, score = lag, s
    if best is None:
        return None, 0.0
    return int(round(best * HOP)), match(onset, chroma, 0, int(round(best)), w)


def fold(x, n, xf=XF):
    """x folded at n samples: x[n:n+xf] crossfaded (equal power) into x[0:xf]; the result is n samples and loops."""
    k = int(xf * SR)
    out = x[:n].copy()
    t = np.linspace(0, np.pi / 2, k, dtype=np.float32)[:, None]
    out[:k] = x[:k] * np.sin(t) + x[n:n + k] * np.cos(t)
    return out


def body(x):
    """From the first to the last half second at a third of the track's typical loudness: no silence, soft intro or fade-out."""
    win = SR // 2
    m = len(x) // win
    if m < 4:
        return x
    rms = np.sqrt((x[:m * win] ** 2).mean(axis=(1,)).reshape(m, win).mean(axis=1))
    typical = np.median(rms[rms > rms.max() * 0.01]) if (rms > rms.max() * 0.01).any() else rms.max()
    loud = np.nonzero(rms >= typical * 0.33)[0]
    if not len(loud):
        return x
    # the loop starts where the music is at its usual level (a soft intro at the head would make every wrap dip)
    return x[loud[0] * win:(loud[-1] + 1) * win]


def fit(x, seconds, bpm=None, meter=4):
    target = int(round(seconds * SR))
    xf = min(3.0, max(XF, 60.0 / bpm * meter)) if bpm else XF
    k = int(xf * SR)
    b = body(x)
    rep = {"in_s": round(len(x) / SR, 2), "body_s": round(len(b) / SR, 2), "repeated": False}
    if len(b) < target + k + SR:   # too short: fold the body into its own best loop, then repeat that loop
        lag, sc = best_lag(b, max(len(b) / SR * 0.5, XF * 2), (len(b) - k) / SR - 0.5, bpm=bpm, meter=meter)
        if lag is None:
            raise SystemExit(f"fit_music: {len(b) / SR:.1f} s of music is too short to loop")
        loop = fold(b, lag, xf)
        reps = int(np.ceil((target * 1.06 + k + SR) / len(loop))) + 1
        b = np.concatenate([loop] * reps)
        rep.update(repeated=True, inner_loop_s=round(lag / SR, 2), inner_seam=round(sc, 2))
    lag, sc = best_lag(b, seconds * 0.95, seconds * 1.05, seconds, bpm=bpm, meter=meter)
    factor = lag / target                    # play this much faster so the lag becomes exactly `seconds`
    y = tempo_change(b, factor)
    if len(y) < target + k:                  # atempo rounding: pad by repeating (never silence) is not needed here
        y = np.concatenate([y, y[:target + k - len(y)]])
    out = fold(y, target, xf)
    rep.update(crossfade_s=round(xf, 2), lag_s=round(lag / SR, 3), tempo_change_pct=round((factor - 1) * 100, 2), seam=round(sc, 2),
               out_s=round(len(out) / SR, 3))
    return out, rep


def beat_strength(onset, a, w):
    """How periodic the onsets in [a, a+w) are: the best normalized autocorrelation at a beat lag (40-200 BPM)."""
    o = onset[a:a + w] - onset[a:a + w].mean()
    if len(o) < w or not o.any():
        return 0.0
    hps = SR / HOP
    ac = np.correlate(o, o, mode="full")[len(o) - 1:] / (o @ o)
    lo, hi = int(hps * 60 / 200), int(hps * 60 / 40)
    return float(ac[lo:hi].max())


def seam_check(out, bpm=None):
    """The loop played twice: at the wrap vs the middle. click = the jump at the wrap over the track's own 99.9th
    percentile sample step (under 1 = no bigger than steps the music already makes);
    dip_db = the quietest 0.25 s near the wrap vs the track's own quietest 5% (0 or more: no dip the music doesn't
    already have); beat = beat steadiness across the wrap over its median at six other spots (1 = as steady);
    grid_off = how far the wrap lands off the beat grid, in beats (0 = on the beat; over 0.15 is a stumble);
    beats = the loop's length in beats (None when the rhythm is too soft to measure)."""
    two = np.concatenate([out, out])
    n = len(out)
    step = np.abs(np.diff(out.mean(axis=1)))
    click = float(abs(out[0].mean() - out[-1].mean()) / (np.percentile(step, 99.9) + 1e-9))
    q = SR // 4
    near = [np.sqrt((two[i:i + q] ** 2).mean()) for i in range(n - 2 * SR, n + 2 * SR - q, q // 2)]
    m = n // q
    typical = np.median(np.sqrt((out[:m * q] ** 2).reshape(m, q, 2).mean(axis=(1, 2))))
    lv = np.sqrt((out[:m * q] ** 2).reshape(m, q, 2).mean(axis=(1, 2)))
    quiet = np.percentile(lv, 5)   # how quiet the music itself gets: a wrap may go that low, not lower
    dip = float(20 * np.log10((min(near) + 1e-9) / (quiet + 1e-9)))
    onset, _ = features(two)
    hps = SR / HOP
    w = int(min(8.0, n / SR * 0.4) * hps)
    wrap = int(n / HOP) - w // 2
    spots = [beat_strength(onset, int(n / HOP * f) - w // 2, w) for f in (0.3, 0.45, 0.6, 0.75, 1.3, 1.5)]
    beat = beat_strength(onset, wrap, w) / (float(np.median(spots)) + 1e-9)
    p = beat_period(features(out)[0], bpm) if bpm else None
    off = None if p is None else abs(n / HOP / p - round(n / HOP / p))
    return {"click": round(click, 1), "dip_db": round(dip, 1), "beat": round(beat, 2),
            "grid_off": None if off is None else round(off, 2), "beats": None if p is None else round(n / HOP / p, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out"); ap.add_argument("seconds", type=float)
    ap.add_argument("--bpm", type=float, help="the tempo the caption asked for (folds land on its beats)")
    ap.add_argument("--meter", type=int, default=4, help="beats per bar (3 for a waltz)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out, rep = fit(load(a.inp), a.seconds, a.bpm, a.meter)
    rep["check"] = seam_check(out, a.bpm)
    save(out, a.out)
    print(json.dumps(rep) if a.json else rep)


if __name__ == "__main__":
    main()
