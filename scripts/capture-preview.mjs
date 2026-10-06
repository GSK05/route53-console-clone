import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const output = path.resolve('preview');
await mkdir(output, { recursive: true });
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const browser = spawn(chrome, [
  '--headless=new', '--disable-gpu', '--remote-debugging-port=9223', '--window-size=1440,1000',
  `--user-data-dir=${path.join(output, 'browser-profile')}`,
  '--no-first-run', '--no-default-browser-check', 'about:blank',
], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
let browserError = '';
browser.stderr.on('data', chunk => { browserError += chunk.toString(); });
browser.on('error', error => { browserError += error.message; });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
try {
  let tabs;
  for (let i = 0; i < 40; i++) {
    try { tabs = await (await fetch('http://127.0.0.1:9223/json')).json(); break; }
    catch { await pause(250); }
  }
  if (!tabs) throw new Error(`Headless Chrome did not start: ${browserError.slice(-2000)}`);
  socket = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    const request = pending.get(message.id);
    if (request) { pending.delete(message.id); message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result); }
  });
  socket.addEventListener('close', () => { for (const request of pending.values()) request.reject(new Error(`Browser closed: ${browserError.slice(-1500)}`)); pending.clear(); });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => (await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result.value;
  const waitFor = async expression => {
    for (let i = 0; i < 80; i++) { if (await evaluate(expression)) return; await pause(250); }
    throw new Error(`Page did not become ready: ${expression}`);
  };
  await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: 'http://127.0.0.1:3000/' });
  await waitFor("!!document.querySelector('.login-card')");
  let shot = await call('Page.captureScreenshot', { format: 'png' });
  await writeFile(path.join(output, 'login.png'), Buffer.from(shot.data, 'base64'));
  const loginStatus = await evaluate("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'demo',password:'route53demo'})}).then(r=>r.status)");
  if (loginStatus !== 200) throw new Error(`Demo sign-in failed (${loginStatus})`);
  await call('Page.navigate', { url: 'http://127.0.0.1:3000/zones' });
  await waitFor("!!document.querySelector('.table-panel') && document.querySelectorAll('tbody tr').length > 0");
  shot = await call('Page.captureScreenshot', { format: 'png' });
  await writeFile(path.join(output, 'hosted-zones.png'), Buffer.from(shot.data, 'base64'));
  const zoneId = await evaluate("fetch('/api/zones').then(r=>r.json()).then(d=>d.items.find(z=>z.type==='public')?.id)");
  await call('Page.navigate', { url: `http://127.0.0.1:3000/zones/${zoneId}` });
  await waitFor("!!document.querySelector('.details-panel') && document.querySelectorAll('tbody tr').length > 0");
  shot = await call('Page.captureScreenshot', { format: 'png' });
  await writeFile(path.join(output, 'zone-records.png'), Buffer.from(shot.data, 'base64'));
  console.log('Captured preview/login.png, preview/hosted-zones.png, preview/zone-records.png');
} finally {
  socket?.close();
  browser.kill();
}
