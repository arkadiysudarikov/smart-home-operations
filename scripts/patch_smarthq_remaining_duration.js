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
  const originals = Array.isArray(original) ? original : [original];
  const target = originals.find((candidate) => before.includes(candidate));
  if (!target) {
    throw new Error(`Expected patch target was not found in ${file}`);
  }
  if (!shouldApply) {
    return "would patch";
  }
  fs.writeFileSync(file, before.replace(target, replacement));
  return "patched";
}

function main() {
  const args = new Set(process.argv.slice(2));
  const shouldApply = args.has("--apply");
  const root = pluginRoot();
  const washer = path.join(root, "dist/devices/clothesWasher.js");
  const device = path.join(root, "dist/devices/device.js");
  const oven = path.join(root, "dist/devices/oven.js");
  const accessToken = path.join(root, "dist/getAccessToken.js");
  const platform = path.join(root, "dist/platform.js");
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
  const authStatus = patchFile(
    accessToken,
    [
      `        // If we have HTML in the response, try to handle it
        if (res.data && typeof res.data === 'string') {
            code = await asyncHandleOkResponse(res.data);
        }`,
      `        // Follow redirects to MFA or terms pages before trying the response body.
        if (res.headers.location) {
            const resolved = res.headers.location.startsWith('/') ? \`\${LOGIN_URL}\${res.headers.location}\` : res.headers.location;
            const intermediateResp = await aclient.get(resolved, { maxRedirects: 0, validateStatus: () => true });
            code = tryExtractCodeFromLocation(intermediateResp.headers.location);
            if (!code && intermediateResp.status === 200 && typeof intermediateResp.data === 'string') {
                code = await asyncHandleOkResponse(intermediateResp.data);
            }
        }
        // If we have HTML in the response, try to handle it.
        if (!code && res.data && typeof res.data === 'string') {
            code = await asyncHandleOkResponse(res.data);
        }`,
    ],
    `        // Follow redirects to MFA or terms pages before trying the response body.
        if (res.headers.location) {
            const resolved = new URL(res.headers.location, LOGIN_URL).toString();
            const intermediateResp = await aclient.get(resolved, { maxRedirects: 0, validateStatus: () => true });
            code = tryExtractCodeFromLocation(intermediateResp.headers.location);
            if (!code && intermediateResp.status === 200 && typeof intermediateResp.data === 'string') {
                code = await asyncHandleOkResponse(intermediateResp.data);
            }
        }
        // If we have HTML in the response, try to handle it.
        if (!code && res.data && typeof res.data === 'string') {
            code = await asyncHandleOkResponse(res.data);
        }`,
    shouldApply
  );
  const authMfaUrlStatus = patchFile(
    accessToken,
    "                        url: `${LOGIN_URL}/account/active/redirect`,",
    "                        url: new URL('/account/active/redirect', LOGIN_URL).toString(),",
    shouldApply
  );
  const comboStatus = patchFile(
    platform,
    `                        case 'Clothes Washer':
                            await this.createSmartHQClothesWasher(userId, device, details, features);
                            break;`,
    `                        case 'Clothes Washer':
                            await this.createSmartHQClothesWasher(userId, device, details, features);
                            break;
                        case 'Combination Washer Dryer':
                            await this.createSmartHQClothesWasher(userId, device, details, features);
                            break;`,
    shouldApply
  );
  const heartbeatImportsStatus = patchFile(
    device,
    `import axios from 'axios';
import { ERD_TYPES } from '../settings.js';`,
    `import axios from 'axios';
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { ERD_TYPES } from '../settings.js';
const SMART_HOME_HEARTBEAT_PATH = process.env.SMART_HOME_SMARTHQ_HEARTBEAT_PATH
    ?? join(homedir(), 'Library', 'Application Support', 'SmartHomeMonitor', 'data', 'smarthq_erd_heartbeat.json');
function recordSmartHQHeartbeat(applianceId, nickname, erd, value) {
    try {
        const now = new Date().toISOString();
        let payload = { version: 1, devices: {} };
        try {
            payload = JSON.parse(readFileSync(SMART_HOME_HEARTBEAT_PATH, 'utf8'));
        }
        catch {
            // The first successful ERD read creates the heartbeat file.
        }
        payload.devices ??= {};
        const prior = payload.devices[applianceId] ?? { erds: {} };
        prior.erds ??= {};
        const changed = prior.erds[erd]?.value !== value;
        prior.nickname = nickname;
        prior.lastSuccessAt = now;
        prior.lastChangedAt = changed ? now : (prior.lastChangedAt ?? now);
        prior.erds[erd] = {
            value,
            lastSuccessAt: now,
            lastChangedAt: changed ? now : (prior.erds[erd]?.lastChangedAt ?? now),
        };
        payload.updatedAt = now;
        payload.devices[applianceId] = prior;
        mkdirSync(dirname(SMART_HOME_HEARTBEAT_PATH), { recursive: true });
        const temporary = \`\${SMART_HOME_HEARTBEAT_PATH}.\${process.pid}.tmp\`;
        writeFileSync(temporary, \`\${JSON.stringify(payload, null, 2)}\\n\`, { mode: 0o600 });
        renameSync(temporary, SMART_HOME_HEARTBEAT_PATH);
    }
    catch {
        // Observability must never break the appliance integration.
    }
}`,
    shouldApply
  );
  const heartbeatReadStatus = patchFile(
    device,
    `            if (typeof d.data.value === 'object') {
                const jsonValue = JSON.stringify(d.data.value);
                await this.debugLog(\`ERD \${erd} returned object: \${jsonValue}\`);
                return jsonValue;
            }
            await this.debugLog(\`ERD \${erd} value: \${d.data.value}\`);
            return String(d.data.value);`,
    `            if (typeof d.data.value === 'object') {
                const jsonValue = JSON.stringify(d.data.value);
                recordSmartHQHeartbeat(this.getApplianceId(), this.getDisplayName(), erd, jsonValue);
                await this.debugLog(\`ERD \${erd} returned object: \${jsonValue}\`);
                return jsonValue;
            }
            const value = String(d.data.value);
            recordSmartHQHeartbeat(this.getApplianceId(), this.getDisplayName(), erd, value);
            await this.debugLog(\`ERD \${erd} value: \${d.data.value}\`);
            return value;`,
    shouldApply
  );
  console.log(JSON.stringify({
    root,
    applied: shouldApply,
    washer: washerStatus,
    oven: ovenStatus,
    auth: authStatus,
    authMfaUrl: authMfaUrlStatus,
    combo: comboStatus,
    heartbeatImports: heartbeatImportsStatus,
    heartbeatRead: heartbeatReadStatus,
  }, null, 2));
}

main();
