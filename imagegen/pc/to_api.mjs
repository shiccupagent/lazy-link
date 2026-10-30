// Turns ComfyUI workflow templates (the UI format, with subgraphs) into API-format graphs that
// batch.py can queue headless. It asks ComfyUI's own frontend to do it (app.graphToPrompt), so
// it stays right for every template and every ComfyUI version.
// Usage (on the PC, ComfyUI already up): node to_api.mjs http://127.0.0.1:8190 OUTDIR template.json ...
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import net from 'node:net';

const [COMFY, OUT, ...FILES] = process.argv.slice(2);
const CHROME = process.env.CHROME || (process.platform === 'win32'
  ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : 'google-chrome');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, () => { const p = s.address().port; s.close(() => res(p)); }); });

const profile = mkdtempSync(join(tmpdir(), 'imagegen-cdp-'));
let chrome = null;
try {
  const port = await freePort();
  chrome = spawn(CHROME, ['--headless=new', '--mute-audio', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-extensions', 'about:blank'], { stdio: 'ignore' });
  let page = null;
  for (let i = 0; i < 40 && !page; i++) {
    try { page = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page'); } catch { await sleep(500); }
  }
  if (!page) throw new Error('chrome devtools did not come up');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => { const id = ++seq; ws.send(JSON.stringify({ id, method, params })); return new Promise(r => pending.set(id, r)); };
  const evaluate = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 800));
    return r.result?.result?.value;
  };
  await send('Page.navigate', { url: COMFY });
  let ready = false;
  for (let i = 0; i < 120 && !ready; i++) {
    await sleep(500);
    try { ready = await evaluate('!!(window.app && window.app.graph && window.app.loadGraphData)'); } catch { /* loading */ }
  }
  if (!ready) throw new Error('ComfyUI frontend did not load');
  for (const f of FILES) {
    const wf = readFileSync(f, 'utf8');
    const out = await evaluate(`(async () => {
      await window.app.loadGraphData(${wf}, true, true, 'imagegen');
      const p = await window.app.graphToPrompt();
      return JSON.stringify(p.output);
    })()`);
    const name = basename(f).replace(/\.json$/, '') + '.api.json';
    writeFileSync(join(OUT, name), JSON.stringify(JSON.parse(out), null, 1));
    console.log(`converted ${basename(f)} -> ${name} (${Object.keys(JSON.parse(out)).length} nodes)`);
  }
  ws.close();
} finally {
  if (chrome) chrome.kill();
  await sleep(1000);
  rmSync(profile, { recursive: true, force: true });
}
