import { spawn } from "node:child_process";

export type QuotaWindow = {
  label: string;
  remainingPercent: number;
  resetAt?: string;
};

export type ProviderQuota = {
  /** Provider-neutral so additional quota sources can be added later. */
  provider: string;
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
  const used = number(raw.usedPercent ?? raw.used_percent);
  if (used === undefined || used < 0 || used > 100) return undefined;
  const resetSeconds = number(raw.resetsAt ?? raw.reset_at ?? raw.resetAt);
  const resetAt = resetSeconds === undefined
    ? undefined
    : new Date(resetSeconds > 10_000_000_000 ? resetSeconds : resetSeconds * 1000).toISOString();
  const durationMins = number(raw.windowDurationMins);
  const label = durationMins === 300
    ? "5-hour"
    : durationMins === 10080
      ? "weekly"
      : fallback;
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
  child.on("close", () => finish(new Error("Codex quota unavailable")));
  child.stdin.on("error", () => finish(new Error("Codex quota unavailable")));
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
          child.stdin.write(`${JSON.stringify({ method: "initialized", params: {} })}\n`);
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

function formatReset(resetAt: string | undefined, now: Date): string {
  if (!resetAt) return "reset unavailable";
  const milliseconds = Date.parse(resetAt) - now.getTime();
  if (!Number.isFinite(milliseconds)) return "reset unavailable";
  if (milliseconds <= 0) return "resets now";
  const minutes = Math.floor(milliseconds / 60_000);
  if (minutes < 1) return "resets in under 1m";
  if (minutes < 60) return `resets in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) {
    const remainderHours = hours % 24;
    return remainderHours > 0 ? `resets in ${days}d ${remainderHours}h` : `resets in ${days}d`;
  }
  const remainderMinutes = minutes % 60;
  return remainderMinutes > 0 ? `resets in ${hours}h ${remainderMinutes}m` : `resets in ${hours}h`;
}

export function formatProviderQuota(quota: ProviderQuota | undefined, now = new Date()): string {
  if (!quota || quota.windows.length === 0) return "GPT quota unavailable.";
  const lines = ["GPT quota"];
  for (const window of quota.windows) {
    const label = window.label === "weekly" ? "Weekly" : window.label === "5-hour" ? "5-hour" : window.label;
    lines.push(`${label}: ${Math.round(window.remainingPercent)}% left · ${formatReset(window.resetAt, now)}`);
  }
  return lines.join("\n");
}
