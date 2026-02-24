import test from "node:test";
import assert from "node:assert/strict";
import {
  TERMINAL_THEME_DARK,
  TERMINAL_THEME_LIGHT,
  terminalThemeForAppTheme
} from "./theme.js";

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
