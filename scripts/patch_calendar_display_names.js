#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

function pluginRoot() {
  if (process.env.SMART_HOME_CALENDAR_PLUGIN_ROOT) {
    return process.env.SMART_HOME_CALENDAR_PLUGIN_ROOT;
  }
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.readdirSync(local).sort().reverse()) {
    const candidate = path.join(local, nodeDir, "lib/node_modules/homebridge-calendar-scheduler");
    if (fs.existsSync(path.join(candidate, "package.json"))) {
      return candidate;
    }
  }
  throw new Error("homebridge-calendar-scheduler was not found under ~/.local");
}

function patchFile(file, patches, shouldApply) {
  let content = fs.readFileSync(file, "utf8");
  const statuses = [];
  for (const [original, replacement] of patches) {
    if (content.includes(replacement)) {
      statuses.push("already patched");
      continue;
    }
    const candidates = Array.isArray(original) ? original : [original];
    const target = candidates.find((candidate) => content.includes(candidate));
    if (!target) {
      throw new Error(`Expected patch target was not found in ${file}`);
    }
    content = content.replace(target, replacement);
    statuses.push(shouldApply ? "patched" : "would patch");
  }
  if (shouldApply && statuses.includes("patched")) {
    fs.writeFileSync(file, content);
  }
  return statuses;
}

function main() {
  const shouldApply = process.argv.includes("--apply");
  const root = pluginRoot();
  const calendarConfig = path.join(root, "dist/configs/calendar.config.js");
  const eventConfig = path.join(root, "dist/configs/event.config.js");
  const calendarHandler = path.join(root, "dist/calendar.handler.js");
  const accessoryBase = path.join(root, "node_modules/homebridge-util-accessory-manager/dist/accessory.js");

  const calendarStatus = patchFile(calendarConfig, [
    [
      "    calendarName;\n    calendarUrl;",
      "    calendarName;\n    calendarDisplayName;\n    calendarUrl;",
    ],
    [
      "        this.calendarName = calendar.calendarName;\n        this.calendarUrl = calendar.calendarUrl;",
      "        this.calendarName = calendar.calendarName;\n        this.calendarDisplayName = calendar.calendarDisplayName || this.calendarName;\n        this.calendarUrl = calendar.calendarUrl;",
    ],
  ], shouldApply);

  const eventStatus = patchFile(eventConfig, [
    [
      "    eventName;\n    eventTriggerOnUpdates;",
      "    eventName;\n    eventDisplayName;\n    eventTriggerOnUpdates;",
    ],
    [
      "                .replace(/\\s+/g, ' ');\n        this.calendarEventNotifications =",
      "                .replace(/\\s+/g, ' ');\n        this.eventDisplayName = event.eventDisplayName || this.safeEventName;\n        this.calendarEventNotifications =",
    ],
  ], shouldApply);

  const handlerStatus = patchFile(calendarHandler, [
    [
      "this._prepareContext(this.calendarConfig.id, this.calendarConfig.calendarName, this.calendarConfig)",
      "this._prepareContext(this.calendarConfig.id, this.calendarConfig.calendarDisplayName, this.calendarConfig)",
    ],
    [
      "this._prepareContext(event.id, event.safeEventName, this.calendarConfig, event)",
      "this._prepareContext(event.id, event.eventDisplayName, this.calendarConfig, event)",
    ],
  ], shouldApply);

  const accessoryStatus = patchFile(accessoryBase, [
    [
      `    _setAccessoryInformation(manufacturer, model, serialNumber, version) {
        this._accessory.getService(this.$_api.hap.Service.AccessoryInformation)
            .setCharacteristic(this.$_api.hap.Characteristic.Manufacturer, manufacturer)`,
      `    _setAccessoryInformation(manufacturer, model, serialNumber, version) {
        const name = this._accessory.context.name;
        if (name) {
            this._accessory.updateDisplayName(name);
            this._accessory.displayName = name;
            this._accessory.getService(this.$_api.hap.Service.AccessoryInformation)
                .setCharacteristic(this.$_api.hap.Characteristic.Name, name);
        }
        this._accessory.getService(this.$_api.hap.Service.AccessoryInformation)
            .setCharacteristic(this.$_api.hap.Characteristic.Manufacturer, manufacturer)`,
    ],
    [
      [
        `    _getService(name, service) {
        return (this._accessory.getService(service)
            || this._accessory.addService(service, ...[name]))
            .setCharacteristic(this.$_api.hap.Characteristic.Name, name);
    }`,
        `    _getService(name, service) {
        const target = this._accessory.getService(service)
            || this._accessory.addService(service, ...[name]);
        target.displayName = name;
        target.setCharacteristic(this.$_api.hap.Characteristic.Name, name);
        target.getCharacteristic(this.$_api.hap.Characteristic.ConfiguredName)?.updateValue(name);
        return target;
    }`,
      ],
      `    _getService(name, service) {
        const target = this._accessory.getService(service)
            || this._accessory.addService(service, ...[name]);
        target.displayName = name;
        target.setCharacteristic(this.$_api.hap.Characteristic.Name, name);
        if (target.testCharacteristic(this.$_api.hap.Characteristic.ConfiguredName)) {
            target.removeCharacteristic(target.getCharacteristic(this.$_api.hap.Characteristic.ConfiguredName));
        }
        return target;
    }`,
    ],
  ], shouldApply);

  console.log(JSON.stringify({
    root,
    applied: shouldApply,
    calendarConfig: calendarStatus,
    eventConfig: eventStatus,
    calendarHandler: handlerStatus,
    accessoryManager: accessoryStatus,
  }, null, 2));
}

main();
