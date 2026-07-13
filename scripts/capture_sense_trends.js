#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const DAYS_TO_CAPTURE = 14;
const LOCAL_TZ = "America/Los_Angeles";

function findSenseModule() {
  const root = path.resolve(__dirname, "..");
  const directCandidates = [
    path.join(root, "node_modules", "sense-energy-node"),
    path.join(root, "node_modules", "homebridge-sense-power-meter", "node_modules", "sense-energy-node"),
  ];
  for (const candidate of directCandidates) {
    if (fs.existsSync(path.join(candidate, "index.js"))) {
      return candidate;
    }
  }
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.existsSync(local) ? fs.readdirSync(local).sort().reverse() : []) {
    const candidate = path.join(
      local,
      nodeDir,
      "lib/node_modules/homebridge-sense-power-meter/node_modules/sense-energy-node"
    );
    if (fs.existsSync(path.join(candidate, "index.js"))) {
      return candidate;
    }
  }
  throw new Error("sense-energy-node dependency was not found in repo node_modules or under ~/.local");
}

function loadSenseConfig() {
  const root = path.resolve(__dirname, "..");
  const localConfigPath = path.join(root, "config", "sense.json");
  if (process.env.SENSE_USERNAME && process.env.SENSE_PASSWORD) {
    return { username: process.env.SENSE_USERNAME, password: process.env.SENSE_PASSWORD };
  }
  if (fs.existsSync(localConfigPath)) {
    const config = JSON.parse(fs.readFileSync(localConfigPath, "utf8"));
    const password = config.password || readKeychainPassword(config.password_keychain_service, config.password_keychain_account || config.username);
    if (config.username && password) {
      return { ...config, password };
    }
  }
  const candidates = [
    process.env.HOMEBRIDGE_CONFIG,
    path.join(os.homedir(), ".homebridge/config.json"),
  ].filter(Boolean);
  for (const configPath of candidates) {
    if (!fs.existsSync(configPath)) {
      continue;
    }
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    const accessory = (config.accessories || []).find((item) => item.accessory === "SensePowerMeter");
    if (accessory?.username && accessory?.password) {
      return accessory;
    }
  }
  const snapshotDir = path.join(root, "data", "snapshots");
  const snapshots = fs.existsSync(snapshotDir)
    ? fs.readdirSync(snapshotDir).filter((name) => name.endsWith(".json")).sort().reverse()
    : [];
  for (const snapshot of snapshots) {
    const payload = JSON.parse(fs.readFileSync(path.join(snapshotDir, snapshot), "utf8"));
    const config = payload?.homebridge?.config || {};
    const accessory = (config.accessories || []).find((item) => item.accessory === "SensePowerMeter");
    if (accessory?.username && accessory?.password) {
      return accessory;
    }
  }
  throw new Error("Sense credentials were not found in SENSE_USERNAME/SENSE_PASSWORD, config/sense.json, Homebridge config, or snapshots");
}

function readKeychainPassword(service, account) {
  if (!service || !account) {
    return null;
  }
  try {
    return execFileSync("security", ["find-generic-password", "-a", account, "-s", service, "-w"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trimEnd();
  } catch {
    return null;
  }
}

function localDateParts(date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: LOCAL_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const out = {};
  for (const part of parts) {
    if (part.type !== "literal") {
      out[part.type] = part.value;
    }
  }
  return out;
}

function localOffset(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: LOCAL_TZ,
    timeZoneName: "shortOffset",
  }).formatToParts(date);
  const value = parts.find((part) => part.type === "timeZoneName")?.value || "GMT-8";
  const match = value.match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/);
  if (!match) {
    return "-08:00";
  }
  return `${match[1]}${match[2].padStart(2, "0")}:${match[3] || "00"}`;
}

function localDayStartIso(daysAgo) {
  const now = new Date();
  const today = localDateParts(now);
  const target = new Date(Date.UTC(Number(today.year), Number(today.month) - 1, Number(today.day) - daysAgo, 12));
  const parts = localDateParts(target);
  return `${parts.year}-${parts.month}-${parts.day}T00:00:00${localOffset(target)}`;
}

async function main() {
  const sense = require(findSenseModule());
  const accessory = loadSenseConfig();
  const root = path.resolve(__dirname, "..");
  const outDir = path.join(root, "data");
  fs.mkdirSync(outDir, { recursive: true });

  const client = await sense({
    email: accessory.username,
    password: accessory.password,
    verbose: false,
  });

  const errors = [];
  const trends = {};
  for (let daysAgo = DAYS_TO_CAPTURE - 1; daysAgo >= 0; daysAgo -= 1) {
    const start = localDayStartIso(daysAgo);
    try {
      trends[start] = await client.getDailyUsage(start);
    } catch (error) {
      errors.push({ start, error: String(error?.message || error) });
    }
  }

  const result = {
    capturedAt: new Date().toISOString(),
    daysRequested: DAYS_TO_CAPTURE,
    daysCaptured: Object.keys(trends).length,
    errors,
    trends,
  };
  const outPath = path.join(outDir, "sense_trends_latest.json");
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2) + "\n");
  console.log(outPath);
  if (errors.length && Object.keys(trends).length === 0) {
    process.exitCode = 2;
  }
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
