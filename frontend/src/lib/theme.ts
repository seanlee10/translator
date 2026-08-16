export type Theme = "dark" | "light";

export const THEME_STORAGE_KEY = "live-caption-theme";

export function normalizeTheme(value: string | null): Theme {
  return value === "light" || value === "dark" ? value : "dark";
}

export function nextTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}
