import test from "node:test";
import assert from "node:assert/strict";
import {
  TERMINAL_THEME_DARK,
  TERMINAL_THEME_LIGHT,
  terminalThemeForAppTheme
} from "./theme.js";

function srgbChannelToLinear(value) {
  const normalized = value / 255;
  if (normalized <= 0.04045) {
    return normalized / 12.92;
  }
  return ((normalized + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex) {
  const normalized = String(hex || "").trim();
  const match = /^#([0-9a-f]{6})$/i.exec(normalized);
  assert.ok(match, `expected full hex color, got '${hex}'`);

  const channels = [0, 2, 4].map((offset) => Number.parseInt(match[1].slice(offset, offset + 2), 16));
  const [red, green, blue] = channels.map(srgbChannelToLinear);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(colorA, colorB) {
  const luminanceA = relativeLuminance(colorA);
  const luminanceB = relativeLuminance(colorB);
  const lighter = Math.max(luminanceA, luminanceB);
  const darker = Math.min(luminanceA, luminanceB);
  return (lighter + 0.05) / (darker + 0.05);
}

test("terminalThemeForAppTheme preserves the dark terminal palette", () => {
  const theme = terminalThemeForAppTheme("dark");

  assert.equal(theme, TERMINAL_THEME_DARK);
  assert.equal(theme.background, "#0b1018");
  assert.equal(theme.foreground, "#e7edf7");
  assert.equal(theme.cursor, "#10a37f");
});

test("terminalThemeForAppTheme resolves light palette for light and fallback values", () => {
  assert.equal(terminalThemeForAppTheme("light"), TERMINAL_THEME_LIGHT);
  assert.equal(terminalThemeForAppTheme("system"), TERMINAL_THEME_LIGHT);
  assert.equal(terminalThemeForAppTheme(""), TERMINAL_THEME_LIGHT);
});

test("light terminal palette keeps foreground readable on ANSI black backgrounds", () => {
  const contrast = contrastRatio(TERMINAL_THEME_LIGHT.foreground, TERMINAL_THEME_LIGHT.black);
  assert.ok(
    contrast >= 4.5,
    `expected >=4.5:1 contrast for foreground against ANSI black in light theme, got ${contrast.toFixed(2)}`
  );
});
