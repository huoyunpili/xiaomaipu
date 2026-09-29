const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function shellHarness() {
  const app = new EventEmitter();
  const events = [], handlers = new Map();
  let window, child, headers;
  Object.assign(app, {
    getPath: () => 'C:/synthetic/AppData/Roaming', setName() {}, setAppUserModelId() {},
    setPath() {}, requestSingleInstanceLock: () => true, whenReady: () => Promise.resolve(),
    getVersion: () => 'test', exit: code => events.push(['exit', code]),
  });
  class BrowserWindow extends EventEmitter {
    constructor() {
      super(); window = this; this.webContents = new EventEmitter();
      Object.assign(this.webContents, { send() {}, setWindowOpenHandler: fn => { this.openWindow = fn; }, getURL: () => this.url });
    }
    isDestroyed() { return false; }
    isMinimized() { return false; }
    show() { events.push(['show']); }
    focus() {}
    hide() { events.push(['hide']); }
    loadURL(url) { this.url = url; return Promise.resolve(); }
  }
  const electron = {
    app, BrowserWindow,
    Menu: { buildFromTemplate: value => value, setApplicationMenu() {} },
    Tray: class extends EventEmitter { setToolTip() {} setContextMenu() {} destroy() {} },
    nativeImage: { createFromPath() {} },
    ipcMain: { handle: (name, fn) => handlers.set(name, fn) },
    shell: { openPath() {}, openExternal() {} }, dialog: {},
    session: { defaultSession: {
      webRequest: { onBeforeSendHeaders: fn => { headers = fn; } },
      setPermissionRequestHandler() {}, setPermissionCheckHandler() {}, on() {},
    } },
  };
  const spawn = () => {
    child = new EventEmitter(); child.exitCode = null;
    child.stdout = new EventEmitter(); child.stdout.setEncoding = () => {};
    child.stdin = new EventEmitter();
    child.stdin.end = () => { events.push(['stop']); child.exitCode = 0; child.emit('exit', 0); };
    return child;
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../desktop/main.js'), 'utf8'), {
    require: name => name === 'electron' ? electron : name === 'node:child_process' ? { spawn }
      : name === 'node:fs' ? { mkdirSync() {}, writeFileSync() {}, openSync: () => 1, closeSync() {}, unlinkSync() {} }
      : require(name),
    __dirname: path.resolve(__dirname, '../desktop'),
    process: { argv: [], execPath: 'C:/synthetic/FishManager.exe', env: {}, pid: 123 },
    URL, setTimeout, clearTimeout, setInterval, clearInterval,
  });
  await new Promise(resolve => setImmediate(resolve));
  return { app, window, child, events, headers };
}

test('closing the window keeps the backend running; tray quit stops it', async () => {
  const h = await shellHarness();
  let prevented = false;
  h.window.emit('close', { preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(h.events, [['hide']]);
  h.app.emit('second-instance', {}, [], '', {});
  assert.deepEqual(h.events.at(-1), ['show']);
  assert.ok(!h.events.some(event => event[0] === 'stop'));
  h.app.emit('second-instance', {}, [], '', { quit: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(h.events.slice(-2), [['stop'], ['exit', 0]]);
});

test('desktop credentials are attached only to the active local backend', async () => {
  const h = await shellHarness();
  h.child.stdout.emit('data', JSON.stringify({ type: 'ready', url: 'http://127.0.0.1:18799' }) + '\n');
  let local, remote;
  h.headers({ url: 'http://127.0.0.1:18799/login/', requestHeaders: {} }, result => { local = result; });
  h.headers({ url: 'https://example.com/', requestHeaders: { 'x-fish-desktop': 'stale' } }, result => { remote = result; });
  assert.equal(local.requestHeaders['X-Fish-Desktop'].length, 64);
  assert.equal(Object.keys(remote.requestHeaders).length, 0);
  assert.equal(h.window.url, 'http://127.0.0.1:18799');
  const preview = h.window.openWindow({ url: 'http://127.0.0.1:18799/product-image/1/' });
  assert.equal(preview.action, 'allow');
  assert.equal(preview.overrideBrowserWindowOptions.parent, h.window);
  assert.equal(h.window.url, 'http://127.0.0.1:18799');
});
