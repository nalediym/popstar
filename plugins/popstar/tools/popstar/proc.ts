// Small process helpers: run a command and capture it (with a timeout), or run it attached to the terminal.

export type RunResult = {
  ok: boolean;
  code: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  error?: string; // spawn failure (command not found, ...)
};

export type RunOptions = { cwd?: string; timeoutMs?: number; env?: Record<string, string | undefined> };

/** Run `cmd` and capture both streams. Never throws: a missing binary comes back as ok=false with `error`. */
export function run(cmd: string[], opts: RunOptions = {}): RunResult {
  try {
    const env: Record<string, string> = {};
    for (const [k, v] of Object.entries({ ...process.env, ...(opts.env ?? {}) })) if (v !== undefined) env[k] = v;
    const p = Bun.spawnSync(cmd, {
      cwd: opts.cwd,
      env,
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
      timeout: opts.timeoutMs,
    });
    const timedOut = (p as { exitedDueToTimeout?: boolean }).exitedDueToTimeout === true;
    return {
      ok: p.exitCode === 0 && !timedOut,
      code: p.exitCode,
      stdout: p.stdout.toString(),
      stderr: p.stderr.toString(),
      timedOut,
    };
  } catch (e) {
    return { ok: false, code: null, stdout: "", stderr: "", timedOut: false, error: (e as Error).message };
  }
}

/** Run `cmd` with the terminal attached (the user sees its output live). Returns the exit code. */
export function runInherit(cmd: string[], opts: RunOptions = {}): number {
  try {
    const env: Record<string, string> = {};
    for (const [k, v] of Object.entries({ ...process.env, ...(opts.env ?? {}) })) if (v !== undefined) env[k] = v;
    const p = Bun.spawnSync(cmd, { cwd: opts.cwd, env, stdin: "inherit", stdout: "inherit", stderr: "inherit" });
    return p.exitCode ?? 1;
  } catch (e) {
    console.error(`cannot run ${cmd[0]}: ${(e as Error).message}`);
    return 127;
  }
}

/** The command as one line, quoting what needs it (for printing a fix or a receipt). */
export const shellLine = (cmd: string[]): string =>
  cmd.map((a) => (/^[\w./:=@%+,-]+$/.test(a) ? a : `'${a.replace(/'/g, `'\\''`)}'`)).join(" ");
