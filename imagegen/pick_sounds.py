#!/usr/bin/env python3
"""pick_sounds.py TAKES_DIR --ref FILE [FILE ...] --dest DEST [--keep 3] [--often a,b] [--max-sec S]
                  [--fade-sec F] [--hp HZ] [--lp HZ] [--peak-db -2] - finish every generated take the way a
sound should be cut, score it against your reference sounds, and keep the best few per sound.

TAKES_DIR holds generated takes named <key>__<anything>.<ext>. imagegen.py make audio writes <name>__audio.mp3,
so several seeds of one sound are e.g. click__s1__audio.mp3, click__s2__audio.mp3. The key is the part before
the first "__". Takes of one key are scored together; keys are picked separately.

--ref FILE ...  the sound you want to match: a few clips you like the sound of, or a game's own clips. wav, mp3,
                ogg, m4a, flac: anything ffmpeg reads. Every take of every key is scored against all of them.

Finish, per take: start 3 ms before the onset; cap at --max-sec (default: 1.5 x the references' average length);
--fade-sec linear fade-out (default 0.06 s); 4 ms fade-in; optional --hp / --lp Hz filters; then loudness: RMS
set to the references' average RMS, peak at most --peak-db (default -2 dBFS).
Tone match: a take noticeably brighter than the references (spectral centroid more than 25% higher) gets a
low-pass that brings its centroid to about 1.15x theirs (still a little fresher, but in the same family). When the
references pass the never-harsh gate (audio_check.py), the take must pass it too.

Score (higher is better), each part 0..1 on the finished clip:
  0.55 tone   - how close its spectrum (16 bands) is to the references: the same kind of sound
  0.45 shape  - how close its loudness envelope is (a sharp crack vs a swell)
  minus 0.35 if it is harsh: it fails the never-harsh gate where the references pass it, or is clearly brighter.
        "harsh" is counted after the tone match, so a take the low-pass can fix (and does) is not counted as harsh;
        its low-pass Hz column says so.
--often a,b     sounds that fire often: the stricter gate (centroid <= 2.5 kHz, >4 kHz share <= 15%)

Writes DEST/<key>__v<1..keep>.wav, DEST/picks.json (every take's scores) and DEST/picks.md (a readable table).
The picks are a shortlist for the ear, not the last word: sounds that matter get listened to.

Needs ffmpeg on the PATH, numpy and scipy (pip3 install numpy scipy), and audio_check.py beside this file.
"""
import argparse, json, subprocess, sys
from pathlib import Path

try:
    import numpy as np
    from scipy.signal import butter, sosfilt
except ImportError:  # --help works without them; the real run checks in main()
    np = None
import audio_check as ac

SR = ac.SR
EXTS = ac.EXTS


def finish(x, max_sec, fade_sec, hp=None, lp=None):
    """Onset -3 ms, length cap, linear fade-out, 4 ms fade-in, optional high-pass / low-pass."""
    x = x.astype(np.float64)
    th = np.abs(x).max() * 0.03
    o = max(0, int(np.argmax(np.abs(x) > th)) - int(0.003 * SR))
    x = x[o:o + int(max_sec * SR)].copy()
    nf = min(int(fade_sec * SR), int(len(x) * 0.3))
    if nf:
        x[-nf:] *= 1 - np.arange(1, nf + 1) / nf
    ni = min(int(0.004 * SR), len(x))
    x[:ni] *= np.arange(1, ni + 1) / ni
    if hp:
        x = sosfilt(butter(2, hp, "highpass", fs=SR, output="sos"), x)
    if lp:
        x = sosfilt(butter(2, lp, "lowpass", fs=SR, output="sos"), x)
    return x


def tone_match(x, ref_centroid, gate=None):
    """Low-pass a take that is >25% brighter than the references so its centroid lands near 1.15x theirs.
    gate=(centroid, hf share): the references pass the never-harsh gate, so the take must pass it too."""
    def ok(y):
        c, h = ac.spectrum(y.astype(np.float32))
        return c <= 1.15 * ref_centroid and (gate is None or (c <= gate[0] and h <= gate[1]))
    c0, h0 = ac.spectrum(x.astype(np.float32))
    if c0 <= 1.25 * ref_centroid and (gate is None or (c0 <= gate[0] and h0 <= gate[1])):
        return x, None
    lo, hi = 150.0, 16000.0
    for _ in range(14):  # bisect for the highest cutoff that is ok (a 2nd-order low-pass)
        mid = (lo * hi) ** 0.5
        lo, hi = (mid, hi) if ok(sosfilt(butter(2, mid, "lowpass", fs=SR, output="sos"), x)) else (lo, mid)
    return sosfilt(butter(2, lo, "lowpass", fs=SR, output="sos"), x), round(lo)


def level(x, target_db, peak_db):
    """RMS over the whole clip to target_db, peak at most peak_db."""
    r = np.sqrt(np.mean(x ** 2)) + 1e-12
    g = min(10 ** (target_db / 20) / r, 10 ** (peak_db / 20) / (np.abs(x).max() + 1e-12))
    return x * g


def bands(x):
    if len(x) < 2048:
        x = np.pad(x, (0, 2048 - len(x)))
    p = (np.abs(np.fft.rfft(np.lib.stride_tricks.sliding_window_view(x, 2048)[::512] * np.hanning(2048), axis=1)) ** 2).sum(0)
    f = np.fft.rfftfreq(2048, 1 / SR); edges = np.geomspace(60, 16000, 17)
    v = np.array([p[(f >= lo) & (f < hi)].sum() for lo, hi in zip(edges[:-1], edges[1:])]) + 1e-12
    v = np.log10(v / v.sum())
    return v - v.mean()


def shape(x, n=64):
    n_fr = max(1, int(SR * 0.002))
    m = len(x) // n_fr
    env = np.sqrt((x[:m * n_fr].reshape(m, n_fr) ** 2).mean(axis=1) + 1e-12)
    e = np.interp(np.linspace(0, len(env) - 1, n), np.arange(len(env)), env)
    return e / (e.max() + 1e-12)


def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("takes", help="folder of generated takes")
    p.add_argument("--ref", nargs="+", required=True, metavar="FILE", help="reference sound files to match")
    p.add_argument("--dest", required=True, help="folder for the kept picks, picks.json and picks.md")
    p.add_argument("--keep", type=int, default=3, help="how many takes to keep per sound (default 3)")
    p.add_argument("--often", default="", help="comma-separated sound names that fire often (stricter gate)")
    p.add_argument("--max-sec", type=float, help="length cap in seconds (default 1.5 x the references' average length)")
    p.add_argument("--fade-sec", type=float, default=0.06, help="fade-out in seconds (default 0.06)")
    p.add_argument("--hp", type=float, help="optional high-pass, Hz")
    p.add_argument("--lp", type=float, help="optional low-pass, Hz (applied before the tone match)")
    p.add_argument("--peak-db", type=float, default=-2.0, help="peak ceiling in dBFS (default -2)")
    a = p.parse_args()
    if np is None:
        sys.exit("pick_sounds needs numpy and scipy on this computer: pip3 install numpy scipy")
    often = {x for x in a.often.split(",") if x}

    missing = [f for f in a.ref if not Path(f).is_file()]
    if missing:
        sys.exit(f"reference file not found: {', '.join(missing)}")
    refs = [ac.decode(Path(f)).astype(np.float64) for f in a.ref]
    ref_sec = float(np.mean([len(r) / SR for r in refs]))
    max_sec = a.max_sec or 1.5 * ref_sec
    ref_db = float(np.mean([20 * np.log10(np.sqrt(np.mean(r ** 2)) + 1e-12) for r in refs]))
    spec = [ac.spectrum(r.astype(np.float32)) for r in refs]
    ref_c = float(np.mean([c for c, _ in spec]))
    ref_h = float(np.mean([h for _, h in spec]))
    ref_b = np.mean([bands(r) for r in refs], axis=0)
    ref_s = np.mean([shape(r) for r in refs], axis=0)
    print(f"references: {len(refs)} file(s), {ref_sec:.2f} s average, centroid {ref_c:.0f} Hz, "
          f"{ref_db:.1f} dBFS RMS, cap {max_sec:.2f} s")

    takes_dir = Path(a.takes)
    files = sorted(f for f in takes_dir.iterdir() if f.suffix in EXTS and f.stem != "soundboard")
    groups = {}
    for f in files:
        groups.setdefault(f.stem.split("__")[0], []).append(f)
    if not groups:
        sys.exit(f"no takes ({', '.join(EXTS)}) in {takes_dir}")

    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    report = {}
    md = ["| Sound | Kept takes (score) | Best: tone / shape / low-pass Hz | Harsh takes |", "|---|---|---|---|"]
    for key, takes in sorted(groups.items()):
        lim_c, lim_h = (2500, 0.15) if key in often else (4000, 0.25)
        ref_ok = ref_c <= lim_c and ref_h <= lim_h
        rows = []
        for f in takes:
            raw = ac.decode(f).astype(np.float64)
            y, cut = tone_match(finish(raw, max_sec, a.fade_sec, a.hp, a.lp), ref_c, (lim_c, lim_h) if ref_ok else None)
            y = level(y, ref_db, a.peak_db)
            tone = (cos(bands(y), ref_b) + 1) / 2
            shp = max(0.0, cos(shape(y), ref_s))
            cen, hf = ac.spectrum(y.astype(np.float32))
            # Harsh = fails the gate where the references pass it, or (for sounds that are bright by nature, such as
            # metal clicks whose references fail it too) is clearly brighter than the references.
            harsh = (not (cen <= lim_c and hf <= lim_h)) and (ref_ok or cen > 1.2 * ref_c or hf > ref_h + 0.06)
            score = 0.55 * tone + 0.45 * shp - (0.35 if harsh else 0)
            rows.append({"take": f.name, "score": round(score, 3), "tone": round(tone, 3), "shape": round(shp, 3),
                         "lowpass_hz": cut, "centroid_hz": round(cen), "hf_share": round(hf, 3),
                         "harsh": harsh, "seconds": round(len(y) / SR, 3), "_y": y})
        rows.sort(key=lambda r: -r["score"])
        for i, r in enumerate(rows[:a.keep]):
            r["kept"] = f"{key}__v{i + 1}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f64le", "-ar", str(SR), "-ac", "1", "-i", "-",
                            str(dest / r["kept"])], input=r["_y"].tobytes(), check=True)
        for r in rows:
            r.pop("_y")
        report[key] = {"ref_sec": round(ref_sec, 3), "ref_centroid_hz": round(ref_c), "takes": rows}
        best = rows[0]
        kept = ", ".join(f"{r['take']} ({r['score']:.2f})" for r in rows[:a.keep])
        harsh_n = sum(r["harsh"] for r in rows)
        md.append(f"| {key} | {kept} | {best['tone']:.2f} / {best['shape']:.2f} / {best['lowpass_hz'] or '-'} "
                  f"| {harsh_n}/{len(rows)} |")
        print(md[-1])
    json.dump(report, open(dest / "picks.json", "w"), indent=1)
    (dest / "picks.md").write_text("\n".join(md) + "\n")
    print(f"picks: {dest / 'picks.md'}")


if __name__ == "__main__":
    main()
