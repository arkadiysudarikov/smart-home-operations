#!/usr/bin/env node
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "..");
const OUTPUT_PATH = path.join(ROOT, "data", "latest_smarthq_laundry_state.json");
const HOMEBRIDGE_CONFIG = process.env.HOMEBRIDGE_CONFIG_PATH || path.join(os.homedir(), ".homebridge", "config.json");

function packageCandidates() {
  const candidates = [];
  if (process.env.HOMEBRIDGE_UI_ROOT) candidates.push(process.env.HOMEBRIDGE_UI_ROOT);
  const localRoot = path.join(os.homedir(), ".local");
  if (fs.existsSync(localRoot)) {
    for (const entry of fs.readdirSync(localRoot)) {
      if (entry.startsWith("node-")) {
        candidates.push(path.join(localRoot, entry, "lib", "node_modules", "homebridge-config-ui-x"));
      }
    }
  }
  candidates.push(
    "/opt/homebrew/lib/node_modules/homebridge-config-ui-x",
    "/usr/local/lib/node_modules/homebridge-config-ui-x",
  );
  return candidates;
}

function findHapClient() {
  const relative = path.join("node_modules", "@homebridge", "hap-client", "dist", "index.js");
  const root = packageCandidates().find((candidate) => fs.existsSync(path.join(candidate, relative)));
  if (!root) throw new Error("Homebridge HAP client was not found");
  return path.join(root, relative);
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function characteristic(service, type) {
  return service?.serviceCharacteristics?.find((item) => item.type === type)?.value ?? null;
}

function booleanValue(value) {
  if (value === null || value === undefined) return null;
  return Boolean(value);
}

async function discoverLaundryServices(client) {
  let services = [];
  for (let attempt = 0; attempt < 5; attempt += 1) {
    await sleep(1500);
    services = await client.getAllServices();
    const names = new Set(services.map((service) => service.accessoryInformation?.Name));
    if (names.has("Washer") && names.has("Dryer")) return services;
    client.refreshInstances();
  }
  return services;
}

async function main() {
  const capturedAt = new Date().toISOString();
  try {
    const config = JSON.parse(fs.readFileSync(HOMEBRIDGE_CONFIG, "utf8"));
    if (!config.bridge?.pin) throw new Error("Homebridge bridge PIN is missing");
    const { HapClient } = await import(pathToFileURL(findHapClient()));
    const logger = { debug() {}, info() {}, log() {}, warn() {}, error() {} };
    const client = new HapClient({
      pin: config.bridge.pin,
      logger,
      config: { discoveryTimeout: 10000 },
    });
    const services = await discoverLaundryServices(client);
    const devices = {};
    for (const appliance of ["washer", "dryer"]) {
      const name = appliance[0].toUpperCase() + appliance.slice(1);
      const applianceServices = services.filter((service) => service.accessoryInformation?.Name === name);
      const mainService = applianceServices.find((service) => service.serviceName === name);
      const cycleService = applianceServices.find((service) => service.serviceName === "Cycle Status");
      if (!mainService || !cycleService) continue;
      await Promise.all(
        [mainService, cycleService].map((service) => service.refreshCharacteristics().catch(() => undefined)),
      );
      devices[appliance] = {
        inUse: booleanValue(characteristic(mainService, "InUse")),
        cycleActive: booleanValue(characteristic(cycleService, "MotionDetected")),
        // SmartHQ returns no usable door ERD for these models. Treating the cached
        // closed value as real would create false unload reminders.
        doorOpen: null,
        remainingSeconds: characteristic(mainService, "RemainingDuration"),
        model: mainService.accessoryInformation?.Model ?? null,
      };
    }
    if (!devices.washer || !devices.dryer) throw new Error("washer or dryer HAP services were not discovered");
    const payload = { ok: true, capturedAt, source: "homebridge-hap-live", devices };
    fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
    const temporary = `${OUTPUT_PATH}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 });
    fs.renameSync(temporary, OUTPUT_PATH);
    console.log(JSON.stringify(payload, null, 2));
    process.exit(0);
  } catch (error) {
    const payload = { ok: false, capturedAt, source: "homebridge-hap-live", error: error.message };
    fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
    fs.writeFileSync(OUTPUT_PATH, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 });
    console.error(JSON.stringify(payload, null, 2));
    process.exit(1);
  }
}

main();
