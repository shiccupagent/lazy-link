// test.mjs CHROME URL FAKE.wav SHOT.png - drives the recorder page in headless Chrome with a fake mic playing FAKE.wav:
// start, record two lines (the room-tone line and one spoken line), play back, then screenshot. Prints the page's
// messages and the recorder state; exits 1 on any failure. Used on the Mac and on the PC (voice/install.py --test).
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [CHROME, URL, FAKE, SHOT] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = mkdtempSync(join(tmpdir(), 'voice-cdp-'));
const port = 9300 + Math.floor(Math.random() * 500);
const chrome = spawn(CHROME, ['--headless=new', '--mute-audio', '--no-first-run', '--no-default-browser-check',
  `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, '--window-size=1280,860',
  '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', ...(FAKE === '-' ? [] : [`--use-file-for-fake-audio-capture=${FAKE}`]),
  '--autoplay-policy=no-user-gesture-required', 'about:blank'], { stdio: 'ignore' });
async function cleanup() {
  const gone = new Promise((r) => { chrome.once('exit', r); setTimeout(r, 4000); });
  try { chrome.kill(); } catch {}
  await gone; await sleep(300);
  rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
}
for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, async () => { await cleanup(); process.exit(130); });

let ws, id = 0; const waiting = new Map(), logs = [];
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id; waiting.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params }));
});
const js = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true, userGesture: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};
const key = async (code, k = ' ') => {
  for (const type of ['keyDown', 'keyUp']) await send('Input.dispatchKeyEvent', { type, code, key: k, windowsVirtualKeyCode: k === ' ' ? 32 : 0 });
};
const until = async (expr, what, secs = 15) => {
  for (let i = 0; i < secs * 5; i++) { if (await js(expr)) return; await sleep(200); }
  throw new Error('timed out waiting for ' + what);
};

let ok = false;
try {
  let targets;
  for (let i = 0; i < 50; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (targets.length) break; } catch {}
    await sleep(200);
  }
  ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r));
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waiting.has(d.id)) { const w = waiting.get(d.id); waiting.delete(d.id); d.error ? w.rej(new Error(d.error.message)) : w.res(d.result); }
    if (d.method === 'Runtime.consoleAPICalled') logs.push(d.params.args.map((a) => a.value).join(' '));
    if (d.method === 'Runtime.exceptionThrown') logs.push('EXCEPTION ' + d.params.exceptionDetails.exception?.description);
  });
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: URL });
  await until(`!!document.getElementById('line') && document.getElementById('line').textContent.length > 0`, 'page');
  const before = await js(`document.getElementById('count').textContent + ' | ' + document.getElementById('line').textContent`);
  await js(`document.getElementById('go').click()`);
  await until(`document.getElementById('cover').hidden`, 'mic start');
  // the first open line: if it is room tone it stops by itself after 3 s
  const room = await js(`document.getElementById('line').classList.contains('room')`);
  await key('Space');
  if (room) await until(`/Saved|noisy/.test(document.getElementById('msg').textContent)`, 'room tone saved', 10);
  else { await sleep(2500); await key('Space'); await until(`/Saved|loud|quiet/.test(document.getElementById('msg').textContent)`, 'take saved'); }
  const m1 = await js(`document.getElementById('msg').textContent`);
  if (/noisy/.test(m1)) await key('ArrowRight', 'ArrowRight');   // fake mics are loud; move on anyway
  const second = await js(`document.getElementById('count').textContent + ' | ' + document.getElementById('line').textContent`);
  await key('Space'); await sleep(2500); await key('Space');
  await until(`/Saved|loud|quiet/.test(document.getElementById('msg').textContent)`, 'second take saved');
  const m2 = await js(`document.getElementById('msg').textContent`);
  await key('ArrowLeft', 'ArrowLeft');
  await js(`document.getElementById('play').click()`);
  await sleep(500);
  const st = await js(`fetch('/api/state').then(r => r.json()).then(s => s.voices.map(v => v.id + ' ' + v.lines.filter(l => l.done).map(l => l.n + ':' + l.secs + 's/' + l.peak_db + 'dB').join(',')).join(' | '))`);
  const mic = await js(`document.getElementById('mic').selectedOptions[0]?.textContent`);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(SHOT, Buffer.from(shot.data, 'base64'));
  console.log(JSON.stringify({ before, first: m1, second, secondMsg: m2, mic, state: st, logs }, null, 1));
  ok = true;
} catch (e) {
  console.error('FAIL', e.message, logs);
} finally {
  try { ws?.close(); } catch {}
  await cleanup();
  process.exit(ok ? 0 : 1);
}
