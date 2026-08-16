export function selectOverlayDisplay(displays, primaryDisplay) {
  return displays.find((display) => display.id !== primaryDisplay.id) ?? primaryDisplay;
}

export function createOverlayWindowOptions(bounds, webPreferences = {}) {
  return {
    ...bounds,
    transparent: true,
    frame: false,
    focusable: false,
    hasShadow: false,
    skipTaskbar: true,
    backgroundColor: "#00000000",
    show: false,
    fullscreenable: false,
    resizable: false,
    movable: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
      ...webPreferences,
    },
  };
}
