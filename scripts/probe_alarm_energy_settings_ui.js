#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const WebSocket = require("ws");

const BASE = "https://www.alarm.com";
const ENERGY_URL = `${BASE}/web/Energy/EnergyConsumption.aspx`;
const ROOT = path.resolve(__dirname, "..");
const RUNTIME_ROOT = path.join(os.homedir(), "Library/Application Support/SmartHomeMonitor");
const DATA_DIR = path.join(ROOT, "data");

function runningFromRuntimeRoot() {
  return path.resolve(ROOT) === path.resolve(RUNTIME_ROOT);
}

function parseArgs(argv) {
  const args = new Set(argv.slice(2));
  const known = new Set(["--force-outside-runtime"]);
  for (const arg of args) {
    if (!known.has(arg)) {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return { forceOutsideRuntime: args.has("--force-outside-runtime") };
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
  while (Date.now() < deadline) {
    const value = await evalJson(cdp, expression).catch(() => false);
    if (value) return value;
    await delay(500);
  }
  throw new Error(`timed out waiting for expression: ${expression}`);
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

async function main() {
  const args = parseArgs(process.argv);
  if (!args.forceOutsideRuntime && !runningFromRuntimeRoot()) {
    throw new Error(
      `refusing to automate Alarm.com settings outside the runtime root: ${ROOT} != ${RUNTIME_ROOT}`
    );
  }
  const alarm = require(findAlarmModule());
  const config = loadAlarmConfig();
  const auth = await alarm.login(config.username, config.password, config.mfaCookie);

  const port = 9423 + Math.floor(Math.random() * 400);
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

    const pageInfo = await fetchJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(ENERGY_URL)}`, { method: "PUT" });
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

    await cdp.send("Page.navigate", { url: ENERGY_URL });
    await waitFor(cdp, `document.body && /Daily Status|Weekly Status|Usage Alerts|Energy Clamp/i.test(document.body.innerText)`, 45000);

    const payload = await evalJson(cdp, `(() => {
      const labelFor = (el) => {
        const labels = [];
        if (el.id) {
          const label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (label) labels.push(label.innerText.trim());
        }
        let parent = el.parentElement;
        for (let i = 0; parent && i < 4; i += 1, parent = parent.parentElement) {
          const text = parent.innerText && parent.innerText.trim();
          if (text && text.length < 240) labels.push(text);
        }
        return [...new Set(labels)].slice(0, 4);
      };
      return {
        url: location.href,
        title: document.title,
        textSample: document.body.innerText.slice(0, 2500),
        forms: [...document.forms].map((form, index) => ({
          index,
          id: form.id || '',
          name: form.name || '',
          action: form.action || '',
          method: form.method || ''
        })),
        inputs: [...document.querySelectorAll('input,select,button,a')]
          .map((el, index) => ({
            index,
            tag: el.tagName,
            type: el.type || '',
            name: el.name || '',
            id: el.id || '',
            value: String(el.value || '').slice(0, 120),
            checked: Boolean(el.checked),
            disabled: Boolean(el.disabled),
            text: (el.innerText || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 160),
            labels: labelFor(el)
          }))
          .filter((item) => /daily|weekly|usage|report|recipient|save|submit|contact|email|sms|goal/i.test(JSON.stringify(item)))
      };
    })()`);

    const out = redactArtifact({ ok: true, generatedAt: new Date().toISOString(), payload });
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(path.join(DATA_DIR, "alarm_energy_settings_ui_probe.json"), JSON.stringify(out, null, 2) + "\n");
    console.log(JSON.stringify(redactArtifact({ ok: true, url: payload.url, title: payload.title, inputs: payload.inputs.slice(0, 80) }), null, 2));
  } finally {
    if (cdp) cdp.close();
    chrome.kill("SIGTERM");
    cleanupProfile(profile);
  }
}

main().catch((error) => {
  console.error(redactText(error.stack || error.message));
  process.exitCode = 1;
});
