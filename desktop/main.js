'use strict';
const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, session, dialog } = require('electron');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const argument = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
};
const root = path.resolve(argument('--app-root', path.dirname(process.execPath)));
const localData = process.env.LOCALAPPDATA || path.join(app.getPath('appData'), '..', 'Local');
const data = path.resolve(argument('--data-dir', path.join(localData, 'XianyuSeller')));
const logs = path.join(data, 'logs');
fs.mkdirSync(logs, { recursive: true });
app.setName('鱼管家');
app.setAppUserModelId('cn.fishmanager.desktop');
app.setPath('userData', path.join(data, 'desktop-profile'));
const quittingCommand = process.argv.includes('--quit');
const stateFile = path.join(data, 'desktop-window.json');
let win, tray, backend, origin;
let closing = false;
let generation = 0;
let status = { type: 'progress', stage: '正在启动鱼管家', detail: '正在准备你的工作台，请稍候。' };
let secret = '';
const startupUrl = pathToFileURL(path.join(__dirname, 'startup.html')).href;
const icon = nativeImage.createFromPath(path.join(__dirname, 'icon.png'));

function showWindow() {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}
function update(next) {
  status = next;
  if (win && !win.isDestroyed()) win.webContents.send('desktop:status', status);
}
function safeExternal(url) {
  try {
    const parsed = new URL(url);
    if (['https:', 'http:'].includes(parsed.protocol) && !['127.0.0.1', 'localhost'].includes(parsed.hostname)) {
      void shell.openExternal(parsed.href);
    }
  } catch { /* Invalid URLs never leave the app. */ }
}
function allowed(url) {
  if (url === startupUrl) return true;
  try { return Boolean(origin) && new URL(url).origin === origin; } catch { return false; }
}
async function failure(detail = '数据已保留。可以重新尝试，或打开诊断文件夹。') {
  update({ type: 'error', stage: '鱼管家暂时未能就绪', detail });
  if (win && !win.isDestroyed()) {
    await win.loadURL(startupUrl);
    showWindow();
  }
}
function startBackend() {
  if (backend || closing) return;
  const current = ++generation;
  origin = undefined;
  secret = crypto.randomBytes(32).toString('hex');
  update({ type: 'progress', stage: '正在检查本机数据', detail: '首次启动可能需要几分钟，请稍候。' });
  const stderr = fs.openSync(path.join(logs, 'desktop-process.log'), 'a');
  backend = spawn(path.join(root, 'runtime', 'python', 'python.exe'), [
    '-m', 'app.desktop.runtime', '--app-root', root, '--data-dir', data,
  ], {
    cwd: root, windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', FISH_DESKTOP_TOKEN: secret },
    stdio: ['pipe', 'pipe', stderr],
  });
  fs.closeSync(stderr);
  backend.stdin.on('error', () => {});
  let pending = '';
  backend.stdout.setEncoding('utf8');
  backend.stdout.on('data', chunk => {
    pending += chunk;
    const lines = pending.split('\n');
    pending = lines.pop();
    for (const line of lines) {
      let event;
      try { event = JSON.parse(line); } catch { continue; }
      if (current !== generation || closing) continue;
      if (event.type === 'ready') {
        let parsed;
        try { parsed = new URL(event.url); } catch { continue; }
        if (parsed.hostname !== '127.0.0.1' || parsed.protocol !== 'http:') continue;
        const changed = origin !== parsed.origin;
        origin = parsed.origin;
        update({ type: 'ready', stage: '工作台已就绪', detail: '' });
        if (changed) void win.loadURL(origin).catch(() => failure());
      } else if (event.type === 'error') {
        void failure(event.detail);
      } else if (event.type === 'progress') update(event);
    }
  });
  backend.once('error', () => { if (current === generation) backend = undefined; if (!closing && current === generation) void failure('程序文件不完整，请重新运行安装程序。'); });
  backend.once('exit', () => {
    if (current === generation) backend = undefined;
    if (!closing && current === generation) void failure();
  });
}
async function stopBackend() {
  const child = backend;
  if (!child) return;
  await new Promise(resolve => {
    const timeout = setTimeout(() => { child.kill(); resolve(); }, 30000);
    child.once('exit', () => { clearTimeout(timeout); resolve(); });
    if (child.exitCode !== null || child.killed) { clearTimeout(timeout); resolve(); return; }
    child.stdin.end(JSON.stringify({ action: 'stop' }) + '\n');
  });
  backend = undefined;
}
async function quit() {
  if (closing) return;
  closing = true;
  update({ type: 'progress', stage: '正在安全退出', detail: '正在停止同步并保存本机数据。' });
  await stopBackend();
  try { fs.unlinkSync(stateFile); } catch {}
  if (tray) tray.destroy();
  app.exit(0);
}

const ownsLock = app.requestSingleInstanceLock({ quit: quittingCommand });
if (!ownsLock) {
  if (quittingCommand) {
    // Wait only for the existing desktop process, never its arbitrary descendants.
    const deadline = Date.now() + 45000;
    const waiting = setInterval(() => {
      if (!fs.existsSync(stateFile)) { clearInterval(waiting); app.exit(0); }
      else if (Date.now() > deadline) { clearInterval(waiting); app.exit(1); }
    }, 200);
  } else app.quit();
} else if (quittingCommand) {
  try { fs.unlinkSync(stateFile); } catch {}
  app.quit();
} else {
  app.on('second-instance', (_event, _argv, _cwd, extra) => {
    if (extra && extra.quit) void quit(); else showWindow();
  });
  app.on('before-quit', event => { if (!closing) { event.preventDefault(); void quit(); } });
  app.on('window-all-closed', () => {});
  app.whenReady().then(() => {
    fs.writeFileSync(stateFile, JSON.stringify({ pid: process.pid, version: app.getVersion() }));
    const desktopSession = session.defaultSession;
    desktopSession.webRequest.onBeforeSendHeaders((details, callback) => {
      const headers = { ...details.requestHeaders };
      for (const name of Object.keys(headers)) {
        if (name.toLowerCase() === 'x-fish-desktop') delete headers[name];
      }
      if (origin && new URL(details.url).origin === origin) headers['X-Fish-Desktop'] = secret;
      callback({ requestHeaders: headers });
    });
    desktopSession.setPermissionRequestHandler((contents, permission, callback) => {
      callback(allowed(contents.getURL()) && permission === 'clipboard-sanitized-write');
    });
    desktopSession.setPermissionCheckHandler((contents, permission) => Boolean(contents) && allowed(contents.getURL()) && permission === 'clipboard-sanitized-write');
    desktopSession.on('will-download', (_event, item) => {
      item.setSaveDialogOptions({ title: '保存导出文件', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename())) });
    });
    win = new BrowserWindow({
      width: 1280, height: 860, minWidth: 880, minHeight: 640,
      title: '鱼管家', icon, show: !process.argv.includes('--background'), backgroundColor: '#f7f8fa',
      webPreferences: { preload: path.join(__dirname, 'preload.js'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true },
    });
    win.on('close', event => { if (!closing) { event.preventDefault(); win.hide(); } });
    win.webContents.on('will-navigate', (event, url) => { if (!allowed(url)) { event.preventDefault(); safeExternal(url); } });
    win.webContents.setWindowOpenHandler(({ url }) => {
      if (origin && allowed(url)) {
        return { action: 'allow', overrideBrowserWindowOptions: {
          parent: win, width: 1080, height: 760, icon,
          webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true },
        } };
      }
      safeExternal(url);
      return { action: 'deny' };
    });
    win.webContents.on('did-create-window', child => {
      child.setMenu(null);
      child.webContents.on('will-navigate', (event, url) => {
        if (!allowed(url)) { event.preventDefault(); safeExternal(url); }
      });
      child.webContents.setWindowOpenHandler(({ url }) => { safeExternal(url); return { action: 'deny' }; });
    });
    win.webContents.on('render-process-gone', () => { if (!closing) void failure('窗口已停止响应。数据仍在本机，可以重新尝试。'); });
    win.webContents.on('did-finish-load', () => win.webContents.send('desktop:status', status));
    const isStartup = event => event.sender === win.webContents && event.senderFrame.url === startupUrl;
    ipcMain.handle('desktop:status', event => isStartup(event) ? status : null);
    ipcMain.handle('desktop:diagnostics', event => { if (isStartup(event)) return shell.openPath(logs); });
    ipcMain.handle('desktop:quit', event => { if (isStartup(event)) void quit(); });
    ipcMain.handle('desktop:retry', async event => {
      if (!isStartup(event)) return;
      generation++;
      await stopBackend();
      startBackend();
    });
    const menu = [
      { label: '鱼管家', submenu: [
        { label: '打开工作台', click: showWindow },
        { label: '打开数据文件夹', click: () => shell.openPath(data) },
        { type: 'separator' }, { label: '彻底退出', click: quit },
      ] },
      { label: '编辑', submenu: [{ role: 'undo', label: '撤销' }, { role: 'redo', label: '重做' }, { type: 'separator' }, { role: 'cut', label: '剪切' }, { role: 'copy', label: '复制' }, { role: 'paste', label: '粘贴' }, { role: 'selectAll', label: '全选' }] },
      { label: '视图', submenu: [{ role: 'resetZoom', label: '实际大小' }, { role: 'zoomIn', label: '放大' }, { role: 'zoomOut', label: '缩小' }, { role: 'togglefullscreen', label: '全屏' }] },
      { label: '帮助', submenu: [{ label: '诊断文件夹', click: () => shell.openPath(logs) }, { label: '关于鱼管家', click: () => dialog.showMessageBox(win, { title: '鱼管家', message: '鱼管家桌面版 ' + app.getVersion(), detail: '独立开发的本地经营工具，非闲鱼、闲管家官方产品。关闭窗口后继续在托盘同步；选择“彻底退出”可停止全部服务。' }) }] },
    ];
    Menu.setApplicationMenu(Menu.buildFromTemplate(menu));
    tray = new Tray(icon);
    tray.setToolTip('鱼管家 · 后台同步');
    tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开鱼管家', click: showWindow }, { type: 'separator' }, { label: '彻底退出', click: quit }]));
    tray.on('double-click', showWindow);
    void win.loadURL(startupUrl).then(startBackend);
  }).catch(() => { void failure(); });
}
