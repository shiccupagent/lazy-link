#!/usr/bin/env python3
"""audio_check.py - the "never harsh" gate for generated sounds, plus a soundboard to listen to.

  audio_check.py DIR [--frequent a,b,...] [--fix OUTDIR]

For every .mp3/.wav/.flac/.ogg in DIR: length, loudness (LUFS), true peak, spectral centroid and the share of
energy above 4 kHz. Gate: sounds that fire often need centroid <= 2.5 kHz and a >4 kHz share <= 15%; the rest need
centroid <= 4 kHz and <= 25%. --frequent names the sounds that fire often (the part of the file name before the
first "__"). Writes DIR/soundboard.mp3 (every sound in a row, 0.7 s apart) and DIR/check.json.

--fix also writes game-ready copies to OUTDIR (.mp3): silence trimmed, short fades, shelf cut above 6 kHz and a
3-4 kHz dip, loudness -18 LUFS with a -1 dBFS ceiling; the fixed copies are checked again.

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


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def loudness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    j = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    return float(j["input_i"]), float(j["input_tp"])


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


def fix(files, outdir, frequent=frozenset()):
    """Game-ready copy: trim silence (relative to the sound's own peak), 5 ms fade-in / 30 ms fade-out,
    shelf cut above 6 kHz and a dip at 3-4 kHz (the audio rules). If it still fails the never-harsh gate, a
    low-pass is lowered (bisected) to the highest cutoff that passes. Then gain to -18 LUFS (RMS for clips
    under 0.5 s, where LUFS is undefined) without letting the peak pass -1 dBFS."""
    outdir.mkdir(parents=True, exist_ok=True)
    done = []
    for p in files:
        x = decode(p)
        env = np.abs(x)
        if env.size == 0 or env.max() == 0:
            print(f"{p.name}: silent, nothing to fix")
            continue
        on = np.where(env > env.max() * 10 ** (-40 / 20))[0]
        x = x[max(0, on[0] - int(0.002 * SR)): on[-1] + int(0.03 * SR)].copy()
        n_in, n_out = int(0.005 * SR), min(int(0.03 * SR), len(x) // 4)
        x[:n_in] *= np.linspace(0, 1, n_in)
        x[-n_out:] *= np.linspace(1, 0, n_out)

        def eq(cut=None):
            af = "highshelf=f=6000:g=-4,equalizer=f=3500:width_type=o:width=1:g=-4" + (f",lowpass=f={cut:.0f}" if cut else "")
            out = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                                  "-af", af, "-f", "f32le", "-"],
                                 input=x.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
            return np.frombuffer(out, dtype=np.float32).copy()

        often = p.stem.split("__")[0] in frequent
        lim_c, lim_h = (2500, 0.15) if often else (4000, 0.25)

        def passes(arr, margin=1.0):
            c, h = spectrum(arr)
            return c <= lim_c * margin and h <= lim_h * margin

        y = eq()
        cut = None
        if not passes(y):
            lo, hi = 150.0, 16000.0  # bisect for the highest low-pass cutoff that passes the gate with 3% to spare
            for _ in range(12):      # (MP3 encoding can move a sound that sits exactly on the limit)
                mid = (lo * hi) ** 0.5
                if passes(eq(mid), 0.97):
                    lo = mid
                else:
                    hi = mid
            if passes(eq(lo)):
                cut, y = lo, eq(lo)
            print(f"{p.name}: low-pass at {lo:.0f} Hz to pass the gate" if cut else
                  f"{p.name}: still fails the gate at 150 Hz low-pass (not fixable this way; listen or regenerate)")
        tmp = outdir / (p.stem + ".tmp.wav")
        enc = lambda arr, dst, codec: subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                                                      *codec, str(dst)], input=arr.astype(np.float32).tobytes(), check=True)
        if len(y) >= SR // 2:
            enc(y, tmp, [])
            now, _ = loudness(tmp)
            tmp.unlink()
        else:
            now = 20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9)
        gain = 10 ** ((-18 - now) / 20)
        gain = min(gain, 10 ** (-1 / 20) / (np.abs(y).max() + 1e-9))
        dst = outdir / (p.stem + ".mp3")
        enc(y * gain, dst, ["-c:a", "libmp3lame", "-q:a", "2"])
        done.append(dst)
    return done


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dir", help="folder of sounds to check")
    p.add_argument("--frequent", default="", help="comma-separated sound names that fire often (stricter gate)")
    p.add_argument("--fix", metavar="OUTDIR", help="also write game-ready copies to OUTDIR and check them again")
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
        fixed = fix(files, fixed_dir, frequent)
        out["fixed"] = check(fixed, frequent)
        soundboard(fixed, fixed_dir / "soundboard.mp3")
    json.dump(out, open(d / "check.json", "w"), indent=1)
    print(f"soundboard: {d / 'soundboard.mp3'}")


if __name__ == "__main__":
    main()
