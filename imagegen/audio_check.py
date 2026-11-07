#!/usr/bin/env python3
"""audio_check.py - the "never harsh" gate for generated sounds, plus a soundboard to listen to.

  audio_check.py DIR [--frequent a,b,...] [--fix OUTDIR [--loop a,b,...] [--music a,b,...]]

For every .mp3/.wav/.flac/.ogg in DIR: length, loudness (LUFS; a clip under 0.4 s is measured tiled), true peak, spectral centroid and the share of
energy above 4 kHz. Gate: sounds that fire often need centroid <= 2.5 kHz and a >4 kHz share <= 15%; the rest need
centroid <= 4 kHz and <= 25%. --frequent names the sounds that fire often (the part of the file name before the
first "__"). Writes DIR/soundboard.mp3 (every sound in a row, 0.7 s apart) and DIR/check.json.

--fix also writes game-ready copies to OUTDIR (.mp3): silence trimmed, short fades, shelf cut above 6 kHz and a
3-4 kHz dip, loudness -18 LUFS with a -1 dBFS ceiling; the fixed copies are checked again.
A take that still fails gets the highest low-pass cutoff that passes; OUTDIR/fix.json lists each copy's
cutoff (lowpass_hz, null = none) so a picker can prefer the least-cut take.
--loop names sounds that repeat in the game (engine hum, ambience, a music bed): only the silence padded onto the
ends is cut (10 ms windows more than 40 dB under the peak; looped, it is a dropout every time round), no fades; the tail is
crossfaded into the head (equal power, 0.25 s, 1 s for music) and the copy starts on an upward zero crossing, so it
wraps with no dip or click. The EQ runs over three copies in a row and the middle one is kept, so the filters
have settled at both ends. --music names tracks that stay stereo, at -16 LUFS, as 192 kbps mp3.

Needs ffmpeg on the PATH and numpy (pip3 install numpy). Nothing else.
"""
import argparse, json, subprocess, sys
from pathlib import Path

try:
    import numpy as np
except ImportError:  # --help works without numpy; the real run checks it in main()
    np = None

SR = 44100
EXTS = (".mp3", ".wav", ".flac", ".ogg")


def decode(path, channels=1):
    """float32 samples at SR: shape (n,) mono, (n, 2) stereo."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", str(channels), "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    return x.reshape(-1, channels) if channels > 1 else x


def loop_crossfade(x, xf):
    """x made to repeat with no gap: its first xf samples blend (equal power) with the xf samples after the loop
    point, so the last kept sample runs on into the sound's own continuation. Returns len(x) - xf samples, or None
    when x is too short for that crossfade."""
    keep = len(x) - xf
    if xf < 1 or keep < 2 * xf:
        return None
    out = x[:keep].copy()
    t = np.linspace(0.0, np.pi / 2, xf, endpoint=False).reshape((xf,) + (1,) * (x.ndim - 1))
    out[:xf] = out[:xf] * np.sin(t) + x[keep:] * np.cos(t)
    return out


def sound_span(x, below=40, win=0.01):
    """(start, end) samples of x from its first to its last 10 ms window whose RMS is within `below` dB of the
    loudest sample: the silence a model pads onto the ends goes, quiet tails and breaths inside stay."""
    m = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    w = max(1, int(win * SR))
    n = len(m) // w
    if n == 0:
        return 0, len(m)
    rms = np.sqrt((m[:n * w].reshape(n, w) ** 2).mean(axis=1))
    on = np.nonzero(rms > m.max() * 10 ** (-below / 20))[0]
    if not on.size:
        return 0, len(m)
    return int(on[0]) * w, len(m) if on[-1] == n - 1 else (int(on[-1]) + 1) * w


def rotate_to_zero(y, keep):
    """One period of the 3x tiled loop y, from the middle copy, starting at its first upward zero crossing
    (an mp3 starts from silence, so a loop that starts mid-wave clicks on every restart)."""
    m = y.mean(axis=1) if y.ndim == 2 else y
    seg = m[keep:2 * keep]
    hits = np.nonzero((seg[:-1] < 0) & (seg[1:] >= 0))[0]
    a = keep + (int(hits[0]) + 1 if hits.size else 0)
    return y[a:a + keep]


def _loudnorm(inp, data=None):
    r = subprocess.run(["ffmpeg", "-hide_banner", *inp, "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       input=data, capture_output=True)
    err = r.stderr.decode(errors="replace")
    j = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
    return float(j["input_i"]), float(j["input_tp"])


def loudness(path):
    """(integrated LUFS, true peak dBTP). A clip shorter than one 400 ms gating block has no integrated loudness
    (ffmpeg says -inf), so it is measured tiled end to end to 3 s instead: the mean square stays the same, so the
    value is the clip's own loudness (padding with silence would read it too quiet). Digital silence stays -inf."""
    lufs, tp = _loudnorm(["-i", str(path)])
    if lufs != float("-inf"):
        return lufs, tp
    ch = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                             "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip() or 1)
    x = decode(path, ch)
    if len(x) == 0 or not np.abs(x).max():
        return lufs, tp
    reps = -(-3 * SR // len(x))
    y = np.tile(x, (reps, 1) if ch > 1 else reps)
    lufs, _ = _loudnorm(["-f", "f32le", "-ar", str(SR), "-ac", str(ch), "-i", "-"],
                        np.ascontiguousarray(y, dtype=np.float32).tobytes())
    return lufs, tp


def spectrum(x):
    """(spectral centroid in Hz, share of energy above 4 kHz) of the non-silent part of x."""
    x = x[np.abs(x) > 1e-4 * max(1e-9, np.abs(x).max())] if len(x) else x  # ignore digital silence
    if len(x) < 1024:
        return 0.0, 0.0
    win = 2048 if len(x) >= 2048 else 1024  # very short clips (a 0.03 s shell click) get the smaller window
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::win // 2] * np.hanning(win)
    p = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).sum(axis=0)
    f = np.fft.rfftfreq(win, 1 / SR)
    return float((f * p).sum() / p.sum()), float(p[f > 4000].sum() / p.sum())


def check(files, frequent):
    rows = []
    for p in files:
        x = decode(p)
        lufs, tp = loudness(p)
        cen, hf = spectrum(x)
        often = p.stem.split("__")[0] in frequent
        lim_c, lim_h = (2500, 0.15) if often else (4000, 0.25)
        ok = cen <= lim_c and hf <= lim_h
        rows.append({"file": p.name, "seconds": round(len(x) / SR, 2), "lufs": round(lufs, 1), "true_peak": round(tp, 1),
                     "centroid_hz": round(cen), "hf_share": round(hf, 3), "frequent": often, "pass": ok})
        print(f"{p.name:34} {len(x)/SR:5.2f} s  {lufs:6.1f} LUFS  peak {tp:5.1f}  centroid {cen:5.0f} Hz  >4k {hf*100:4.1f}%  "
              f"{'often ' if often else ''}{'PASS' if ok else 'HARSH'}")
    return rows


def soundboard(files, out):
    gap = ["-f", "lavfi", "-t", "0.7", "-i", "anullsrc=r=44100:cl=mono"]
    args, parts = [], []
    for i, p in enumerate(files):
        args += ["-i", str(p)] + gap
        parts += [f"[{2*i}:a]aresample=44100,aformat=channel_layouts=mono[a{i}]", f"[{2*i+1}:a]anull[g{i}]"]
    chain = "".join(f"[a{i}][g{i}]" for i in range(len(files)))
    flt = ";".join(parts) + f";{chain}concat=n={2*len(files)}:v=0:a=1[out]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", flt, "-map", "[out]", "-q:a", "2", str(out)], check=True)


def fix(files, outdir, frequent=frozenset(), loops=frozenset(), music=frozenset()):
    """Game-ready copy: trim silence (relative to the sound's own peak), 5 ms fade-in / 30 ms fade-out,
    shelf cut above 6 kHz and a dip at 3-4 kHz (the audio rules). If it still fails the never-harsh gate, a
    low-pass is lowered (bisected) to the highest cutoff that passes. Then gain to -18 LUFS (clips under 0.4 s
    measured tiled, see loudness) without letting the peak pass -1 dBFS.
    loops / music: names (before "__") of sounds that repeat, and of stereo tracks (-16 LUFS, 192k); see the top."""
    outdir.mkdir(parents=True, exist_ok=True)
    done, report = [], {}
    for p in files:
        name = p.stem.split("__")[0]
        is_loop, is_music = name in loops, name in music
        ch = 2 if is_music else 1
        x = decode(p, ch)
        env = np.abs(x).max(axis=1) if ch > 1 else np.abs(x)
        if env.size == 0 or env.max() == 0:
            print(f"{p.name}: silent, nothing to fix")
            continue
        keep = 0
        if is_loop:
            a, b = sound_span(x)  # Stable Audio pads a short ask with digital silence; looped, it is a dropout
            if b - a < len(x):
                print(f"{p.name}: {a / SR:.2f} s of silence off the start, {(len(x) - b) / SR:.2f} s off the end")
            x = loop_crossfade(x[a:b], int((1.0 if is_music else 0.25) * SR))
            if x is None:
                print(f"{p.name}: too short to loop, skipped")
                continue
            keep = len(x)
            x = np.concatenate([x, x, x])
        else:
            on = np.where(env > env.max() * 10 ** (-40 / 20))[0]
            x = x[max(0, on[0] - int(0.002 * SR)): on[-1] + int(0.03 * SR)].copy()
            n_in, n_out = int(0.005 * SR), min(int(0.03 * SR), len(x) // 4)
            fade = lambda r: r.reshape((-1,) + (1,) * (x.ndim - 1))
            x[:n_in] *= fade(np.linspace(0, 1, n_in))
            x[-n_out:] *= fade(np.linspace(1, 0, n_out))

        def eq(cut=None):
            af = "highshelf=f=6000:g=-4,equalizer=f=3500:width_type=o:width=1:g=-4" + (f",lowpass=f={cut:.0f}" if cut else "")
            out = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", str(ch), "-i", "-",
                                  "-af", af, "-f", "f32le", "-"],
                                 input=np.ascontiguousarray(x, dtype=np.float32).tobytes(), capture_output=True, check=True).stdout
            y = np.frombuffer(out, dtype=np.float32).copy()
            y = y.reshape(-1, ch) if ch > 1 else y
            return rotate_to_zero(y, keep) if keep else y

        often = name in frequent
        lim_c, lim_h = (2500, 0.15) if often else (4000, 0.25)

        def passes(arr, spare=False):
            # spare: room for the MP3 encode, which moves a sound that sits on the limit (it adds about 1 point
            # to the >4 kHz share): 3% under the centroid limit and 2 points under the share limit.
            c, h = spectrum(arr.mean(axis=1) if arr.ndim == 2 else arr)
            return c <= lim_c * (0.97 if spare else 1) and h <= lim_h - (0.02 if spare else 0)

        y = eq()
        cut = None
        if not passes(y, spare=True):
            lo, hi = 150.0, 16000.0  # bisect for the highest low-pass cutoff that passes the gate with room to spare
            for _ in range(12):
                mid = (lo * hi) ** 0.5
                if passes(eq(mid), spare=True):
                    lo = mid
                else:
                    hi = mid
            if passes(eq(lo)):
                cut, y = lo, eq(lo)
            print(f"{p.name}: low-pass at {lo:.0f} Hz to pass the gate" if cut else
                  f"{p.name}: still fails the gate at 150 Hz low-pass (not fixable this way; listen or regenerate)")
        report[p.stem + ".mp3"] = {"from": p.name, "lowpass_hz": round(cut) if cut else None,
                                    "passes": cut is not None or passes(y)}
        tmp = outdir / (p.stem + ".tmp.wav")
        enc = lambda arr, dst, codec: subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", str(ch),
                                                      "-i", "-", *codec, str(dst)],
                                                     input=np.ascontiguousarray(arr, dtype=np.float32).tobytes(), check=True)
        enc(y, tmp, [])
        now, _ = loudness(tmp)
        tmp.unlink()
        gain = 10 ** (((-16 if is_music else -18) - now) / 20)
        gain = min(gain, 10 ** (-1 / 20) / (np.abs(y).max() + 1e-9))
        dst = outdir / (p.stem + ".mp3")
        enc(y * gain, dst, ["-c:a", "libmp3lame"] + (["-b:a", "192k"] if is_music else ["-q:a", "2"]))
        if keep:
            print(f"{p.name}: loop of {keep / SR:.2f} s")
        done.append(dst)
    # fix.json: the low-pass each copy needed (null = none). A kit choosing among takes should prefer the highest
    # cutoff: a click cut to ~1.2 kHz passes the gate but sounds dull (FlatHead bolt-off 2026-11-06).
    (outdir / "fix.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return done


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dir", help="folder of sounds to check")
    p.add_argument("--frequent", default="", help="comma-separated sound names that fire often (stricter gate)")
    p.add_argument("--fix", metavar="OUTDIR", help="also write game-ready copies to OUTDIR and check them again")
    p.add_argument("--loop", default="", help="with --fix: comma-separated sound names that repeat (seamless copies)")
    p.add_argument("--music", default="", help="with --fix: comma-separated names of stereo music tracks (-16 LUFS)")
    a = p.parse_args()
    if np is None:
        sys.exit("audio_check needs numpy on this computer: pip3 install numpy")
    d = Path(a.dir)
    frequent = {x for x in a.frequent.split(",") if x}
    files = sorted(f for f in d.iterdir() if f.suffix in EXTS and f.stem != "soundboard")
    if not files:
        sys.exit(f"no sounds ({', '.join(EXTS)}) in {d}")
    rows = check(files, frequent)
    soundboard(files, d / "soundboard.mp3")
    out = {"raw": rows}
    if a.fix:
        fixed_dir = Path(a.fix)
        print("--- game-ready copies")
        fixed = fix(files, fixed_dir, frequent, {x for x in a.loop.split(",") if x}, {x for x in a.music.split(",") if x})
        out["fixed"] = check(fixed, frequent)
        out["skipped"] = sorted({f.stem for f in files} - {f.stem for f in fixed})  # silent, or too short to loop
        if fixed:
            soundboard(fixed, fixed_dir / "soundboard.mp3")
    json.dump(out, open(d / "check.json", "w"), indent=1)
    if a.fix and not out["fixed"]:
        sys.exit(f"no game-ready copies: every sound was skipped ({', '.join(out['skipped'])}; see above)")
    print(f"soundboard: {d / 'soundboard.mp3'}")


if __name__ == "__main__":
    main()
