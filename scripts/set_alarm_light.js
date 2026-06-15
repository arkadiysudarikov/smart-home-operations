#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const RUNTIME_ROOT = path.join(os.homedir(), "Library/Application Support/SmartHomeMonitor");

function runningFromRuntimeRoot() {
  return path.resolve(ROOT) === path.resolve(RUNTIME_ROOT);
}

function findAlarmModule() {
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.existsSync(local) ? fs.readdirSync(local).sort().reverse() : []) {
    const candidate = path.join(
      local,
      nodeDir,
      "lib/node_modules/homebridge-node-alarm-dot-com/node_modules/node-alarm-dot-com/dist/index.js"
    );
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error("node-alarm-dot-com dependency was not found under ~/.local");
}

function loadAlarmConfig() {
  const configPath = path.join(os.homedir(), ".homebridge/config.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const platform = (config.platforms || []).find((item) => item.platform === "Alarmdotcom");
  if (!platform) {
    throw new Error("Alarmdotcom platform is not configured in Homebridge");
  }
  for (const key of ["username", "password", "mfaCookie"]) {
    if (!platform[key]) {
      throw new Error(`Alarmdotcom Homebridge config is missing ${key}`);
    }
  }
  return platform;
}

function parseArgs(argv) {
  const args = { command: "status", lightId: "104430779-1206", brightness: 100, forceOutsideRuntime: false };
  for (let index = 2; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--status") {
      args.command = "status";
    } else if (item === "--on") {
      args.command = "on";
    } else if (item === "--off") {
      args.command = "off";
    } else if (item === "--light-id") {
      args.lightId = argv[++index];
    } else if (item === "--brightness") {
      args.brightness = Number(argv[++index]);
    } else if (item === "--force-outside-runtime") {
      args.forceOutsideRuntime = true;
    } else {
      throw new Error(`unknown argument: ${item}`);
    }
  }
  if (!Number.isFinite(args.brightness)) {
    throw new Error("--brightness must be numeric");
  }
  args.brightness = Math.max(1, Math.min(100, Math.round(args.brightness)));
  return args;
}

function summarizeLight(light) {
  const attrs = light.attributes || {};
  return {
    id: light.id,
    name: attrs.description || null,
    state: attrs.state,
    on: attrs.state === 2,
    desiredState: attrs.desiredState,
    brightness: attrs.lightLevel ?? null,
    isDimmer: Boolean(attrs.isDimmer),
  };
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.forceOutsideRuntime && !runningFromRuntimeRoot()) {
    throw new Error(
      `refusing to send live Alarm.com light command outside the runtime root: ${ROOT} != ${RUNTIME_ROOT}`
    );
  }
  const alarm = require(findAlarmModule());
  const config = loadAlarmConfig();
  const auth = await alarm.login(config.username, config.password, config.mfaCookie);
  const systemId = auth.systems[0];
  if (!systemId) {
    throw new Error("Alarm.com login returned no systems");
  }

  const state = await alarm.getCurrentState(systemId, auth);
  const current = (state.lights || []).find((light) => light.id === args.lightId);
  if (!current) {
    throw new Error(`light not found: ${args.lightId}`);
  }

  let result = current;
  if (args.command === "on") {
    result = (await alarm.setLightOn(args.lightId, auth, args.brightness, Boolean(current.attributes?.isDimmer))).data;
  } else if (args.command === "off") {
    result = (await alarm.setLightOff(args.lightId, auth, args.brightness, Boolean(current.attributes?.isDimmer))).data;
  }

  process.stdout.write(JSON.stringify({ ok: true, command: args.command, light: summarizeLight(result) }) + "\n");
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({ ok: false, error: error.message }) + "\n");
  process.exitCode = 1;
});
