#!/usr/bin/env bun
// comfy_compile: fill a pinned Comfy Cloud API graph by slot NAME, so a render never depends on a past job id (only
// its owner can re-run it) or on flattened subgraph ids like "129:211" (they move when Comfy updates a template).
//
//   bun tools/comfy_compile.ts <graph> [--set slot=value ...] [--inputs inputs.json] [--preset name] [--out file.json] [--list]
//   bun tools/comfy_compile.ts --list                       # every graph
//
// <graph> is a name in workflows/slots.json (pose-restyle, lipsync-ia2v, ...). Later wins: pinned defaults < --preset
// < --inputs (JSON object of slot -> value) < --set. The compiled API graph goes to stdout (or --out), a one-line
// summary to stderr. Submit it with the comfy-cloud MCP: submit_workflow({ workflow: <compiled JSON> }).
// No dependencies. See workflows/README.md.
//
// Where the graphs are read from (first directory that holds a slots.json): POPSTAR_WORKFLOWS_DIR, then the copy bundled
// next to this file (tools/workflows/ inside the Claude Code plugin, ../workflows/ in the repo), then <project>/workflows/
// (project = POPSTAR_ROOT, else the nearest popstar.json at or above the working directory, else the working directory).
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

export type SlotType = "string" | "integer" | "number" | "boolean";
export type Target = { node: string; input: string };
export type Slot = Target & {
  type: SlotType;
  invert?: boolean; // boolean written negated: use_first_frame=true -> bypass=false
  also?: Target[]; // more inputs that always get the same value
  min?: number;
  max?: number;
  enum?: (string | number)[];
  default?: unknown; // documentation; the pinned graph is the source of truth (drift = warning)
  desc?: string;
};
export type GraphEntry = {
  file: string;
  what?: string;
  output?: { node: string; kind: string };
  provenance?: Record<string, unknown>;
  slots: Record<string, Slot>;
  presets?: Record<string, Record<string, unknown>>;
};
export type ApiNode = { class_type: string; inputs: Record<string, unknown>; _meta?: { title?: string } };
export type ApiGraph = Record<string, ApiNode>;

/** The project root without importing the CLI: POPSTAR_ROOT, else the nearest popstar.json above cwd, else cwd. */
function projectRoot(): string {
  if (process.env.POPSTAR_ROOT) return resolve(process.env.POPSTAR_ROOT);
  let dir = process.cwd();
  for (;;) {
    if (existsSync(join(dir, "popstar.json"))) return dir;
    const up = dirname(dir);
    if (up === dir) return process.cwd();
    dir = up;
  }
}

/** The directories the pinned graphs may live in, in priority order (see the header). */
export const workflowsCandidates = (): string[] => [
  ...(process.env.POPSTAR_WORKFLOWS_DIR ? [resolve(process.env.POPSTAR_WORKFLOWS_DIR)] : []),
  join(import.meta.dir, "workflows"), // the plugin layout: plugins/popstar/tools/workflows/
  join(import.meta.dir, "..", "workflows"), // the repo layout: workflows/
  join(projectRoot(), "workflows"), // the project's own copy (a checkout of the repo)
];

/** The first candidate that holds a slots.json; the repo layout when none does, so the error names a real path. */
export function findWorkflowsDir(): string {
  const dirs = workflowsCandidates();
  return dirs.find((d) => existsSync(join(d, "slots.json"))) ?? join(import.meta.dir, "..", "workflows");
}

export const WORKFLOWS_DIR = findWorkflowsDir();
export class CompileError extends Error {}

const USAGE = `usage: bun tools/comfy_compile.ts <graph> [--set slot=value ...] [--inputs inputs.json] [--preset name] [--out file.json] [--list]
       bun tools/comfy_compile.ts --list`;

const readJson = (path: string, label: string): unknown => {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    throw new CompileError(`${label}: cannot read ${path}`);
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new CompileError(`${label}: ${path} is not valid JSON (${(e as Error).message})`);
  }
};

export function loadSlots(dir = WORKFLOWS_DIR): Record<string, GraphEntry> {
  const raw = readJson(join(dir, "slots.json"), "slots") as Record<string, GraphEntry>;
  return Object.fromEntries(Object.entries(raw).filter(([k]) => !k.startsWith("_") && !k.startsWith("$")));
}

export const loadGraph = (entry: GraphEntry, dir = WORKFLOWS_DIR) => readJson(join(dir, entry.file), "graph") as ApiGraph;

export const targetsOf = (s: Slot): Target[] => [{ node: s.node, input: s.input }, ...(s.also ?? [])];
const isLink = (v: unknown) => Array.isArray(v) && v.length === 2 && typeof v[0] === "string" && typeof v[1] === "number";
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The slot's value as the graph holds it, in slot terms (undoes invert). */
export const currentValue = (s: Slot, g: ApiGraph): unknown => {
  const v = g[s.node]?.inputs?.[s.input];
  return s.invert && typeof v === "boolean" ? !v : v;
};

/** Every slot must point at an existing node and a literal (non-link) input of the pinned graph. */
export function checkSlots(entry: GraphEntry, g: ApiGraph): { errors: string[]; warnings: string[] } {
  const errors: string[] = [];
  const warnings: string[] = [];
  for (const [name, s] of Object.entries(entry.slots)) {
    let ok = true;
    for (const t of targetsOf(s)) {
      const where = `slot "${name}" -> ${t.node}.${t.input}`;
      const node = g[t.node];
      if (!node) errors.push(`${where}: node "${t.node}" is missing from ${entry.file}`);
      else if (!node.inputs || !(t.input in node.inputs))
        errors.push(`${where}: node "${t.node}" (${node.class_type}) has no input "${t.input}" in ${entry.file}`);
      else if (isLink(node.inputs[t.input]))
        errors.push(`${where}: that input is a link ${JSON.stringify(node.inputs[t.input])}, not a value`);
      else continue;
      ok = false;
    }
    if (!ok) continue;
    for (const t of s.also ?? [])
      if (!same(g[t.node].inputs[t.input], g[s.node].inputs[s.input]))
        warnings.push(`slot "${name}": ${t.node}.${t.input} and ${s.node}.${s.input} differ in ${entry.file}`);
    if ("default" in s && !same(currentValue(s, g), s.default))
      warnings.push(`slot "${name}": slots.json default differs from ${entry.file} (the graph wins)`);
  }
  for (const [p, values] of Object.entries(entry.presets ?? {}))
    for (const k of Object.keys(values)) if (!entry.slots[k]) errors.push(`preset "${p}": unknown slot "${k}"`);
  return { errors, warnings };
}

/** Coerce a raw value (string from --set, any JSON from --inputs) by slot type; returns what gets written. */
export function coerce(name: string, s: Slot, raw: unknown): unknown {
  const bad = (why: string) => new CompileError(`slot "${name}" (${s.type}): ${why}, got ${JSON.stringify(raw)}`);
  let v: string | number | boolean;
  switch (s.type) {
    case "string":
      if (typeof raw === "string" || typeof raw === "number" || typeof raw === "boolean") v = String(raw);
      else throw bad("expected a string");
      break;
    case "integer": {
      const n = typeof raw === "number" ? raw : typeof raw === "string" && /^\s*-?\d+\s*$/.test(raw) ? Number(raw) : NaN;
      if (!Number.isInteger(n)) throw bad("expected an integer");
      if (!Number.isSafeInteger(n)) throw bad("too large to stay exact in JSON (max 2^53-1)");
      v = n;
      break;
    }
    case "number": {
      const n = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() !== "" ? Number(raw) : NaN;
      if (!Number.isFinite(n)) throw bad("expected a number");
      v = n;
      break;
    }
    case "boolean": {
      const t = typeof raw === "string" || typeof raw === "number" ? String(raw).trim().toLowerCase() : "";
      if (typeof raw === "boolean") v = raw;
      else if (["true", "1", "yes", "on"].includes(t)) v = true;
      else if (["false", "0", "no", "off"].includes(t)) v = false;
      else throw bad("expected true or false");
      break;
    }
    default:
      throw new CompileError(`slot "${name}": unknown type "${String(s.type)}" in slots.json`);
  }
  if (typeof v === "number" && s.min !== undefined && v < s.min) throw bad(`below min ${s.min}`);
  if (typeof v === "number" && s.max !== undefined && v > s.max) throw bad(`above max ${s.max}`);
  if (s.enum && typeof v !== "boolean" && !s.enum.includes(v)) throw bad(`not one of ${s.enum.join(", ")}`);
  return s.invert ? !v : v;
}

/** Pinned graph + slot values -> a new API graph. Unknown slots and bad values are errors (all reported at once). */
export function compile(entry: GraphEntry, g: ApiGraph, values: Record<string, unknown>): ApiGraph {
  const out = structuredClone(g);
  const errors: string[] = [];
  const unknown = Object.keys(values).filter((k) => !entry.slots[k]);
  if (unknown.length)
    errors.push(`unknown slot(s): ${unknown.join(", ")}. Known: ${Object.keys(entry.slots).join(", ")}`);
  for (const [name, raw] of Object.entries(values)) {
    const s = entry.slots[name];
    if (!s) continue;
    try {
      const w = coerce(name, s, raw);
      for (const t of targetsOf(s)) out[t.node].inputs[t.input] = w;
    } catch (e) {
      if (!(e instanceof CompileError)) throw e;
      errors.push(e.message);
    }
  }
  if (errors.length) throw new CompileError(errors.join("\n  "));
  return out;
}

/** JSON with 2-space indent; arrays of primitives stay on one line, so links read as ["129:211", 0]. */
export function format(v: unknown, indent = ""): string {
  const inner = indent + "  ";
  if (Array.isArray(v)) {
    if (v.every((x) => x === null || typeof x !== "object")) return `[${v.map((x) => JSON.stringify(x)).join(", ")}]`;
    return `[\n${v.map((x) => inner + format(x, inner)).join(",\n")}\n${indent}]`;
  }
  if (v !== null && typeof v === "object") {
    const entries = Object.entries(v);
    if (!entries.length) return "{}";
    return `{\n${entries.map(([k, x]) => `${inner}${JSON.stringify(k)}: ${format(x, inner)}`).join(",\n")}\n${indent}}`;
  }
  return JSON.stringify(v);
}

const short = (v: unknown, max = 44): string => {
  const s = JSON.stringify(v) ?? "(absent)";
  if (s.length <= max) return s;
  return typeof v === "string" ? `${s.slice(0, max)}…" (${v.length} chars)` : `${s.slice(0, max)}…`;
};

function listGraph(name: string, e: GraphEntry, g: ApiGraph): string {
  const p: Record<string, unknown> = e.provenance ?? {};
  const out = [`${name}  workflows/${e.file}  ${Object.keys(g).length} nodes` + (e.output ? `  output node ${e.output.node} (${e.output.kind})` : "")];
  if (e.what) out.push(`  ${e.what}`);
  const prov = ["source_job", "template", "pinned"].filter((k) => p[k]).map((k) => `${k}: ${String(p[k])}`);
  if (prov.length) out.push(`  ${prov.join(" · ")}`);
  const rows = Object.entries(e.slots).map(([k, s]) => [
    k,
    targetsOf(s).map((t) => `${t.node}.${t.input}`).join(" + ") + (s.invert ? " (inverted)" : ""),
    s.type + (s.min !== undefined || s.max !== undefined ? ` ${s.min ?? ""}..${s.max ?? ""}` : ""),
    short(currentValue(s, g)),
    [s.desc, s.enum ? `one of: ${s.enum.join(" | ")}` : ""].filter(Boolean).join(" "),
  ]);
  const w = [0, 1, 2].map((i) => Math.max(["slot", "target", "type"][i].length, ...rows.map((r) => r[i].length)));
  out.push("", `  ${"slot".padEnd(w[0])}  ${"target".padEnd(w[1])}  ${"type".padEnd(w[2])}  default (pinned graph)`);
  for (const r of rows) {
    out.push(`  ${r[0].padEnd(w[0])}  ${r[1].padEnd(w[1])}  ${r[2].padEnd(w[2])}  ${r[3]}`);
    if (r[4]) out.push(`  ${"".padEnd(w[0])}  ↳ ${r[4]}`);
  }
  const presets = Object.keys(e.presets ?? {});
  if (presets.length) out.push("", `  presets (--preset <name>): ${presets.join(", ")}`);
  return out.join("\n");
}

function main(argv: string[]) {
  const sets: string[] = [];
  let graphArg: string | undefined;
  let inputsPath: string | undefined;
  let presetName: string | undefined;
  let outPath: string | undefined;
  let list = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new CompileError(`${a} needs a value\n${USAGE}`);
      return v;
    };
    if (a === "--set") sets.push(value());
    else if (a.startsWith("--set=")) sets.push(a.slice(6));
    else if (a === "--inputs") inputsPath = value();
    else if (a === "--preset") presetName = value();
    else if (a === "--out") outPath = value();
    else if (a === "--list") list = true;
    else if (a === "-h" || a === "--help") return void console.log(USAGE);
    else if (a.startsWith("-")) throw new CompileError(`unknown flag ${a}\n${USAGE}`);
    else if (graphArg === undefined) graphArg = a;
    else throw new CompileError(`unexpected argument ${a}\n${USAGE}`);
  }

  const all = loadSlots();
  if (graphArg === undefined) {
    if (!list) throw new CompileError(`missing <graph>; one of: ${Object.keys(all).join(", ")}\n${USAGE}`);
    const w = Math.max(...Object.keys(all).map((k) => k.length));
    for (const [k, e] of Object.entries(all))
      console.log(`${k.padEnd(w)}  ${String(Object.keys(e.slots).length).padStart(2)} slots  ${e.what ?? ""}`);
    return;
  }
  const name = basename(graphArg).replace(/\.api\.json$/, "");
  const entry = all[name];
  if (!entry) throw new CompileError(`unknown graph "${graphArg}"; one of: ${Object.keys(all).join(", ")}`);
  const g = loadGraph(entry);

  const { errors, warnings } = checkSlots(entry, g);
  for (const w of warnings) console.error(`comfy_compile: warning: ${w}`);
  if (errors.length) throw new CompileError(`slots.json does not match the pinned graph:\n  ${errors.join("\n  ")}`);
  if (list) return void console.log(listGraph(name, entry, g));

  const values: Record<string, unknown> = {};
  if (presetName !== undefined) {
    const p = entry.presets?.[presetName];
    if (!p) throw new CompileError(`unknown preset "${presetName}" for ${name}; one of: ${Object.keys(entry.presets ?? {}).join(", ") || "(none)"}`);
    Object.assign(values, p);
  }
  if (inputsPath !== undefined) {
    const j = readJson(inputsPath, "--inputs");
    if (!j || typeof j !== "object" || Array.isArray(j)) throw new CompileError(`--inputs: ${inputsPath} must hold a JSON object of slot -> value`);
    Object.assign(values, j);
  }
  for (const s of sets) {
    const eq = s.indexOf("=");
    if (eq <= 0) throw new CompileError(`--set expects slot=value, got "${s}"`);
    values[s.slice(0, eq)] = s.slice(eq + 1);
  }

  const out = compile(entry, g, values);
  const text = format(out) + "\n";
  if (outPath !== undefined) {
    mkdirSync(dirname(outPath), { recursive: true });
    writeFileSync(outPath, text);
  } else process.stdout.write(text);

  const touched = Object.keys(values).map((k) => {
    const s = entry.slots[k];
    return same(out[s.node].inputs[s.input], g[s.node].inputs[s.input]) ? `${k} (= default)` : k;
  });
  console.error(
    `comfy_compile: ${name} · ${Object.keys(out).length} nodes · ` +
      (touched.length ? `${touched.length} slot(s) set: ${touched.join(", ")}` : "all defaults") +
      (presetName ? ` · preset ${presetName}` : "") +
      ` → ${outPath ?? "stdout"}`,
  );
}

if (import.meta.main) {
  try {
    main(process.argv.slice(2));
  } catch (e) {
    if ((e as { code?: string }).code === "EPIPE") process.exit(0); // stdout closed early (e.g. | head)
    if (!(e instanceof CompileError)) throw e;
    console.error(`comfy_compile: error: ${e.message}`);
    process.exit(1);
  }
}
