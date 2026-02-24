export const TERMINAL_THEME_LIGHT = Object.freeze({
  background: "#f8fbff",
  foreground: "#0f172a",
  cursor: "#0f8f70",
  selectionBackground: "rgba(15, 23, 42, 0.2)",
  black: "#d0dae6",
  red: "#b42318",
  green: "#0f8f70",
  yellow: "#b45309",
  blue: "#2563eb",
  magenta: "#9333ea",
  cyan: "#0f766e",
  white: "#64748b",
  brightBlack: "#334155",
  brightRed: "#dc2626",
  brightGreen: "#10b981",
  brightYellow: "#d97706",
  brightBlue: "#1d4ed8",
  brightMagenta: "#a855f7",
  brightCyan: "#0d9488",
  brightWhite: "#0f172a"
});

export const TERMINAL_THEME_DARK = Object.freeze({
  background: "#0b1018",
  foreground: "#e7edf7",
  cursor: "#10a37f",
  selectionBackground: "rgba(148, 163, 184, 0.28)",
  black: "#0f172a",
  red: "#dc4c40",
  green: "#19a683",
  yellow: "#f5b74a",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#2dd4bf",
  white: "#cbd5e1",
  brightBlack: "#64748b",
  brightRed: "#fb7185",
  brightGreen: "#34d399",
  brightYellow: "#facc15",
  brightBlue: "#93c5fd",
  brightMagenta: "#e879f9",
  brightCyan: "#5eead4",
  brightWhite: "#f8fafc"
});

export function terminalThemeForAppTheme(appTheme) {
  return String(appTheme || "").toLowerCase() === "dark" ? TERMINAL_THEME_DARK : TERMINAL_THEME_LIGHT;
}
