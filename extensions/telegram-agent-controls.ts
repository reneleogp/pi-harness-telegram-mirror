import type { ModelThinkingLevel } from "@earendil-works/pi-ai";
import type {
  ExtensionAPI,
  ExtensionContext,
} from "@earendil-works/pi-coding-agent";

type AgentModel = NonNullable<ExtensionContext["model"]>;
type ControlCommand = "agent_info" | "change_model" | "change_thinking";

export type AgentControlRequest = {
  command: ControlCommand;
  provider?: unknown;
  model?: unknown;
  level?: unknown;
};

export type AgentControlChoice = {
  label: string;
  provider?: string;
  model?: string;
  level?: string;
};

export type AgentControlResult = {
  text: string;
  menu?: "model" | "thinking";
  choices?: AgentControlChoice[];
  target?: { provider: string; model: string };
};

type SupportedThinkingLevels = (model: AgentModel) => ModelThinkingLevel[];

const MAX_MODEL_CHOICES = 2000;
const VALID_THINKING_LEVELS = new Set<ModelThinkingLevel>([
  "off",
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
]);
const UNAVAILABLE = "Agent controls unavailable: no owning Pi session is connected.";
const BUSY = "Pi is busy. Try again after the current response and queued messages finish.";

function sameModel(left: AgentModel | undefined, right: AgentModel | undefined): boolean {
  return Boolean(left && right && left.provider === right.provider && left.id === right.id);
}

function formatInteger(value: number): string {
  return Math.floor(value).toLocaleString("en-US");
}

function safeThinkingLevel(pi: ExtensionAPI): string | undefined {
  try {
    const level = pi.getThinkingLevel();
    return VALID_THINKING_LEVELS.has(level) ? level : undefined;
  } catch {
    return undefined;
  }
}

function providerLabel(ctx: ExtensionContext, provider: string): string {
  try {
    const display = ctx.modelRegistry.getProviderDisplayName(provider).trim();
    return display && display !== provider ? `${display} (${provider})` : provider;
  } catch {
    return provider;
  }
}

function formatContext(ctx: ExtensionContext, model: AgentModel | undefined): string {
  let usage: ReturnType<ExtensionContext["getContextUsage"]>;
  try {
    usage = ctx.getContextUsage();
  } catch {
    usage = undefined;
  }

  const reportedWindow = usage?.contextWindow;
  const modelWindow = model?.contextWindow;
  const contextWindow = typeof reportedWindow === "number" && Number.isFinite(reportedWindow) && reportedWindow > 0
    ? reportedWindow
    : typeof modelWindow === "number" && Number.isFinite(modelWindow) && modelWindow > 0
      ? modelWindow
      : undefined;
  const tokens = usage?.tokens;
  const measurableTokens = typeof tokens === "number" && Number.isFinite(tokens) && tokens >= 0
    ? tokens
    : undefined;

  if (measurableTokens !== undefined && contextWindow !== undefined) {
    const percent = (measurableTokens / contextWindow) * 100;
    return `Context: ${formatInteger(measurableTokens)} / ${formatInteger(contextWindow)} tokens (${percent.toFixed(1)}%)`;
  }
  if (contextWindow !== undefined) {
    return `Context: unknown used / ${formatInteger(contextWindow)} tokens`;
  }
  if (measurableTokens !== undefined) {
    return `Context: ${formatInteger(measurableTokens)} used / unknown capacity`;
  }
  return "Context: unavailable";
}

function availableModels(ctx: ExtensionContext): AgentModel[] {
  const available = ctx.modelRegistry.getAvailable();
  const allowed = ctx.scopedModels.length > 0
    ? new Set(ctx.scopedModels.map(({ model }) => `${model.provider}\0${model.id}`))
    : undefined;
  const seen = new Set<string>();
  const result: AgentModel[] = [];
  for (const model of available) {
    const key = `${model.provider}\0${model.id}`;
    if (allowed && !allowed.has(key)) continue;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(model);
  }
  return result;
}

function modelDescription(model: AgentModel | undefined): string {
  return model ? `${model.provider}/${model.id}` : "unavailable";
}

export function createAgentControls(
  pi: ExtensionAPI,
  getSupportedThinkingLevels: SupportedThinkingLevels,
) {
  let activeContext: ExtensionContext | null = null;
  let currentModel: AgentModel | undefined;
  let sessionGeneration = 0;

  function start(ctx: ExtensionContext): void {
    sessionGeneration += 1;
    activeContext = ctx;
    currentModel = ctx.model;
  }

  function stop(): void {
    sessionGeneration += 1;
    activeContext = null;
    currentModel = undefined;
  }

  function modelSelected(model: AgentModel, ctx: ExtensionContext): void {
    if (!activeContext) return;
    activeContext = ctx;
    currentModel = model;
  }

  async function handle(request: AgentControlRequest): Promise<AgentControlResult> {
    const ctx = activeContext;
    const generation = sessionGeneration;
    if (!ctx) return { text: UNAVAILABLE };

    if (request.command === "agent_info") {
      const thinking = safeThinkingLevel(pi);
      return {
        text: [
          "Agent Info",
          `Model: ${currentModel?.id ?? "unavailable"}`,
          `Provider: ${currentModel ? providerLabel(ctx, currentModel.provider) : "unavailable"}`,
          `Thinking: ${thinking ?? "unavailable"}`,
          formatContext(ctx, currentModel),
        ].join("\n"),
      };
    }

    if (!ctx.isIdle()) return { text: BUSY };

    if (request.command === "change_model") {
      const hasSelection = request.provider !== undefined || request.model !== undefined;
      if (!hasSelection) {
        let models: AgentModel[];
        try {
          models = availableModels(ctx);
        } catch {
          return { text: "Available models could not be read from Pi." };
        }
        if (models.length === 0) {
          return { text: `Change Model\nCurrent: ${modelDescription(currentModel)}\nNo authenticated models are available.` };
        }
        if (models.length > MAX_MODEL_CHOICES) {
          return { text: "Pi reported too many models to show safely in Telegram." };
        }
        return {
          text: `Change Model\nCurrent: ${modelDescription(currentModel)}\nChoose an available model:`,
          menu: "model",
          choices: models.map((model) => ({
            label: `${model.provider}/${model.id}`,
            provider: model.provider,
            model: model.id,
          })),
        };
      }

      if (typeof request.provider !== "string" || typeof request.model !== "string") {
        return { text: "Invalid model choice. Run /change_model again." };
      }
      let target: AgentModel | undefined;
      try {
        target = availableModels(ctx).find(
          (model) => model.provider === request.provider && model.id === request.model,
        );
      } catch {
        return { text: "Available models could not be read from Pi." };
      }
      if (!target) return { text: "That model is no longer available. Run /change_model again." };
      if (sessionGeneration !== generation || !activeContext || !ctx.isIdle()) return { text: BUSY };

      let changed: boolean;
      try {
        changed = await pi.setModel(target);
      } catch {
        return { text: "Pi could not apply that model. The current model was not confirmed." };
      }
      if (!changed) {
        return { text: "Pi could not use that model because its provider is not authenticated." };
      }
      if (sessionGeneration !== generation || !activeContext) {
        return { text: "The Pi session changed before the model result could be verified." };
      }
      if (!sameModel(currentModel, target)) {
        return { text: "Pi accepted the model change, but the resulting model could not be verified." };
      }
      const actualThinking = safeThinkingLevel(pi);
      return {
        text: [
          "Model changed",
          `Model: ${modelDescription(currentModel)}`,
          `Thinking: ${actualThinking ?? "unavailable (readback failed)"}`,
        ].join("\n"),
      };
    }

    if (request.command === "change_thinking") {
      if (!currentModel) return { text: "Thinking level unavailable: no model is selected." };
      const expectedProvider = request.provider;
      const expectedModel = request.model;
      const hasSelection = request.level !== undefined || expectedProvider !== undefined || expectedModel !== undefined;
      let supported: ModelThinkingLevel[];
      try {
        supported = getSupportedThinkingLevels(currentModel).filter((level) => VALID_THINKING_LEVELS.has(level));
      } catch {
        return { text: "Supported thinking levels could not be read from Pi." };
      }
      if (!hasSelection) {
        if (supported.length === 0) {
          return { text: "Pi reported no supported thinking levels for the current model." };
        }
        const current = safeThinkingLevel(pi);
        return {
          text: `Change Thinking Level\nCurrent: ${current ?? "unavailable"}\nChoose a level for ${modelDescription(currentModel)}:`,
          menu: "thinking",
          target: { provider: currentModel.provider, model: currentModel.id },
          choices: supported.map((level) => ({ label: level, level })),
        };
      }
      if (typeof expectedProvider !== "string" || typeof expectedModel !== "string" ||
          expectedProvider !== currentModel.provider || expectedModel !== currentModel.id) {
        return { text: "The model changed after this menu opened. Run /change_thinking again." };
      }
      if (typeof request.level !== "string" || !VALID_THINKING_LEVELS.has(request.level as ModelThinkingLevel) ||
          !supported.includes(request.level as ModelThinkingLevel)) {
        return { text: "That thinking level is not supported by the current model." };
      }
      if (sessionGeneration !== generation || !activeContext || !ctx.isIdle()) return { text: BUSY };
      try {
        pi.setThinkingLevel(request.level as ModelThinkingLevel);
      } catch {
        return { text: "Pi could not apply that thinking level. The current level was not confirmed." };
      }
      if (sessionGeneration !== generation || !activeContext) {
        return { text: "The Pi session changed before the thinking result could be verified." };
      }
      const actual = safeThinkingLevel(pi);
      if (!actual) {
        return { text: "Pi accepted the thinking change, but the resulting level could not be verified." };
      }
      return {
        text: actual === request.level
          ? `Thinking level changed\nThinking: ${actual}`
          : `Thinking level changed\nRequested: ${request.level}\nActual: ${actual}`,
      };
    }

    return { text: "Unsupported agent control." };
  }

  return { start, stop, modelSelected, handle };
}
