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

function runningFromRuntimeRoot() {
  return path.resolve(ROOT) === path.resolve(RUNTIME_ROOT);
}

function parseArgs(argv) {
  const parsed = { clickText: "", forceOutsideRuntime: false };
  for (let index = 2; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--click") {
      parsed.clickText = argv[++index] || "";
    } else if (item === "--force-outside-runtime") {
      parsed.forceOutsideRuntime = true;
    } else {
      throw new Error(`unknown argument: ${item}`);
    }
  }
  return parsed;
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

async function waitFor(cdp, expression, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    const result = await cdp.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    last = result.result?.value;
    if (last) return last;
    await delay(500);
  }
  throw new Error(`timed out waiting for expression: ${expression}; last=${JSON.stringify(last)}`);
}

async function evalJson(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return result.result?.value;
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
  const clickText = args.clickText;
  const alarm = require(findAlarmModule());
  const config = loadAlarmConfig();
  const auth = await alarm.login(config.username, config.password, config.mfaCookie);

  const port = 9223 + Math.floor(Math.random() * 400);
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
    const cdp = new Cdp(pageInfo.webSocketDebuggerUrl);
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

    const before = await evalJson(cdp, `(() => {
      const text = document.body.innerText;
      const buttons = [...document.querySelectorAll('button,[role="button"],a')]
        .map((el, index) => ({
          index,
          tag: el.tagName,
          text: (el.innerText || el.getAttribute('aria-label') || el.title || '').trim(),
          aria: el.getAttribute('aria-label') || '',
          title: el.title || '',
          disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true'
        }))
        .filter((item) => item.text.includes('Sensor Left Open') || item.aria.includes('Sensor Left Open') || item.title.includes('Sensor Left Open'));
      return { url: location.href, title: document.title, hasRule: text.includes(${JSON.stringify(RULE_NAME)}), buttons };
    })()`);

    const clicked = await evalJson(cdp, `(() => {
      const controls = [...document.querySelectorAll('button,[role="button"],a')];
      const target = controls.find((el) => {
        const text = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim();
        return text === ${JSON.stringify(`${RULE_NAME} edit`)} || text.includes(${JSON.stringify(`${RULE_NAME} edit`)});
      });
      if (!target) return false;
      target.click();
      return true;
    })()`);
    if (!clicked) throw new Error("edit control was not found");
    await delay(3000);
    await waitFor(cdp, `location.href.includes('automation') && !document.body.innerText.includes('Rules | SST has loaded') || document.body.innerText.includes('Save')`, 30000).catch(() => null);

    let clickResult = null;
    if (clickText) {
      clickResult = await evalJson(cdp, `(() => {
        const wanted = ${JSON.stringify(clickText)};
        const controls = [...document.querySelectorAll('button,[role="button"],a,input,select,textarea')];
        const target = controls.find((el) => {
          const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim();
          return text === wanted || text.includes(wanted);
        });
        if (!target) return { clicked: false, wanted };
        target.scrollIntoView({ block: 'center', inline: 'center' });
        target.click();
        return {
          clicked: true,
          wanted,
          tag: target.tagName,
          text: (target.innerText || target.value || target.getAttribute('aria-label') || target.title || '').trim()
        };
      })()`);
      await delay(2500);
    }

    const after = await evalJson(cdp, `(() => {
      function labelFor(el) {
        const labels = [];
        if (el.id) {
          const direct = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
          if (direct) labels.push(direct.innerText.trim());
        }
        let parent = el.parentElement;
        for (let i = 0; parent && i < 4; i += 1, parent = parent.parentElement) {
          const text = parent.innerText && parent.innerText.trim();
          if (text && text.length < 260) labels.push(text);
        }
        return [...new Set(labels)].slice(0, 4);
      }
      const controls = [...document.querySelectorAll('input,select,textarea,button,[role="button"]')]
        .map((el, index) => ({
          index,
          tag: el.tagName,
          type: el.type || '',
          role: el.getAttribute('role') || '',
          text: (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 180),
          value: String(el.value || '').slice(0, 80),
          checked: Boolean(el.checked),
          disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
          aria: el.getAttribute('aria-label') || '',
          name: el.name || '',
          id: el.id || '',
          labels: labelFor(el)
        }))
        .filter((item) => /save|cancel|heat|cool|trim|minute|open|thermostat|rule|sensor|paused|enabled|name/i.test(JSON.stringify(item)));
      return {
        url: location.href,
        title: document.title,
        textSample: document.body.innerText.slice(0, 3500),
        controls,
      };
    })()`);

    cdp.close();
    const out = redactArtifact({
      ok: true,
      generatedAt: new Date().toISOString(),
      before,
      clickText,
      clickResult,
      after,
    });
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(path.join(DATA_DIR, "alarm_sensor_saver_ui_probe.json"), JSON.stringify(out, null, 2) + "\n");
    console.log(JSON.stringify(redactArtifact({
      ok: true,
      before,
      after: {
        url: after.url,
        title: after.title,
        textSample: after.textSample.slice(0, 1200),
        clickResult,
        controls: after.controls.slice(0, 40),
      },
    }), null, 2));
  } finally {
    chrome.kill("SIGTERM");
    cleanupProfile(profile);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
