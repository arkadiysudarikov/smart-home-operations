#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function pluginRoot() {
  if (process.env.SMART_HOME_SMARTHQ_PLUGIN_ROOT) {
    return process.env.SMART_HOME_SMARTHQ_PLUGIN_ROOT;
  }
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.existsSync(local) ? fs.readdirSync(local).sort().reverse() : []) {
    const candidate = path.join(local, nodeDir, "lib/node_modules/@homebridge-plugins/homebridge-smarthq");
    if (fs.existsSync(path.join(candidate, "package.json"))) {
      return candidate;
    }
  }
  throw new Error("@homebridge-plugins/homebridge-smarthq was not found under ~/.local");
}

function patchFile(file, original, replacement, shouldApply) {
  const before = fs.readFileSync(file, "utf8");
  if (before.includes(replacement)) {
    return "already patched";
  }
  if (!before.includes(original)) {
    throw new Error(`Expected patch target was not found in ${file}`);
  }
  if (!shouldApply) {
    return "would patch";
  }
  fs.writeFileSync(file, before.replace(original, replacement));
  return "patched";
}

function main() {
  const args = new Set(process.argv.slice(2));
  const shouldApply = args.has("--apply");
  const root = pluginRoot();
  const washer = path.join(root, "dist/devices/clothesWasher.js");
  const oven = path.join(root, "dist/devices/oven.js");
  const washerStatus = patchFile(
    washer,
    `            const seconds = Math.round(minutes * 60); // Don't cap, let it show actual time
            this.infoLog(\`Time Remaining - Hex: \${r}, Decimal: \${value}, Minutes: \${minutes}, Seconds: \${seconds}\`);
            return seconds;`,
    `            const seconds = Math.round(minutes * 60);
            const homeKitSeconds = Math.min(seconds, 3600);
            this.infoLog(\`Time Remaining - Hex: \${r}, Decimal: \${value}, Minutes: \${minutes}, Seconds: \${seconds}, HomeKit Seconds: \${homeKitSeconds}\`);
            return homeKitSeconds;`,
    shouldApply
  );
  const ovenStatus = patchFile(
    oven,
    `            const seconds = minutes * 60;
            this.debugLog(\`Cook Time Remaining - Hex: \${r}, Minutes: \${minutes}, Seconds: \${seconds}\`);
            return seconds;`,
    `            const seconds = minutes * 60;
            const homeKitSeconds = Math.min(seconds, 3600);
            this.debugLog(\`Cook Time Remaining - Hex: \${r}, Minutes: \${minutes}, Seconds: \${seconds}, HomeKit Seconds: \${homeKitSeconds}\`);
            return homeKitSeconds;`,
    shouldApply
  );
  console.log(JSON.stringify({ root, applied: shouldApply, washer: washerStatus, oven: ovenStatus }, null, 2));
}

main();
