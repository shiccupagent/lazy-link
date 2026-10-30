// render.mjs - draws a web page frame by frame in headless Chrome and saves each frame as a JPEG.
// webvideo.py runs it on the big PC; it also runs on any computer with Node 22+ and Chrome:
//   node render.mjs PAGE_DIR OUT_DIR SECONDS FPS WIDTH HEIGHT [PAGE.html] [QUERY]
//   -> OUT_DIR/frames/f00000.jpg, f00001.jpg, ...  (round(SECONDS * FPS) frames)
//
// The page must expose the contract (docs/WEB-TO-VIDEO.md):
//   window.sceneReady   a Promise that resolves once everything the page loads has loaded
//   window.renderAt(t)  draws the frame at t seconds; the page must not animate on its own
//
// Gotcha: three.js's LoadingManager does not expose its item counts, so the page must wait on
// each texture's own load callback before it sets sceneReady.
//
// CHROME=/path/to/chrome overrides the browser search.

import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { delimiter, extname, join, resolve, sep } from 'node:path';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function fail(msg) { console.error(`render.mjs: ${msg}`); process.exit(2); }

const [DIR, OUT, SECS, FPS, W, H, PAGE = 'index.html', QUERY = ''] = process.argv.slice(2);
if (!H) fail('usage: node render.mjs PAGE_DIR OUT_DIR SECONDS FPS WIDTH HEIGHT [PAGE.html] [QUERY]');
const num = (v, what) => { const n = Number(v); if (!Number.isFinite(n) || n <= 0) fail(`${what} must be a positive number, not "${v}"`); return n; };
const secs = num(SECS, 'SECONDS');
const fps = num(FPS, 'FPS');
const width = Math.round(num(W, 'WIDTH'));
const height = Math.round(num(H, 'HEIGHT'));
const frames = Math.floor(secs * fps + 0.5);
if (Number(process.versions.node.split('.')[0]) < 22) fail(`Node 22 or newer is needed (this is ${process.version})`);

const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
  '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.woff2': 'font/woff2',
};

// Chrome: CHROME if set, else the usual install place on each system (Linux: a name on PATH).
function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const absolute = {
    win32: ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'],
    darwin: ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium'],
  }[process.platform] || [];
  const hit = absolute.find((p) => existsSync(p));
  if (hit) return hit;
  const names = process.platform === 'win32' || process.platform === 'darwin' ? [] : ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'];
  for (const name of names) {
    const found = (process.env.PATH || '').split(delimiter).map((d) => join(d, name)).find((f) => existsSync(f));
    if (found) return found;
  }
  throw new Error('Chrome was not found. Install Google Chrome, or set the CHROME environment variable to its path.');
}

// Set by main() once Chrome runs. A signal (the job runner stopping us) must still remove Chrome and its profile.
let stopChrome = () => {};
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, async () => { await stopChrome(); process.exit(130); });

async function main(software = false) {
  const chromePath = findChrome();
  const root = resolve(DIR);
  const server = createServer((req, res) => {
    try {
      let rel;
      try { rel = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname).replace(/^\/+/, '') || PAGE; }
      catch { res.writeHead(400).end(); return; }
      const file = resolve(root, rel);
      if (!file.startsWith(root + sep) || !statSync(file, { throwIfNoEntry: false })?.isFile()) { res.writeHead(404).end(); return; }
      res.writeHead(200, { 'Content-Type': TYPES[extname(file).toLowerCase()] || 'application/octet-stream' });
      res.end(readFileSync(file));
    } catch { res.writeHead(500).end(); }
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));

  const debugPort = 9300 + Math.floor(Math.random() * 500);
  const profile = mkdtempSync(join(tmpdir(), 'webvideo-cdp-'));
  let chromeError = null;
  // software: Chrome's own CPU renderer (SwiftShader), for when the graphics card gives this session no WebGL
  const gpuFlags = software ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
    : ['--force_high_performance_gpu', '--ignore-gpu-blocklist', '--enable-gpu'];
  const chrome = spawn(chromePath, ['--headless=new', '--mute-audio', ...gpuFlags,
    `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, `--window-size=${width},${height}`, '--no-first-run', 'about:blank'],
  { stdio: 'ignore' });
  chrome.on('error', (e) => { chromeError = e; });
  // Wait for Chrome to really exit before deleting its profile: deleting straight after kill() races with a Chrome
  // that is still shutting down and re-creates files, and the profile survives.
  const chromeGone = () => new Promise((r) => { if (chrome.exitCode !== null || chrome.signalCode) return r(); chrome.once('exit', r); setTimeout(r, 4000); });
  stopChrome = async () => { try { const gone = chromeGone(); chrome.kill(); await gone; await sleep(300); rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }); } catch { /* best effort on a signal */ } };
  let ws = null;
  try {
    let page;
    for (let i = 0; i < 60 && !page; i++) {
      if (chromeError) throw new Error(`could not start Chrome at ${chromePath}: ${chromeError.message}`);
      try { page = (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find((t) => t.type === 'page'); }
      catch { /* not listening yet */ }
      if (!page) await sleep(500);
    }
    if (!page) throw new Error(`Chrome did not open its debugging port ${debugPort}`);

    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = () => bad(new Error('DevTools connection failed')); });
    let id = 0;
    const waiting = new Map();
    const pageErrors = [];   // the page's own errors, shown if it never gets ready
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; pageErrors.push(d.exception?.description || d.text); }
      if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') pageErrors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
      if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') pageErrors.push(`${m.params.entry.text} ${m.params.entry.url || ''}`);
      const done = waiting.get(m.id); if (done) { waiting.delete(m.id); done(m); }
    };
    const send = (method, params = {}) => new Promise((ok) => { const i = ++id; waiting.set(i, ok); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (expression) => {
      const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 600));
      return r.result?.result?.value;
    };

    await send('Runtime.enable'); await send('Log.enable');
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    const url = new URL(`http://127.0.0.1:${server.address().port}/${encodeURIComponent(PAGE)}`);
    if (QUERY) url.search = QUERY.replace(/^\?/, '');
    await send('Page.navigate', { url: url.href });

    // Wait for sceneReady (60 s at most). A rejected sceneReady, or a page without renderAt, stops the job with the reason.
    const READY = `(async () => {
      if (!window.sceneReady) return 'wait';
      if (typeof window.renderAt !== 'function') return 'error: the page has no window.renderAt(t)';
      const limit = new Promise((_, no) => setTimeout(() => no(new Error('sceneReady did not resolve within 60 s')), 60000));
      try { await Promise.race([window.sceneReady, limit]); return 'ok'; } catch (e) { return 'error: ' + e.message; }
    })()`;
    let state = 'wait';
    for (let i = 0; i < 120 && state === 'wait'; i++) {
      state = await ev(READY);
      if (state === 'wait') await sleep(250);
    }
    if (state !== 'ok') throw new Error((state === 'wait' ? 'the page never set window.sceneReady' : `page not ready: ${state}`) +
      (pageErrors.length ? `; the page said: ${pageErrors.slice(0, 5).join(' | ').slice(0, 1200)}` : ''));
    console.log('gpu:', await ev(`(() => { const g = document.createElement('canvas').getContext('webgl2'); const x = g && g.getExtension('WEBGL_debug_renderer_info'); return x ? g.getParameter(x.UNMASKED_RENDERER_WEBGL) : 'no WebGL2'; })()`));

    const dir = join(OUT, 'frames');
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    for (let f = 0; f < frames; f++) {
      await ev(`window.renderAt(${f / fps})`);
      const shot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 92 });
      if (!shot.result) throw new Error(`frame ${f} failed: ${JSON.stringify(shot.error)}`);
      writeFileSync(join(dir, `f${String(f).padStart(5, '0')}.jpg`), Buffer.from(shot.result.data, 'base64'));
    }
    console.log(`rendered ${frames} frames (${secs} s at ${fps} fps, ${width}x${height})`);
  } finally {
    ws?.close();
    server.close();
    await stopChrome();
  }
}

try {
  await main();
} catch (e) {
  // Windows gives a job with nobody signed in no hardware WebGL; draw on the CPU instead (slower, same pictures).
  if (!/WebGL context/i.test(e.message)) { console.error(`render.mjs: ${e.message}`); process.exit(1); }
  console.log('render.mjs: no WebGL from the graphics card in this session (often: nobody is signed in); drawing on the CPU, slower');
  try { await main(true); } catch (e2) { console.error(`render.mjs: ${e2.message}`); process.exit(1); }
}
