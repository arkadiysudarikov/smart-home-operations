#!/usr/bin/env node
"use strict";

const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const RUNTIME_ROOT = path.join(os.homedir(), "Library/Application Support/SmartHomeMonitor");
const SOURCES_CONFIG = path.join(ROOT, "config/sources.json");
const HOMEBRIDGE_CONFIG = path.join(os.homedir(), ".homebridge/config.json");
const BACKUP_DIR = path.join(os.homedir(), ".homebridge/codex-backups");

function runningFromRuntimeRoot() {
  return path.resolve(ROOT) === path.resolve(RUNTIME_ROOT);
}

function loadJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function saveJson(filePath, payload) {
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 4) + "\n");
}

function normalizeEndpoint(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  return text.replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/:8443$/, "");
}

function sourceCandidates() {
  const config = fs.existsSync(SOURCES_CONFIG) ? loadJson(SOURCES_CONFIG) : {};
  const network = config.network || {};
  return [
    network.known_tahoma_office,
    ...(network.known_tahoma_office_candidates || []),
  ];
}

function homebridgeTahomaPlatforms(config) {
  return (config.platforms || []).filter((item) => item.platform === "Tahoma");
}

function officePlatform(config) {
  const platform = homebridgeTahomaPlatforms(config).find((item) => item.name === "Office");
  if (!platform) {
    throw new Error("Office TaHoma platform was not found in ~/.homebridge/config.json");
  }
  if (!platform.password) {
    throw new Error("Office TaHoma platform has no local API token");
  }
  return platform;
}

function candidatesFromConfig(config, office) {
  const known = new Set();
  for (const item of [
    office.user,
    ...sourceCandidates(),
    "192.168.0.90",
    "192.168.0.164",
    ...homebridgeTahomaPlatforms(config).map((item) => item.user),
  ]) {
    const normalized = normalizeEndpoint(item);
    if (normalized) known.add(normalized);
  }
  return [...known];
}

function getJson(ip, token) {
  const options = {
    hostname: ip,
    port: 8443,
    path: "/enduser-mobile-web/1/enduserAPI/setup/devices",
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
    rejectUnauthorized: false,
    timeout: 5000,
  };
  return new Promise((resolve) => {
    const req = https.request(options, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        let parsed = null;
        try {
          parsed = body ? JSON.parse(body) : null;
        } catch {
          parsed = null;
        }
        resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, body: parsed });
      });
    });
    req.on("timeout", () => {
      req.destroy(new Error("timeout"));
    });
    req.on("error", (error) => {
      resolve({ ok: false, status: 0, error: error.message || String(error) });
    });
    req.end();
  });
}

function summarizeDevices(payload) {
  const devices = Array.isArray(payload) ? payload : [];
  return {
    count: devices.length,
    labels: devices.map((item) => item.label).filter(Boolean),
    classes: devices.map((item) => item.definition?.uiClass).filter(Boolean),
  };
}

function looksLikeOffice(summary) {
  const labels = summary.labels.join(" ");
  return /Office/i.test(labels);
}

function timestamp() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..*$/, "").replace("T", "-");
}

function backupConfig(raw) {
  fs.mkdirSync(BACKUP_DIR, { recursive: true });
  const backup = path.join(BACKUP_DIR, `config-before-office-tahoma-ip-${timestamp()}.json`);
  fs.writeFileSync(backup, raw);
  return backup;
}

async function main() {
  const args = new Set(process.argv.slice(2));
  const shouldApply = args.has("--apply");
  const forceOutsideRuntime = args.has("--force-outside-runtime");
  if (shouldApply && !forceOutsideRuntime && !runningFromRuntimeRoot()) {
    console.error(JSON.stringify({
      ok: false,
      error: "refusing to update live Homebridge config outside the runtime root",
      sourceRoot: ROOT,
      runtimeRoot: RUNTIME_ROOT,
    }, null, 2));
    process.exit(1);
  }
  const rawConfig = fs.readFileSync(HOMEBRIDGE_CONFIG, "utf8");
  const config = JSON.parse(rawConfig);
  const office = officePlatform(config);
  const candidates = candidatesFromConfig(config, office);
  const results = [];

  for (const ip of candidates) {
    const response = await getJson(ip, office.password);
    const summary = response.ok ? summarizeDevices(response.body) : { count: 0, labels: [], classes: [] };
    results.push({
      ip,
      ok: response.ok,
      status: response.status,
      error: response.error || null,
      count: summary.count,
      labels: summary.labels.slice(0, 12),
      office: looksLikeOffice(summary),
    });
  }

  const match = results.find((item) => item.office);
  const output = {
    current: normalizeEndpoint(office.user),
    match: match ? match.ip : null,
    changed: false,
    backup: null,
    applied: false,
    results,
  };

  if (shouldApply) {
    if (!match) {
      throw new Error("No candidate responded with Office TaHoma devices");
    }
    if (normalizeEndpoint(office.user) !== match.ip) {
      output.backup = backupConfig(rawConfig);
      office.user = match.ip;
      saveJson(HOMEBRIDGE_CONFIG, config);
      output.changed = true;
    }
    output.applied = true;
  }

  console.log(JSON.stringify(output, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error) }, null, 2));
  process.exit(1);
});
