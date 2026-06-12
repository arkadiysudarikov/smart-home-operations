#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

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
  const args = { mode: null, partitionId: null };
  for (let index = 2; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--mode") {
      args.mode = argv[++index];
    } else if (item === "--partition-id") {
      args.partitionId = argv[++index];
    } else {
      throw new Error(`unknown argument: ${item}`);
    }
  }
  if (!["home", "stay", "off"].includes(args.mode)) {
    throw new Error("--mode must be one of: home, stay, off");
  }
  return args;
}

function armingOptions(config, mode) {
  const requested = mode === "home" ? "stay" : mode;
  const configured = ((config.armingModes || {})[requested]) || {};
  return {
    noEntryDelay: mode === "home" ? true : Boolean(configured.noEntryDelay),
    silentArming: Boolean(configured.silentArming),
    nightArming: Boolean(configured.nightArming),
    forceBypass: Boolean(configured.forceBypass),
  };
}

function summarizePartition(partition) {
  const attrs = partition.attributes || {};
  return {
    id: partition.id,
    name: attrs.description || null,
    stateText: attrs.stateText || null,
    state: attrs.state,
    desiredState: attrs.desiredState,
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const alarm = require(findAlarmModule());
  const config = loadAlarmConfig();
  const auth = await alarm.login(config.username, config.password, config.mfaCookie);
  const systemId = auth.systems[0];
  if (!systemId) {
    throw new Error("Alarm.com login returned no systems");
  }

  const state = await alarm.getCurrentState(systemId, auth);
  const partition = args.partitionId
    ? (state.partitions || []).find((item) => String(item.id) === String(args.partitionId))
    : (state.partitions || [])[0];
  if (!partition) {
    throw new Error(args.partitionId ? `partition not found: ${args.partitionId}` : "no Alarm.com partition found");
  }

  let response;
  if (args.mode === "off") {
    response = await alarm.disarm(partition.id, auth);
  } else {
    response = await alarm.armStay(partition.id, auth, armingOptions(config, args.mode));
  }

  process.stdout.write(
    JSON.stringify({
      ok: true,
      mode: args.mode,
      partition: summarizePartition(partition),
      responseStatus: response?.status ?? null,
    }) + "\n"
  );
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({ ok: false, error: error.message }) + "\n");
  process.exitCode = 1;
});
