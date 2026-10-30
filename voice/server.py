#!/usr/bin/env python3
"""server.py - the walk-up voice recorder. You double-click "Record voices" on the PC's desktop: this starts
a tiny web server on 127.0.0.1 (only this PC can reach it) and opens the browser on page/index.html, which shows one
line of scripts.json at a time, records it from the mic and sends it here. Takes land in recordings/<voice>/<NN>.wav
(a redo keeps the one before as <NN>.prev.wav), where your small computer picks them up (voice/clone.py).
Recordings never leave your home network: only the PC and your small computer ever hold them.
Standard library only, so any Python runs it.

  server.py [--port 8765] [--root DIR] [--no-browser]
"""
import argparse, json, math, os, threading, time, webbrowser, wave
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PAGE = HERE / "page"
IDLE_EXIT = 45 * 60   # quit after this long with no page open, so nothing lingers
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".png": "image/png"}
last_seen = time.time()


def script():
    return json.loads((HERE / "scripts.json").read_text(encoding="utf-8"))


def take_path(rec, voice, n):
    return rec / voice / f"{n:02d}.wav"


def stats(path):
    """Length in seconds, peak and loudness (RMS) in dBFS of a 16-bit mono WAV."""
    with wave.open(str(path)) as w:
        rate, n = w.getframerate(), w.getnframes()
        a = array("h", w.readframes(n))
    if not a:
        return {"secs": 0, "peak_db": -120, "rms_db": -120}
    peak = max(abs(min(a)), max(a)) / 32768
    rms = math.sqrt(sum(x * x for x in a) / len(a)) / 32768
    db = lambda v: round(20 * math.log10(max(v, 1e-6)), 1)
    return {"secs": round(n / rate, 2), "peak_db": db(peak), "rms_db": db(rms)}


def state(rec):
    s = script()
    for v in s["voices"]:
        for i, line in enumerate(v["lines"], 1):
            line["n"] = i
            p = take_path(rec, v["id"], i)
            line["done"] = p.is_file()
            if line["done"]:
                line.update(stats(p))
    return s


class Handler(BaseHTTPRequestHandler):
    rec = None

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def which(self, q):
        voices = {v["id"]: len(v["lines"]) for v in script()["voices"]}
        voice, n = q.get("voice", [""])[0], q.get("n", [""])[0]
        if voice not in voices or not n.isdigit() or not 1 <= int(n) <= voices[voice]:
            return None
        return take_path(self.rec, voice, int(n))

    def do_GET(self):
        global last_seen
        last_seen = time.time()
        u = urlparse(self.path)
        if u.path == "/api/ping":   # the open page says it is still there
            return self.send(200, b"{}")
        if u.path == "/api/state":
            return self.send(200, json.dumps(state(self.rec)).encode())
        if u.path == "/api/take":
            p = self.which(parse_qs(u.query))
            return self.send(200, p.read_bytes(), "audio/wav") if p and p.is_file() else self.send(404)
        name = "index.html" if u.path == "/" else u.path.lstrip("/")
        f = (PAGE / name).resolve()
        if PAGE in f.parents and f.is_file() and f.suffix in TYPES:
            return self.send(200, f.read_bytes(), TYPES[f.suffix])
        self.send(404)

    def do_POST(self):
        global last_seen
        last_seen = time.time()
        u = urlparse(self.path)
        p = self.which(parse_qs(u.query)) if u.path == "/api/take" else None
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not p or body[:4] != b"RIFF" or body[8:12] != b"WAVE":
            return self.send(400, b'{"error": "not a take"}')
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_bytes(body)
        try:
            st = stats(tmp)
        except (wave.Error, EOFError):
            tmp.unlink()
            return self.send(400, b'{"error": "unreadable audio"}')
        if p.is_file():
            os.replace(p, p.with_suffix(".prev.wav"))
        os.replace(tmp, p)
        self.send(200, json.dumps(st).encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root", default=str(HERE / "recordings"))
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}/"
    Handler.rec = Path(a.root)
    Handler.rec.mkdir(parents=True, exist_ok=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError:   # already running from an earlier double-click: just show it again
        if not a.no_browser:
            webbrowser.open(url)
        return
    def idle():
        while time.time() - last_seen < IDLE_EXIT:
            time.sleep(30)
        os._exit(0)
    threading.Thread(target=idle, daemon=True).start()
    if not a.no_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    print(f"recorder on {url}, takes in {Handler.rec}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
