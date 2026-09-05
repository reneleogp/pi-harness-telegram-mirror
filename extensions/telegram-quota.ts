import { spawn } from "node:child_process";

export type QuotaWindow = {
  label: string;
  remainingPercent: number;
  resetAt?: string;
};

export type ProviderQuota = {
  provider: "OpenAI Codex";
  windows: QuotaWindow[];
};

/** Narrow provider-neutral contract: future readers return this shape. */
export type QuotaReader = () => Promise<ProviderQuota>;

type RpcMessage = { id?: unknown; result?: unknown };

function number(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function windowFrom(value: unknown, fallback: string): QuotaWindow | undefined {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Record<string, unknown>;
  const used = number(raw.used_percent ?? raw.usedPercent);
  if (used === undefined || used < 0 || used > 100) return undefined;
  const seconds = number(raw.reset_at ?? raw.resetAt);
  const resetAt = seconds === undefined
    ? undefined
    : new Date(seconds > 10_000_000_000 ? seconds : seconds * 1000).toISOString();
  const duration = number(raw.limit_window_seconds ?? raw.windowDurationMins);
  const label = duration !== undefined && duration <= 8 * 60 * 60 ? "5-hour" : fallback;
  return { label, remainingPercent: 100 - used, resetAt };
}

/** Uses Codex's local authenticated app-server; it never reads credentials. */
export const readCodexQuota: QuotaReader = () => new Promise((resolve, reject) => {
  const child = spawn("codex", ["app-server"], { stdio: ["pipe", "pipe", "ignore"] });
  let buffer = "";
  let done = false;
  const timeout = setTimeout(() => finish(new Error("Codex quota unavailable")), 15000);
  const finish = (error?: Error, value?: ProviderQuota): void => {
    if (done) return;
    done = true;
    clearTimeout(timeout);
    child.kill();
    if (error) reject(error); else resolve(value!);
  };
  child.on("error", () => finish(new Error("Codex quota unavailable")));
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => {
    buffer += chunk;
    let newline = buffer.indexOf("\n");
    while (newline >= 0 && !done) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      try {
        const message = JSON.parse(line) as RpcMessage;
        if (message.id === 1) {
          child.stdin.write(`${JSON.stringify({ id: 2, method: "account/rateLimits/read", params: {} })}\n`);
        } else if (message.id === 2) {
          const result = (message.result ?? {}) as Record<string, unknown>;
          const limits = (result.rateLimits ?? result.rate_limits ?? result) as Record<string, unknown>;
          const windows = [
            windowFrom(limits.primary ?? limits.primary_window, "primary"),
            windowFrom(limits.secondary ?? limits.secondary_window, "weekly"),
          ].filter((window): window is QuotaWindow => Boolean(window));
          if (!windows.length) finish(new Error("Codex quota unavailable"));
          else finish(undefined, { provider: "OpenAI Codex", windows });
        }
      } catch { /* Ignore notifications and malformed app-server lines. */ }
      newline = buffer.indexOf("\n");
    }
  });
  child.stdin.write(`${JSON.stringify({ id: 1, method: "initialize", params: { clientInfo: { name: "pi-telegram", title: "Pi Telegram", version: "1.0.0" }, capabilities: {} } })}\n`);
});

export function formatProviderQuota(quota: ProviderQuota | undefined): string {
  if (!quota || quota.windows.length === 0) {
    return "Provider quota: unavailable (Codex account rate limits could not be read).";
  }
  const lines = ["Provider quota (not Pi session context):", `Provider: ${quota.provider}`];
  for (const window of quota.windows) {
    lines.push(`${window.label}: ${window.remainingPercent}% remaining; reset: ${window.resetAt ?? "unavailable"}`);
  }
  return lines.join("\n");
}
