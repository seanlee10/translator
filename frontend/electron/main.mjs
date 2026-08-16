import { app, BrowserWindow, ipcMain, screen } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  createOverlayWindowOptions,
  isWindowSender,
  selectOverlayDisplay,
} from "./window-config.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isDev = process.argv.includes("--dev");
const devUrl = "http://127.0.0.1:5173";

let controlWindow = null;
let overlayWindow = null;
let latestOverlaySnapshot = null;

function loadApp(window, overlay = false) {
  if (isDev) {
    return window.loadURL(`${devUrl}${overlay ? "/?overlay=1" : ""}`);
  }

  return window.loadFile(path.join(__dirname, "../dist/index.html"), {
    query: overlay ? { overlay: "1" } : undefined,
  });
}

function createControlWindow() {
  controlWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 980,
    minHeight: 700,
    backgroundColor: "#08090a",
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: path.join(__dirname, "preload.mjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  void loadApp(controlWindow);
  controlWindow.on("closed", () => {
    controlWindow = null;
  });
}

function createOverlayWindow() {
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.showInactive();
    return;
  }

  const primary = screen.getPrimaryDisplay();
  const display = selectOverlayDisplay(screen.getAllDisplays(), primary);
  overlayWindow = new BrowserWindow(
    createOverlayWindowOptions(display.bounds, {
      preload: path.join(__dirname, "preload.mjs"),
    }),
  );

  overlayWindow.setAlwaysOnTop(true, "screen-saver", 1);
  overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  overlayWindow.setIgnoreMouseEvents(true, { forward: true });
  overlayWindow.setAutoHideMenuBar(true);

  void loadApp(overlayWindow, true);
  overlayWindow.once("ready-to-show", () => overlayWindow?.showInactive());
  overlayWindow.on("closed", () => {
    overlayWindow = null;
  });
}

app.whenReady().then(() => {
  ipcMain.on("overlay:update", (event, snapshot) => {
    if (!isWindowSender(event.sender, controlWindow)) return;
    latestOverlaySnapshot = snapshot;
    overlayWindow?.webContents.send("overlay:snapshot", snapshot);
  });
  ipcMain.on("overlay:request-snapshot", (event) => {
    if (!isWindowSender(event.sender, overlayWindow)) return;
    if (latestOverlaySnapshot) {
      event.sender.send("overlay:snapshot", latestOverlaySnapshot);
    }
  });
  ipcMain.handle("overlay:open", (event) => {
    if (!isWindowSender(event.sender, controlWindow)) return false;
    createOverlayWindow();
    return true;
  });
  ipcMain.handle("overlay:close", (event) => {
    if (!isWindowSender(event.sender, controlWindow)) return false;
    overlayWindow?.close();
    return true;
  });
  ipcMain.handle(
    "overlay:is-open",
    (event) =>
      isWindowSender(event.sender, controlWindow) &&
      Boolean(overlayWindow && !overlayWindow.isDestroyed()),
  );

  createControlWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createControlWindow();
  });
});

app.on("window-all-closed", () => {
  app.quit();
});
