import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const frontendRoot = path.resolve(import.meta.dirname, "..");

const read = (relativePath: string) =>
  fs.readFileSync(path.join(frontendRoot, relativePath), "utf8");

describe("Electron overlay snapshot handshake", () => {
  it("requests the latest snapshot only after the overlay listener is installed", () => {
    const preload = read("electron/preload.mjs");
    const main = read("electron/main.mjs");
    const overlay = read("src/components/OverlayView.tsx");

    expect(preload).toContain("requestOverlaySnapshot");
    expect(main).toContain('ipcMain.on("overlay:request-snapshot"');
    expect(main).toContain("isWindowSender(event.sender, overlayWindow)");
    expect(main).toContain("isWindowSender(event.sender, controlWindow)");
    expect(main).not.toContain('webContents.once("did-finish-load"');

    const listenerIndex = overlay.indexOf("onOverlaySnapshot");
    const requestIndex = overlay.indexOf("requestOverlaySnapshot");
    expect(listenerIndex).toBeGreaterThan(-1);
    expect(requestIndex).toBeGreaterThan(listenerIndex);
  });
});
