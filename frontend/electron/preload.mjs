import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("translatorDesktop", {
  openOverlay: () => ipcRenderer.invoke("overlay:open"),
  closeOverlay: () => ipcRenderer.invoke("overlay:close"),
  isOverlayOpen: () => ipcRenderer.invoke("overlay:is-open"),
  publishOverlaySnapshot: (snapshot) => ipcRenderer.send("overlay:update", snapshot),
  requestOverlaySnapshot: () => ipcRenderer.send("overlay:request-snapshot"),
  onOverlaySnapshot: (callback) => {
    const listener = (_event, snapshot) => callback(snapshot);
    ipcRenderer.on("overlay:snapshot", listener);
    return () => ipcRenderer.removeListener("overlay:snapshot", listener);
  },
});
