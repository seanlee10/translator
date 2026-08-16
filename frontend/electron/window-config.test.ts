import { describe, expect, it } from "vitest";
import {
  createOverlayWindowOptions,
  selectOverlayDisplay,
} from "./window-config.mjs";

const primary = {
  id: 1,
  bounds: { x: 0, y: 0, width: 1512, height: 982 },
};
const external = {
  id: 2,
  bounds: { x: 1512, y: 0, width: 1920, height: 1080 },
};

describe("selectOverlayDisplay", () => {
  it("prefers an external presentation display", () => {
    expect(selectOverlayDisplay([primary, external], primary)).toBe(external);
  });

  it("falls back to the primary display", () => {
    expect(selectOverlayDisplay([primary], primary)).toBe(primary);
  });
});

describe("createOverlayWindowOptions", () => {
  it("creates a transparent, non-focusable full-display window", () => {
    expect(
      createOverlayWindowOptions(external.bounds, {
        preload: "/app/electron/preload.mjs",
      }),
    ).toMatchObject({
      ...external.bounds,
      transparent: true,
      frame: false,
      focusable: false,
      hasShadow: false,
      skipTaskbar: true,
      backgroundColor: "#00000000",
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: "/app/electron/preload.mjs",
      },
    });
  });
});
