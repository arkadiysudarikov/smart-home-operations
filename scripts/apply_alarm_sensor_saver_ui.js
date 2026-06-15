#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const WebSocket = require("ws");

const BASE = "https://www.alarm.com";
const RULE_NAME = "Sensor Left Open Energy Saver";
const ROOT = path.resolve(__dirname, "..");
const RUNTIME_ROOT = path.join(os.homedir(), "Library/Application Support/SmartHomeMonitor");
const DATA_DIR = path.join(ROOT, "data");
const TARGETS = [
  { section: "FOR THE DURATION: 1 MINUTE", option: "3 Minutes" },
  { section: "HEAT OFFSET: 0°F", option: "2°F" },
  { section: "COOL OFFSET: 0°F", option: "2°F" },
];

function runningFromRuntimeRoot() {
  return path.resolve(ROOT) === path.resolve(RUNTIME_ROOT);
}

function parseArgs(argv) {
  const args = new Set(argv.slice(2));
  const known = new Set(["--apply", "--dry-run", "--force-outside-runtime"]);
  for (const arg of args) {
    if (!known.has(arg)) {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return {
    dryRun: !args.has("--apply") || args.has("--dry-run"),
    forceOutsideRuntime: args.has("--force-outside-runtime"),
  };
}

function findAlarmModule() {
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.existsSync(local) ? fs.readdirSync(local).sort().reverse() : []) {
    const candidate = path.join(
      local,
      nodeDir,
      "lib/node_modules/homebridge-node-alarm-dot-com/node_modules/node-alarm-dot-com/dist/core.js"
    );
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error("node-alarm-dot-com dependency was not found under ~/.local");
}

function loadAlarmConfig() {
  const configPath = path.join(os.homedir(), ".homebridge", "config.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const platform = (config.platforms || []).find((item) => item.platform === "Alarmdotcom");
  if (!platform) throw new Error("Alarmdotcom platform is not configured in Homebridge");
  return platform;
}

function chromePath() {
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) throw new Error("Chrome executable was not found");
  return found;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function cleanupProfile(profile) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      fs.rmSync(profile, { recursive: true, force: true });
      return;
    } catch {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 200);
    }
  }
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${url} returned HTTP ${res.status}`);
  return res.json();
}

class Cdp {
  constructor(wsUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.ws = new WebSocket(wsUrl);
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.once("open", resolve);
      this.ws.once("error", reject);
    });
    this.ws.on("message", (raw) => {
      const msg = JSON.parse(String(raw));
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message));
        else resolve(msg.result);
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  close() {
    this.ws.close();
  }
}

async function evalJson(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return result.result?.value;
}

async function waitFor(cdp, expression, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await evalJson(cdp, expression).catch((error) => ({ error: error.message }));
    if (last) return last;
    await delay(500);
  }
  throw new Error(`timed out waiting for expression: ${expression}; last=${JSON.stringify(last)}`);
}

function parseCookies(cookieHeader) {
  return String(cookieHeader || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const index = part.indexOf("=");
      return index > 0 ? { name: part.slice(0, index), value: part.slice(index + 1) } : null;
    })
    .filter(Boolean);
}

function redactText(value) {
  return String(value)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[redacted-email]")
    .replace(/\b(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}\b/g, "[redacted-phone]")
    .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}\b/g, "[redacted-token]");
}

function redactArtifact(value) {
  if (typeof value === "string") return redactText(value);
  if (Array.isArray(value)) return value.map(redactArtifact);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactArtifact(item)]));
  }
  return value;
}

async function clickText(cdp, text) {
  const result = await evalJson(cdp, `(() => {
    const wanted = ${JSON.stringify(text)};
    const controls = [...document.querySelectorAll('button,[role="button"],a,input,select,textarea')];
    const target = controls.find((el) => {
      const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim();
      return label === wanted || label.includes(wanted);
    });
    if (!target) return { ok: false, wanted, body: document.body.innerText.slice(0, 1000) };
    target.scrollIntoView({ block: 'center', inline: 'center' });
    target.click();
    return { ok: true, wanted, text: (target.innerText || target.value || target.getAttribute('aria-label') || target.title || '').trim() };
  })()`);
  if (!result?.ok) throw new Error(`could not click ${text}: ${JSON.stringify(result)}`);
  await delay(1600);
  return result;
}

async function selectVisibleOption(cdp, text) {
  const result = await evalJson(cdp, `(() => {
    const wanted = ${JSON.stringify(text)};
    const select = document.querySelector('select');
    if (!select) return { ok: false, reason: 'no select', body: document.body.innerText.slice(0, 1000) };
    const option = [...select.options].find((item) => item.text.trim() === wanted);
    if (!option) return { ok: false, reason: 'option missing', wanted, options: [...select.options].map((item) => item.text.trim()) };
    select.value = option.value;
    option.selected = true;
    select.dispatchEvent(new Event('input', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, wanted, value: option.value };
  })()`);
  if (!result?.ok) throw new Error(`could not select ${text}: ${JSON.stringify(result)}`);
  await delay(500);
  return result;
}

async function snapshot(cdp) {
  return evalJson(cdp, `(() => ({
    url: location.href,
    title: document.title,
    text: document.body.innerText.slice(0, 3500),
    buttons: [...document.querySelectorAll('button,[role="button"],a')]
      .map((el) => (el.innerText || el.getAttribute('aria-label') || el.title || '').trim())
      .filter(Boolean)
      .slice(0, 120)
  }))()`);
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.forceOutsideRuntime && !runningFromRuntimeRoot()) {
    throw new Error(
      `refusing to automate Alarm.com settings outside the runtime root: ${ROOT} != ${RUNTIME_ROOT}`
    );
  }
  const dryRun = args.dryRun;
  const alarm = require(findAlarmModule());
  const config = loadAlarmConfig();
  const auth = await alarm.login(config.username, config.password, config.mfaCookie);

  const port = 9323 + Math.floor(Math.random() * 400);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "alarm-cdp-"));
  const chrome = spawn(chromePath(), [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    "about:blank",
  ], { stdio: "ignore" });

  let cdp;
  const actions = [];
  try {
    let version;
    for (let i = 0; i < 40; i += 1) {
      try {
        version = await fetchJson(`http://127.0.0.1:${port}/json/version`);
        break;
      } catch {
        await delay(250);
      }
    }
    if (!version) throw new Error("Chrome remote debugging endpoint did not start");

    const pageInfo = await fetchJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(`${BASE}/web/system/automation/rules`)}`, {
      method: "PUT",
    });
    cdp = new Cdp(pageInfo.webSocketDebuggerUrl);
    await cdp.open();
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");

    for (const cookie of parseCookies(auth.cookie)) {
      await cdp.send("Network.setCookie", {
        ...cookie,
        domain: ".alarm.com",
        path: "/",
        secure: true,
        httpOnly: false,
        sameSite: "None",
      });
    }

    await cdp.send("Page.navigate", { url: `${BASE}/web/system/automation/rules` });
    await waitFor(cdp, `document.body && document.body.innerText.includes(${JSON.stringify(RULE_NAME)})`, 45000);
    actions.push(await clickText(cdp, `${RULE_NAME} edit`));
    await waitFor(cdp, `document.body && document.body.innerText.includes('HEAT OFFSET') && document.body.innerText.includes('SAVE')`, 30000);

    for (const target of TARGETS) {
      actions.push(await clickText(cdp, target.section));
      await waitFor(cdp, `document.querySelector('select') && document.body.innerText.includes(${JSON.stringify(target.option)})`, 30000);
      actions.push(await selectVisibleOption(cdp, target.option));
      if (dryRun) {
        actions.push({ ok: true, dryRun: true, wouldClick: "DONE" });
        await clickText(cdp, "BACK");
      } else {
        actions.push(await clickText(cdp, "DONE"));
      }
      await waitFor(cdp, `document.body && document.body.innerText.includes('SAVE')`, 30000);
    }

    const beforeSave = await snapshot(cdp);
    if (!beforeSave.text.includes("FOR THE DURATION: 3 MINUTES") ||
        !beforeSave.text.includes("HEAT OFFSET: 2°F") ||
        !beforeSave.text.includes("COOL OFFSET: 2°F")) {
      throw new Error(`edited screen did not contain expected values before save: ${JSON.stringify(beforeSave)}`);
    }

    if (!dryRun) {
      actions.push(await clickText(cdp, "SAVE"));
      await delay(5000);
    }
    const afterSave = await snapshot(cdp);

    const out = redactArtifact({
      ok: true,
      dryRun,
      generatedAt: new Date().toISOString(),
      actions,
      beforeSave,
      afterSave,
    });
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(path.join(DATA_DIR, "alarm_sensor_saver_apply.json"), JSON.stringify(out, null, 2) + "\n");
    console.log(JSON.stringify(redactArtifact({
      ok: true,
      dryRun,
      beforeSave: {
        url: beforeSave.url,
        expected: {
          duration3: beforeSave.text.includes("FOR THE DURATION: 3 MINUTES"),
          heat2: beforeSave.text.includes("HEAT OFFSET: 2°F"),
          cool2: beforeSave.text.includes("COOL OFFSET: 2°F"),
        },
      },
      afterSave: {
        url: afterSave.url,
        textSample: afterSave.text.slice(0, 1200),
      },
    }), null, 2));
  } finally {
    if (cdp) cdp.close();
    chrome.kill("SIGTERM");
    cleanupProfile(profile);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
