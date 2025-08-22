const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("api", {
  generateDoc: (repoUrl) => ipcRenderer.invoke("generate-doc", { repoUrl }),
  onLog: (cb) => ipcRenderer.on("log", (_e, line) => cb(line)),
});
