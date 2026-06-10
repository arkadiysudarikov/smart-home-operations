#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function findSenseModule() {
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
  throw new Error("sense-energy-node dependency was not found under ~/.local");
}

function loadSenseConfig() {
  const configPath = path.join(os.homedir(), ".homebridge/config.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const accessory = (config.accessories || []).find((item) => item.accessory === "SensePowerMeter");
  if (!accessory) {
    throw new Error("SensePowerMeter accessory is not configured in Homebridge");
  }
  return accessory;
}

async function main() {
  const sense = require(findSenseModule());
  const accessory = loadSenseConfig();
  const root = path.resolve(__dirname, "..");
  const outDir = path.join(root, "data");
  fs.mkdirSync(outDir, { recursive: true });

  const startedAt = new Date().toISOString();
  let done = false;
  const client = await sense({
    email: encodeURI(accessory.username),
    password: encodeURI(accessory.password),
    verbose: false,
  });

  const finish = (payload) => {
    if (done) {
      return;
    }
    done = true;
    try {
      client.closeStream();
    } catch {}
    const realtime = payload?.payload || {};
    const result = {
      capturedAt: new Date().toISOString(),
      startedAt,
      type: payload?.type,
      watts: realtime.w,
      current: realtime.c,
      voltage:
        Array.isArray(realtime.voltage) && realtime.voltage.length
          ? realtime.voltage.reduce((sum, value) => sum + value, 0) / realtime.voltage.length
          : null,
      hz: realtime.hz,
      channelWatts: realtime.channels,
      devices: Array.isArray(realtime.devices)
        ? realtime.devices.map((device) => ({
            id: device.id,
            name: device.name,
            watts: device.w,
            current: device.c,
            icon: device.icon,
          }))
        : [],
    };
    const outPath = path.join(outDir, "sense_now_latest.json");
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2) + "\n");
    console.log(outPath);
    setTimeout(() => process.exit(0), 100);
  };

  client.events.on("data", (data) => {
    if (data?.type === "realtime_update" && data.payload) {
      finish(data);
    }
  });
  client.events.on("error", (error) => {
    if (!done) {
      console.error(String(error?.message || error));
      process.exit(2);
    }
  });

  client.openStream();
  setTimeout(() => {
    if (!done) {
      console.error("Timed out waiting for Sense realtime_update");
      process.exit(3);
    }
  }, 20000);
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
