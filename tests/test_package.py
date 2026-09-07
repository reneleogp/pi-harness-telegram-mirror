from __future__ import annotations
import json, os, plistlib, shlex, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[1]
OWNER=ROOT/'bin/pi-telegram-owner.py'
def run(*args, env): return subprocess.run([sys.executable,str(OWNER),*args],env=env,capture_output=True,text=True)
def test_manifest():
 d=json.loads((ROOT/'package.json').read_text()); assert 'pi-package' in d['keywords']; assert d['license']=='MIT'; assert d['pi']['extensions']==['./extensions/telegram-mirror.ts']; assert (ROOT/'LICENSE').is_file(); assert '@earendil-works/pi-coding-agent' in d['peerDependencies']; assert '@earendil-works/pi-ai' in d['peerDependencies']

def test_claude_points_to_project_guidance():
 assert (ROOT/'AGENTS.md').is_file()
 assert (ROOT/'CLAUDE.md').is_file()
 assert (ROOT/'CLAUDE.md').read_text().splitlines()[-1] == '@AGENTS.md'
def test_exact_root_and_contention():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; root=Path(t)/'root'; root.mkdir(); sub=root/'sub'; sub.mkdir(); e={**os.environ,'PI_TELEGRAM_DIR':str(h)}
  assert run('allow-root',str(root),env=e).returncode==0
  assert run('claim',str(sub),env=e).returncode != 0
  assert run('claim',str(root),env=e).returncode != 0
  assert not (h/'session.json').exists()
def parse_systemd_unit(text):
 sections = {}; section = None
 for line in text.splitlines():
  if not line: continue
  if line.startswith('[') and line.endswith(']'):
   section = line[1:-1]; sections[section] = {}; continue
  assert section is not None and '=' in line
  key, value = line.split('=', 1)
  parsed = shlex.split(value, comments=False)
  sections[section].setdefault(key, []).append([item.replace('%%', '%') for item in parsed])
 return sections

def test_permissions_and_no_secret_in_unit():
 with tempfile.TemporaryDirectory() as t:
  h=Path(t)/'h'; e={**os.environ,'PI_TELEGRAM_DIR':str(h)}; (h/'env').parent.mkdir(); (h/'env').write_text('TELEGRAM_BOT_TOKEN=secret\n'); (h/'env').chmod(0o600)
  result=subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'service-unit'],env=e,capture_output=True,text=True)
  assert result.returncode == 0
  assert 'secret' not in result.stdout and 'TELEGRAM_BOT_TOKEN' not in result.stdout
  if sys.platform == 'darwin':
   unit = plistlib.loads(result.stdout.encode())
   assert unit['EnvironmentVariables']['PI_TELEGRAM_DIR'] == str(h)
  elif sys.platform == 'linux':
   unit = parse_systemd_unit(result.stdout)
   assert unit['Service']['Type'] == [['simple']]
   environments = unit['Service']['Environment']
   assert [f'PI_TELEGRAM_DIR={h}'] in environments
   assert [f"PATH={Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"] in environments
   assert unit['Install']['WantedBy'] == [['default.target']]
  assert (h/'env').stat().st_mode & 0o077 == 0
def test_help(): assert subprocess.run([sys.executable,str(ROOT/'bin/pi-telegram.py'),'--help'],capture_output=True).returncode==0


def test_telegram_footer_uses_theme_check_and_honest_states():
    script = r'''
import { formatTelegramFooter } from "./extensions/telegram-footer.ts";
const calls = [];
const theme = { fg: (color, text) => { calls.push([color, text]); return `<${color}>${text}</${color}>`; } };
if (formatTelegramFooter(theme, true, true) !== "telegram: <success>✓</success>") throw new Error("enabled footer");
if (formatTelegramFooter(theme, true, false) !== "telegram: ✗") throw new Error("disabled footer");
if (formatTelegramFooter(theme, false, true) !== "telegram: unavailable") throw new Error("disconnected footer");
if (calls.length !== 1 || calls[0][0] !== "success" || calls[0][1] !== "✓") throw new Error("theme was not used");
'''
    result = subprocess.run(
        ['node', '--experimental-strip-types', '--input-type=module', '-'],
        cwd=ROOT, input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_agent_controls_use_live_pi_apis_without_submitting_prompts():
    script = r'''
import { createAgentControls } from "./extensions/telegram-agent-controls.ts";

const models = [
  { provider: "alpha", id: "one", name: "One", reasoning: true, contextWindow: 128000 },
  { provider: "beta", id: "two", name: "Two", reasoning: true, contextWindow: 64000 },
  { provider: "beta", id: "plain", name: "Plain", reasoning: false, contextWindow: 32000 },
];
let idle = true;
let thinking = "high";
let thinkingReadbackFails = false;
let contextUsage = { tokens: 32000, contextWindow: 128000, percent: 25 };
let model = models[0];
let setModelMode = "normal";
let releaseModel;
let promptCalls = 0;
let supportedCalls = 0;
let controls;
const pi = {
  getThinkingLevel: () => {
    if (thinkingReadbackFails) throw new Error("readback failed");
    return thinking;
  },
  setThinkingLevel: (level) => { thinking = level === "high" ? "low" : level; },
  setModel: async (next) => {
    if (setModelMode === "pending") await new Promise((resolve) => { releaseModel = resolve; });
    if (setModelMode !== "no-readback") {
      model = next;
      thinking = next.reasoning ? "medium" : "off";
      controls.modelSelected(next, { ...ctx, model: next });
    }
    return true;
  },
  sendUserMessage: () => { promptCalls += 1; },
  sendMessage: () => { promptCalls += 1; },
  prompt: () => { promptCalls += 1; },
};
const registry = {
  getAvailable: () => models,
  getProviderDisplayName: (provider) => provider === "alpha" ? "Alpha Cloud" : provider,
  complete: () => { promptCalls += 1; throw new Error("inference called"); },
};
const ctx = {
  modelRegistry: registry,
  model,
  scopedModels: [],
  isIdle: () => idle,
  getContextUsage: () => contextUsage,
};
const supported = (candidate) => {
  supportedCalls += 1;
  return candidate.reasoning ? ["off", "low", "high"] : ["off"];
};
controls = createAgentControls(pi, supported);

const disconnected = await controls.handle({ command: "agent_info" });
if (!disconnected.text.includes("no owning Pi session")) throw new Error(disconnected.text);
controls.start(ctx);
const info = await controls.handle({ command: "agent_info" });
if (info.text !== "Agent Info\nModel: one\nProvider: Alpha Cloud (alpha)\nThinking: high\nContext: 32,000 / 128,000 tokens (25.0%)") throw new Error(info.text);
const picker = await controls.handle({ command: "change_model" });
if (picker.menu !== "model" || picker.choices.length !== 3 || picker.choices[1].model !== "two") throw new Error("model catalog");
ctx.scopedModels = [{ model: models[1] }];
controls.start(ctx);
const scopedPicker = await controls.handle({ command: "change_model" });
if (scopedPicker.choices.length !== 1 || scopedPicker.choices[0].model !== "two") throw new Error("model scope");
ctx.scopedModels = [];
controls.start(ctx);

idle = false;
const busy = await controls.handle({ command: "change_model" });
if (!busy.text.includes("Pi is busy")) throw new Error(busy.text);
idle = true;
const invalid = await controls.handle({ command: "change_model", provider: "beta", model: "missing" });
if (!invalid.text.includes("no longer available")) throw new Error(invalid.text);
const changed = await controls.handle({ command: "change_model", provider: "beta", model: "plain" });
if (changed.text !== "Model changed\nModel: beta/plain\nThinking: off") throw new Error(changed.text);

const thinkingPicker = await controls.handle({ command: "change_thinking" });
if (thinkingPicker.choices.length !== 1 || thinkingPicker.choices[0].level !== "off") throw new Error("thinking semantics");
const staleThinking = await controls.handle({ command: "change_thinking", provider: "alpha", model: "one", level: "off" });
if (!staleThinking.text.includes("model changed")) throw new Error(staleThinking.text);
const unsupported = await controls.handle({ command: "change_thinking", provider: "beta", model: "plain", level: "high" });
if (!unsupported.text.includes("not supported")) throw new Error(unsupported.text);

controls.modelSelected(models[0], ctx);
model = models[0];
thinking = "low";
const actual = await controls.handle({ command: "change_thinking", provider: "alpha", model: "one", level: "high" });
if (actual.text !== "Thinking level changed\nRequested: high\nActual: low") throw new Error(actual.text);
thinkingReadbackFails = true;
const thinkingUnreadable = await controls.handle({ command: "change_thinking", provider: "alpha", model: "one", level: "off" });
if (!thinkingUnreadable.text.includes("could not be verified")) throw new Error(thinkingUnreadable.text);
thinkingReadbackFails = false;

setModelMode = "no-readback";
const unreadable = await controls.handle({ command: "change_model", provider: "beta", model: "two" });
if (!unreadable.text.includes("could not be verified")) throw new Error(unreadable.text);
setModelMode = "pending";
const pending = controls.handle({ command: "change_model", provider: "beta", model: "two" });
await Promise.resolve();
controls.stop();
releaseModel();
const replaced = await pending;
if (!replaced.text.includes("session changed")) throw new Error(replaced.text);

controls.start(ctx);
contextUsage = { tokens: null, contextWindow: 128000, percent: null };
const unknown = await controls.handle({ command: "agent_info" });
if (!unknown.text.includes("Context: unknown used / 128,000 tokens")) throw new Error(unknown.text);
if (supportedCalls < 3) throw new Error("Pi thinking semantics were not consulted");
if (promptCalls !== 0) throw new Error(`agent inference path called ${promptCalls} times`);
'''
    result = subprocess.run(
        ['node', '--experimental-strip-types', '--input-type=module', '-'],
        cwd=ROOT, input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_extension_delivery_mode_starts_idle_turns_and_steers_when_busy():
    script = r'''
import { sendTelegramDelivery } from "./extensions/telegram-delivery.ts";
const calls = [];
const send = async (content, options) => calls.push({ content, options });
await sendTelegramDelivery(send, true, "text");
await sendTelegramDelivery(send, true, "caption", { data: "png", mime: "image/png" });
await sendTelegramDelivery(send, false, "text");
await sendTelegramDelivery(send, false, "caption", { data: "png", mime: "image/png" });
if (calls[0].options !== undefined || calls[1].options !== undefined) throw new Error("idle delivery did not start a turn");
if (calls[2].options?.deliverAs !== "steer" || calls[3].options?.deliverAs !== "steer") throw new Error("busy delivery did not steer");
if (calls[1].content[1].type !== "image" || calls[3].content[1].type !== "image") throw new Error("image delivery was lost");
'''
    result = subprocess.run(
        ['node', '--experimental-strip-types', '--input-type=module', '-'],
        cwd=ROOT, input=script, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
