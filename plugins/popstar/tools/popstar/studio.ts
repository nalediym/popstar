// popstar studio: start every tool server that exists, each on a free port, and serve one dark hub page with
// big cards (live URL + health) and the pipeline status, refreshed every 5 s. Ctrl-C kills every child's process group.
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { paint } from "./ansi.ts";
import type { Manifest } from "./project.ts";
import { run } from "./proc.ts";
import { computeStatus } from "./status.ts";

export type ToolDef = { key: string; name: string; file: string; blurb: string; stage: string };
export const TOOLS: ToolDef[] = [
  { key: "recorder", name: "Coach recorder", file: "recorder/server.ts", blurb: "Lyrics, beat HUD, move cards and pose warnings while you record the takes.", stage: "5 SHOOT" },
  { key: "looks", name: "Reference Master", file: "reference-master/server.ts", blurb: "Wardrobe, set, light and expression per scene; queue the renders for Comfy Cloud.", stage: "4 LOOKS" },
  { key: "cut", name: "Director's Cut", file: "cut/server.ts", blurb: "The edit on the song clock: transitions, captions, the stitch to outputs/final.mp4.", stage: "7 STITCH" },
];
/** 56101 is the recorder preview pinned in .claude/launch.json; never hand it out. */
export const RESERVED_PORTS = [56101];

export async function freePort(avoid: number[] = RESERVED_PORTS): Promise<number> {
  for (let i = 0; i < 25; i++) {
    const s = Bun.serve({ port: 0, hostname: "127.0.0.1", fetch: () => new Response("") });
    const port = s.port ?? 0;
    s.stop(true);
    if (port > 0 && !avoid.includes(port)) return port;
  }
  throw new Error("could not find a free port");
}

export type Running = {
  tool: ToolDef;
  port: number;
  child: ChildProcess;
  url: string | null;
  state: "starting" | "up" | "exited";
  exitCode: number | null;
  log: string[];
  ready: Promise<string | null>;
};

export const urlIn = (text: string): string | null => /https?:\/\/[^\s)'"<>]+/.exec(text)?.[0] ?? null;

/** Spawn `bun <tool.file>` with PORT in its own process group and resolve `ready` with the URL it prints. */
export function startTool(root: string, tool: ToolDef, port: number, readyTimeoutMs = 15_000): Running {
  const child = spawn(process.execPath, [join(root, tool.file)], {
    cwd: root,
    env: { ...process.env, PORT: String(port) },
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const r: Running = { tool, port, child, url: null, state: "starting", exitCode: null, log: [], ready: Promise.resolve(null) };
  r.ready = new Promise<string | null>((resolve) => {
    let done = false;
    const finish = (url: string | null) => {
      if (done) return;
      done = true;
      resolve(url);
    };
    const onData = (chunk: Buffer | string) => {
      const text = chunk.toString();
      for (const line of text.split("\n")) if (line.trim()) r.log.push(line.trimEnd());
      if (r.log.length > 200) r.log.splice(0, r.log.length - 200);
      const url = urlIn(text);
      if (url && !r.url) {
        r.url = url;
        r.state = "up";
        finish(url);
      }
    };
    child.stdout?.on("data", onData);
    child.stderr?.on("data", onData);
    child.on("exit", (code) => {
      r.state = "exited";
      r.exitCode = code;
      finish(r.url);
    });
    child.on("error", (e) => {
      r.log.push(`spawn error: ${e.message}`);
      r.state = "exited";
      finish(null);
    });
    const t = setTimeout(() => {
      if (!r.url && r.state === "starting") {
        r.url = `http://127.0.0.1:${port}/`;
        r.state = "up";
        r.log.push(`(no URL printed within ${readyTimeoutMs / 1000}s; assuming ${r.url})`);
      }
      finish(r.url);
    }, readyTimeoutMs);
    (t as { unref?: () => void }).unref?.();
  });
  return r;
}

/** SIGTERM the child's whole process group (it was spawned detached), falling back to the pid. */
export function killGroup(child: ChildProcess, signal: NodeJS.Signals = "SIGTERM"): void {
  if (!child.pid || child.exitCode !== null) return;
  try {
    process.kill(-child.pid, signal);
  } catch {
    try {
      child.kill(signal);
    } catch {
      /* already gone */
    }
  }
}

export type Health = "up" | "down" | "unknown";
export async function health(url: string | null, timeoutMs = 1500): Promise<Health> {
  if (!url) return "unknown";
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), redirect: "manual" });
    return r.status < 500 ? "up" : "down";
  } catch {
    return "down";
  }
}

/** Is something listening on 127.0.0.1:<port> (or *:<port>)? Uses lsof, never a guess. */
export function portListening(port: number): boolean {
  const r = run(["lsof", "-nP", `-iTCP:${port}`, "-sTCP:LISTEN"], { timeoutMs: 5000 });
  return r.ok && r.stdout.trim().split("\n").length > 1;
}

export function openUrl(url: string): boolean {
  return run(["open", url], { timeoutMs: 10_000 }).ok;
}

type Card = Running | { tool: ToolDef; missing: true };

export async function studio(root: string, m: Manifest, o: { open: boolean; log?: (s: string) => void } = { open: true }): Promise<number> {
  const log = o.log ?? console.log;
  const p = paint;
  log(p.amber(`\n  POP-STAR STUDIO · ${m.slug}\n`));
  const cards: Card[] = [];
  for (const t of TOOLS) {
    if (!existsSync(join(root, t.file))) {
      cards.push({ tool: t, missing: true });
      log(`  ${p.dim("·")} ${t.name.padEnd(17)} ${p.dim(`not built yet (${t.file})`)}`);
      continue;
    }
    const port = await freePort();
    const r = startTool(root, t, port);
    cards.push(r);
    log(`  ${p.amber("▶")} ${t.name.padEnd(17)} ${p.dim(`starting ${t.file} on port ${port}…`)}`);
  }
  await Promise.all(cards.map((c) => ("missing" in c ? null : c.ready)));
  for (const c of cards) {
    if ("missing" in c) continue;
    if (c.state === "exited") log(`  ${p.red("✗")} ${c.tool.name.padEnd(17)} exited (${c.exitCode}): ${c.log.slice(-3).join(" | ")}`);
    else log(`  ${p.green("✓")} ${c.tool.name.padEnd(17)} ${c.url}`);
  }

  const hubPort = await freePort();
  const stateJson = async () => ({
    project: { slug: m.slug, title: m.title },
    at: new Date().toISOString(),
    tools: await Promise.all(
      cards.map(async (c) =>
        "missing" in c
          ? { key: c.tool.key, name: c.tool.name, stage: c.tool.stage, blurb: c.tool.blurb, file: c.tool.file, built: false, url: null, port: null, pid: null, state: "missing", health: "unknown" as Health, log: [] as string[] }
          : {
              key: c.tool.key, name: c.tool.name, stage: c.tool.stage, blurb: c.tool.blurb, file: c.tool.file, built: true,
              url: c.url, port: c.port, pid: c.child.pid ?? null, state: c.state,
              health: c.state === "exited" ? ("down" as Health) : await health(c.url),
              log: c.log.slice(-5),
            },
      ),
    ),
    status: computeStatus(root, m.slug, m),
  });
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: hubPort,
    async fetch(req) {
      const url = new URL(req.url);
      if (url.pathname === "/api/state") return Response.json(await stateJson(), { headers: { "cache-control": "no-store" } });
      if (url.pathname === "/") return new Response(hubHtml(), { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
      return new Response("not found", { status: 404 });
    },
  });
  const hubUrl = `http://127.0.0.1:${server.port}/`;
  log(`\n  ${p.amber("HUB")} ${hubUrl}  ${p.dim("(Ctrl-C stops everything)")}\n`);
  if (o.open) openUrl(hubUrl);

  return new Promise<number>((resolve) => {
    let closing = false;
    const shutdown = (why: string) => {
      if (closing) return;
      closing = true;
      log(`\n  ${p.dim(`${why}: stopping ${cards.filter((c) => !("missing" in c)).length} server(s)…`)}`);
      for (const c of cards) if (!("missing" in c)) killGroup(c.child, "SIGTERM");
      setTimeout(() => {
        for (const c of cards) if (!("missing" in c)) killGroup(c.child, "SIGKILL");
        server.stop(true);
        resolve(0);
      }, 800);
    };
    process.on("SIGINT", () => shutdown("SIGINT"));
    process.on("SIGTERM", () => shutdown("SIGTERM"));
    process.on("exit", () => {
      for (const c of cards) if (!("missing" in c)) killGroup(c.child, "SIGKILL");
    });
  });
}

/** The hub page. Static shell; the script pulls /api/state every 5 s. */
export function hubHtml(): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>popstar studio</title>
<style>
:root{--bg:#0b0b0d;--panel:#141417;--line:#232329;--ink:#e8e4dc;--mute:#8a867f;--amber:#ffb347;--amber2:#ff9f43;--brown:#7a4a2a;--ok:#5fd38d;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 ui-monospace,Menlo,SFMono-Regular,monospace}
main{max-width:1100px;margin:0 auto;padding:32px 20px 60px}
h1{font-size:14px;letter-spacing:.2em;text-transform:uppercase;color:var(--amber);margin:0 0 4px}
.sub{color:var(--mute);margin:0 0 28px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:36px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;display:flex;flex-direction:column;gap:8px;min-height:170px;position:relative}
.card.built{border-color:#3a2a1a}.card.built:hover{border-color:var(--amber2)}
.card .stage{font-size:11px;letter-spacing:.15em;color:var(--amber2);text-transform:uppercase}
.card .name{font-size:22px;font-weight:600}
.card .blurb{color:var(--mute);flex:1}
.card a{color:var(--amber);text-decoration:none;font-size:15px;word-break:break-all}
.card a:hover{text-decoration:underline}
.card .missing{color:var(--mute);font-style:italic}
.dot{position:absolute;top:18px;right:18px;width:10px;height:10px;border-radius:50%;background:#444}
.dot.up{background:var(--ok);box-shadow:0 0 10px var(--ok)}.dot.down{background:var(--bad)}.dot.unknown{background:#444}
.log{font-size:11px;color:#5e5a53;white-space:pre-wrap;max-height:60px;overflow:hidden}
h2{font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--mute);margin:0 0 12px}
.stages{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 20px}
.stage-row{display:grid;grid-template-columns:20px 24px 90px 1fr;gap:8px;padding:5px 0;border-bottom:1px solid var(--line)}
.stage-row:last-child{border-bottom:0}
.stage-row .mark{text-align:center}.ok{color:var(--ok)}.next{color:var(--amber)}.todo{color:#444}
.stage-row .detail.dim{color:var(--mute)}
.nextline{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}
.nextline b{color:var(--amber)}
.foot{color:#5e5a53;font-size:12px;margin-top:20px}
#err{color:var(--bad)}
</style></head><body><main>
<h1>popstar studio</h1><p class="sub" id="sub">loading…</p>
<section class="cards" id="cards"></section>
<h2>pipeline</h2><section class="stages" id="stages"></section>
<p class="foot">refreshes every 5 s · <span id="at"></span> <span id="err"></span></p>
</main>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function render(s){
  document.getElementById('sub').textContent = s.project.title + ' · ' + s.project.slug;
  document.getElementById('cards').innerHTML = s.tools.map(function(t){
    var body = t.built
      ? (t.url ? '<a href="'+esc(t.url)+'" target="_blank" rel="noopener">'+esc(t.url)+'</a>' : '<span class="missing">starting…</span>')
        + '<div class="log">'+esc((t.log||[]).slice(-2).join('\\n'))+'</div>'
      : '<span class="missing">not built yet · '+esc(t.file)+'</span>';
    var h = t.built ? (t.state==='exited' ? 'down' : t.health) : 'unknown';
    return '<article class="card '+(t.built?'built':'')+'"><span class="dot '+h+'" title="'+h+'"></span>'
      + '<div class="stage">'+esc(t.stage)+'</div><div class="name">'+esc(t.name)+'</div>'
      + '<div class="blurb">'+esc(t.blurb)+'</div>'+body+'</article>';
  }).join('');
  var st = s.status; var next = st.next;
  document.getElementById('stages').innerHTML = st.stages.map(function(x){
    var cls = x.done ? 'ok' : (next && x.n===next.n ? 'next' : 'todo');
    var mark = x.done ? '✓' : (next && x.n===next.n ? '▶' : '·');
    return '<div class="stage-row"><span class="mark '+cls+'">'+mark+'</span><span>'+x.n+'</span><span>'+esc(x.name)+'</span><span class="detail '+(x.done?'':'dim')+'">'+esc(x.detail)+'</span></div>';
  }).join('') + '<div class="nextline">' + (next ? '<b>NEXT:</b> '+esc(next.next) : '<b>DONE.</b> Watch outputs/final.mp4') + '</div>';
  document.getElementById('at').textContent = new Date(s.at).toLocaleTimeString();
  document.getElementById('err').textContent = '';
}
async function refresh(){
  try { var r = await fetch('/api/state', {cache:'no-store'}); render(await r.json()); }
  catch (e) { document.getElementById('err').textContent = 'hub unreachable: ' + e; }
}
refresh(); setInterval(refresh, 5000);
</script></body></html>
`;
}
