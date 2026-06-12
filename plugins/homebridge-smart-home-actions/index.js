"use strict";

const PLUGIN_NAME = "homebridge-smart-home-actions";
const PLATFORM_NAME = "SmartHomeActions";

const DEFAULT_ACTIONS = [
  { id: "check", name: "Check", path: "/action/run-check", timeoutMs: 120000 },
  { id: "hb-restart", name: "HB Restart", path: "/action/restart-homebridge", timeoutMs: 5000 },
  { id: "office-restart", name: "Office Restart", path: "/action/restart-office-tahoma", timeoutMs: 5000 },
  { id: "mute-alerts", name: "Mute Alerts", path: "/action/silence-alerts", timeoutMs: 120000 },
  { id: "refresh-sce", name: "Refresh SCE", path: "/action/refresh-sce", timeoutMs: 120000 },
  { id: "reconcile-energy", name: "Reconcile Energy", path: "/action/reconcile-energy", timeoutMs: 120000 },
  { id: "alarm-refresh", name: "Alarm Refresh", path: "/action/refresh-alarm-cache", timeoutMs: 120000 },
  { id: "garage-activity", name: "Garage Activity", path: "/action/garage-activity", timeoutMs: 120000 },
  { id: "panel-home", name: "Panel Home", path: "/action/panel-home", timeoutMs: 120000 },
  { id: "panel-stay", name: "Panel Stay", path: "/action/panel-stay", timeoutMs: 120000 },
  { id: "panel-off", name: "Panel Off", path: "/action/panel-off", timeoutMs: 120000 },
];

module.exports = (homebridge) => {
  homebridge.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, SmartHomeActionsPlatform);
};

class SmartHomeActionsPlatform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    this.api = api;
    this.Service = api.hap.Service;
    this.Characteristic = api.hap.Characteristic;
    this.accessories = new Map();
    this.baseUrl = (this.config.baseUrl || "http://127.0.0.1:18765").replace(/\/+$/, "");
    this.actions = this.configuredActions();

    this.api.on("didFinishLaunching", () => this.syncAccessories());
  }

  configureAccessory(accessory) {
    this.accessories.set(accessory.UUID, accessory);
  }

  configuredActions() {
    const configured = Array.isArray(this.config.actions) && this.config.actions.length
      ? this.config.actions
      : [];
    const actionIds = new Set(configured.map((action) => action.id || action.name));
    const missingDefaults = DEFAULT_ACTIONS.filter((action) => !actionIds.has(action.id));
    return configured.length ? [...configured, ...missingDefaults] : DEFAULT_ACTIONS;
  }

  syncAccessories() {
    const activeUUIDs = new Set();

    for (const action of this.actions) {
      const uuid = this.api.hap.uuid.generate(`${PLUGIN_NAME}:${action.id || action.name}`);
      activeUUIDs.add(uuid);

      let accessory = this.accessories.get(uuid);
      if (!accessory) {
        accessory = new this.api.platformAccessory(action.name, uuid);
        accessory.context.action = action;
        this.api.registerPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, [accessory]);
      }

      accessory.displayName = action.name;
      accessory.context.action = action;
      this.configureSwitch(accessory);
    }

    const stale = [...this.accessories.values()].filter((accessory) => !activeUUIDs.has(accessory.UUID));
    if (stale.length) {
      this.api.unregisterPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, stale);
    }

    this.log.info(`Configured ${this.actions.length} Smart Home action switches.`);
  }

  configureSwitch(accessory) {
    const action = accessory.context.action;
    const service = accessory.getService(this.Service.Switch) || accessory.addService(this.Service.Switch, action.name);
    service.setCharacteristic(this.Characteristic.Name, action.name);

    accessory.getService(this.Service.AccessoryInformation)
      .setCharacteristic(this.Characteristic.Manufacturer, "Smart Home Monitor")
      .setCharacteristic(this.Characteristic.Model, "Local Action Switch")
      .setCharacteristic(this.Characteristic.SerialNumber, `smart-home-action-${action.id || action.name}`);

    service.getCharacteristic(this.Characteristic.On)
      .removeAllListeners("get")
      .removeAllListeners("set")
      .on("get", (callback) => callback(null, false))
      .on("set", async (value, callback) => {
        if (!value) {
          callback(null);
          return;
        }

        try {
          await this.runAction(action);
          callback(null);
        } catch (error) {
          this.log.error(`${action.name} failed: ${error.message}`);
          callback(error);
        } finally {
          setTimeout(() => {
            service.getCharacteristic(this.Characteristic.On).updateValue(false);
          }, Number(action.resetAfterMs || this.config.resetAfterMs || 1000));
        }
      });
  }

  async runAction(action) {
    const url = action.url || `${this.baseUrl}${action.path}`;
    const timeoutMs = Number(action.timeoutMs || this.config.timeoutMs || 120000);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(url, {
        method: action.method || "POST",
        signal: controller.signal,
      });
      const body = await response.text().catch(() => "");

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      this.log.info(`${action.name} completed${this.summarizeResponse(body)}`);
    } finally {
      clearTimeout(timer);
    }
  }

  summarizeResponse(body) {
    if (!body) {
      return ".";
    }
    try {
      const payload = JSON.parse(body);
      const parts = [];
      if (payload.finishedAt) {
        parts.push(`finished ${payload.finishedAt}`);
      }
      if (payload.startedAt && !payload.finishedAt) {
        parts.push(`started ${payload.startedAt}`);
      }
      if (payload.status) {
        parts.push(`status ${payload.status}`);
      }
      if (payload.staleBefore !== undefined || payload.staleAfter !== undefined) {
        parts.push(`stale ${payload.staleBefore ?? "?"}->${payload.staleAfter ?? "?"}`);
      }
      if (payload.coverageEnd) {
        parts.push(`coverage through ${payload.coverageEnd}`);
      }
      if (payload.scheduled) {
        parts.push("scheduled");
      }
      return parts.length ? ` (${parts.join(", ")}).` : ".";
    } catch (error) {
      const summary = body.trim().replace(/\s+/g, " ").slice(0, 160);
      return summary ? `: ${summary}` : ".";
    }
  }
}
