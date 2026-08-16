export {};

declare global {
  interface Window {
    translatorDesktop?: {
      openOverlay: () => Promise<boolean>;
      closeOverlay: () => Promise<boolean>;
      isOverlayOpen: () => Promise<boolean>;
      publishOverlaySnapshot: (snapshot: import("./lib/overlay").OverlaySnapshot) => void;
      onOverlaySnapshot: (
        callback: (snapshot: import("./lib/overlay").OverlaySnapshot) => void,
      ) => () => void;
    };
  }
}
