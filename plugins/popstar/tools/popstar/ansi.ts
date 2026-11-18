// Hand-rolled ANSI for a dark, monospace terminal: amber accents, green ticks, red crosses. No dependencies.

export type Paint = Record<"amber" | "green" | "red" | "dim" | "bold" | "grey" | "white", (s: string) => string>;

export function makePaint(enabled: boolean): Paint {
  const wrap = (code: string) => (s: string) => (enabled ? `\x1b[${code}m${s}\x1b[0m` : s);
  return {
    amber: wrap("38;5;215"),
    green: wrap("32"),
    red: wrap("31"),
    dim: wrap("2"),
    bold: wrap("1"),
    grey: wrap("90"),
    white: wrap("97"),
  };
}

/** Colors on unless NO_COLOR is set (the status command keeps its own always-on palette). */
export const paint: Paint = makePaint(!process.env.NO_COLOR);

export const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;]*m/g, "");
export const visibleLength = (s: string): number => [...stripAnsi(s)].length;
export const padEnd = (s: string, n: number): string => s + " ".repeat(Math.max(0, n - visibleLength(s)));

/** Pad every column to its widest visible cell. Rows may be ragged; the last column is never padded. */
export function table(rows: string[][], indent = "  ", gap = 2): string[] {
  const widths: number[] = [];
  for (const row of rows) row.forEach((cell, i) => (widths[i] = Math.max(widths[i] ?? 0, visibleLength(cell))));
  return rows.map((row) =>
    indent + row.map((cell, i) => (i === row.length - 1 ? cell : padEnd(cell, widths[i] ?? 0))).join(" ".repeat(gap)),
  );
}

export const OK = "✓";
export const BAD = "✗";
export const ARROW = "▶";
export const DOT = "·";
