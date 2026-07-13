#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function pluginRoot() {
  if (process.env.SMART_HOME_ALARM_PLUGIN_ROOT) {
    return process.env.SMART_HOME_ALARM_PLUGIN_ROOT;
  }
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.readdirSync(local).sort().reverse()) {
    const candidate = path.join(local, nodeDir, "lib/node_modules/homebridge-node-alarm-dot-com");
    if (fs.existsSync(path.join(candidate, "package.json"))) {
      return candidate;
    }
  }
  throw new Error("homebridge-node-alarm-dot-com was not found under ~/.local");
}

function main() {
  const shouldApply = process.argv.includes("--apply");
  const root = pluginRoot();
  const handler = path.join(root, "dist/handlers/BaseHandler.js");
  const original = "            service.getCharacteristic(hap.Characteristic.ConfiguredName)?.updateValue(alias);";
  const replacement = [
    "            if (service.testCharacteristic(hap.Characteristic.ConfiguredName)) {",
    "                service.removeCharacteristic(service.getCharacteristic(hap.Characteristic.ConfiguredName));",
    "            }",
  ].join("\n");
  let content = fs.readFileSync(handler, "utf8");
  let status;
  if (content.includes(replacement)) {
    status = "already patched";
  } else if (content.includes(original)) {
    content = content.replace(original, replacement);
    status = shouldApply ? "patched" : "would patch";
    if (shouldApply) {
      fs.writeFileSync(handler, content);
    }
  } else {
    throw new Error(`Expected patch target was not found in ${handler}`);
  }
  console.log(JSON.stringify({ root, applied: shouldApply, handler: status }, null, 2));
}

main();
