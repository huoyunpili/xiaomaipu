const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('fishDesktop', Object.freeze({
  retry: () => ipcRenderer.invoke('desktop:retry'),
  diagnostics: () => ipcRenderer.invoke('desktop:diagnostics'),
  quit: () => ipcRenderer.invoke('desktop:quit'),
  onStatus: callback => {
    ipcRenderer.on('desktop:status', (_event, status) => callback(status));
    ipcRenderer.invoke('desktop:status').then(callback);
  },
}));
