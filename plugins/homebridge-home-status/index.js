"use strict";

const fs = require("fs");
const crypto = require("crypto");

const PLUGIN_NAME = "homebridge-home-status";
const PLATFORM_NAME = "HomeStatusDashboard";
const DEFAULT_SOURCE_PATH = `${process.env.HOME}/Library/Application Support/SmartHomeMonitor/data/latest.json`;
const DEFAULT_REFRESH_SECONDS = 30;
const EXCLUDED_PLATFORMS = new Set(["SmartHomeActions", PLATFORM_NAME]);

const SENSOR_TYPES = {
  OccupancyDetected: { service: "OccupancySensor", characteristic: "OccupancyDetected" },
  ContactSensorState: { service: "ContactSensor", characteristic: "ContactSensorState" },
  MotionDetected: { service: "MotionSensor", characteristic: "MotionDetected" },
  CurrentTemperature: { service: "TemperatureSensor", characteristic: "CurrentTemperature" },
  Temperature: { service: "TemperatureSensor", characteristic: "CurrentTemperature" },
  CurrentRelativeHumidity: { service: "HumiditySensor", characteristic: "CurrentRelativeHumidity" },
  CurrentAmbientLightLevel: { service: "LightSensor", characteristic: "CurrentAmbientLightLevel" },
  AirQuality: { service: "AirQualitySensor", characteristic: "AirQuality" },
  StatusLowBattery: { service: "OccupancySensor", characteristic: "OccupancyDetected", suffix: "Battery Low" },
};

function sensorKey(sensor) {
  return [sensor.platform, sensor.accessory, sensor.service, sensor.sourceCharacteristic].join("|");
}

function shortHash(value) {
  return crypto.createHash("sha256").update(value).digest("hex").slice(0, 24);
}

function displayName(sensor) {
  const parts = [sensor.accessory];
  const genericService = /sensor$/i.test(sensor.service) || sensor.service === sensor.serviceType;
  if (sensor.service && !genericService && sensor.service !== sensor.accessory) {
    const accessoryPrefix = `${sensor.accessory} `;
    parts.push(sensor.service.startsWith(accessoryPrefix) ? sensor.service.slice(accessoryPrefix.length) : sensor.service);
  }
  if (sensor.suffix) {
    parts.push(sensor.suffix);
  }
  const valueLabels = {
    CurrentTemperature: "Temperature",
    Temperature: "Temperature",
    CurrentRelativeHumidity: "Humidity",
    CurrentAmbientLightLevel: "Light",
    AirQuality: "Air Quality",
  };
  const valueLabel = valueLabels[sensor.sourceCharacteristic];
  if (valueLabel && !parts.join(" ").toLowerCase().includes(valueLabel.toLowerCase())) {
    parts.push(valueLabel);
  }
  return parts.join(" ");
}

function normalizeValue(sensor, value) {
  switch (sensor.sourceCharacteristic) {
    case "OccupancyDetected":
    case "MotionDetected":
    case "StatusLowBattery":
      return value === true || Number(value) === 1 ? 1 : 0;
    case "ContactSensorState":
      return Number(value) === 1 ? 1 : 0;
    case "CurrentTemperature":
    case "Temperature":
      return Math.max(-270, Math.min(100, Number(value) || 0));
    case "CurrentRelativeHumidity":
      return Math.max(0, Math.min(100, Number(value) || 0));
    case "CurrentAmbientLightLevel":
      return Math.max(0.0001, Math.min(100000, Number(value) || 0.0001));
    case "AirQuality":
      return Math.max(0, Math.min(5, Math.round(Number(value) || 0)));
    default:
      return value;
  }
}

function discoverSensors(payload, sourcePlatforms = null) {
  const characteristics = payload?.homeEvents?.currentCharacteristics || {};
  const discovered = new Map();
  const allowedPlatforms = Array.isArray(sourcePlatforms) && sourcePlatforms.length
    ? new Set(sourcePlatforms)
    : null;

  for (const item of Object.values(characteristics)) {
    const definition = SENSOR_TYPES[item.characteristic];
    if (!definition || EXCLUDED_PLATFORMS.has(item.platform) || (allowedPlatforms && !allowedPlatforms.has(item.platform)) || !item.accessory || !item.service) {
      continue;
    }
    const sensor = {
      platform: item.platform || "Unknown",
      accessory: item.accessory,
      service: item.service,
      sourceCharacteristic: item.characteristic,
      serviceType: definition.service,
      characteristicType: definition.characteristic,
      suffix: definition.suffix,
      value: item.value,
    };
    discovered.set(sensorKey(sensor), sensor);
  }

  return [...discovered.values()].sort((a, b) => sensorKey(a).localeCompare(sensorKey(b)));
}

module.exports = (homebridge) => {
  homebridge.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, HomeStatusDashboardPlatform);
};

class HomeStatusDashboardPlatform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    this.api = api;
    this.Service = api.hap.Service;
    this.Characteristic = api.hap.Characteristic;
    this.accessories = new Map();
    this.sourcePath = this.config.sourcePath || DEFAULT_SOURCE_PATH;
    this.refreshMs = Math.max(10, Number(this.config.refreshSeconds || DEFAULT_REFRESH_SECONDS)) * 1000;
    this.timer = null;

    this.api.on("didFinishLaunching", () => {
      this.refresh();
      this.timer = setInterval(() => this.refresh(), this.refreshMs);
    });
    this.api.on("shutdown", () => clearInterval(this.timer));
  }

  configureAccessory(accessory) {
    this.accessories.set(accessory.UUID, accessory);
  }

  loadSensors() {
    return discoverSensors(
      JSON.parse(fs.readFileSync(this.sourcePath, "utf8")),
      this.config.sourcePlatforms,
    );
  }

  refresh() {
    let sensors;
    try {
      sensors = this.loadSensors();
    } catch (error) {
      this.log.warn(`Could not read dashboard sensor source: ${error.message}`);
      return;
    }

    const activeUUIDs = new Set();
    const chunkSize = Math.max(1, Math.min(25, Number(this.config.chunkSize || 20)));
    const chunks = [];
    for (let index = 0; index < sensors.length; index += chunkSize) {
      chunks.push(sensors.slice(index, index + chunkSize));
    }

    for (let chunkIndex = 0; chunkIndex < chunks.length; chunkIndex += 1) {
      const chunk = chunks[chunkIndex];
      const chunkKey = `${this.config.name || PLATFORM_NAME}:chunk:${chunkIndex + 1}`;
      const uuid = this.api.hap.uuid.generate(`${PLUGIN_NAME}:${chunkKey}`);
      activeUUIDs.add(uuid);
      let accessory = this.accessories.get(uuid);
      const name = `${this.config.name || "Home Status"} ${chunkIndex + 1}`;
      if (!accessory) {
        accessory = new this.api.platformAccessory(name, uuid);
        this.accessories.set(uuid, accessory);
        this.api.registerPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, [accessory]);
      }
      if (accessory.displayName !== name) {
        accessory.updateDisplayName(name);
      }
      const activeServiceSubtypes = new Set();
      for (const sensor of chunk) {
        const subtype = shortHash(sensorKey(sensor));
        activeServiceSubtypes.add(`sensor:${subtype}`);
        this.configureSensor(accessory, sensor, displayName(sensor), subtype);
      }
      for (const service of [...accessory.services]) {
        if (service.UUID === this.Service.AccessoryInformation.UUID || !service.subtype) {
          continue;
        }
        if (!activeServiceSubtypes.has(service.subtype)) {
          accessory.removeService(service);
        }
      }
      accessory.context.sensorKeys = chunk.map(sensorKey);
      accessory.getService(this.Service.AccessoryInformation)
        .setCharacteristic(this.Characteristic.Name, name)
        .setCharacteristic(this.Characteristic.Manufacturer, "Smart Home Monitor")
        .setCharacteristic(this.Characteristic.Model, "Read-only Sensor Dashboard")
        .setCharacteristic(this.Characteristic.SerialNumber, `home-status-${shortHash(chunkKey)}`);
      this.api.updatePlatformAccessories([accessory]);
    }

    const stale = [...this.accessories.values()].filter((accessory) => !activeUUIDs.has(accessory.UUID));
    if (stale.length) {
      this.api.unregisterPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, stale);
      for (const accessory of stale) {
        this.accessories.delete(accessory.UUID);
      }
    }
    this.log.info(`Updated ${sensors.length} read-only Home Status sensors in ${chunks.length} containers.`);
  }

  configureSensor(accessory, sensor, name, subtype) {
    const ServiceType = this.Service[sensor.serviceType];
    const CharacteristicType = this.Characteristic[sensor.characteristicType];
    const serviceSubtype = `sensor:${subtype}`;
    const service = accessory.getServiceById(ServiceType, serviceSubtype) || accessory.addService(ServiceType, name, serviceSubtype);
    service.displayName = name;
    service.setCharacteristic(this.Characteristic.Name, name);
    service.addOptionalCharacteristic(this.Characteristic.ConfiguredName);
    service.setCharacteristic(this.Characteristic.ConfiguredName, name);
    service.getCharacteristic(CharacteristicType).removeAllListeners("get").updateValue(normalizeValue(sensor, sensor.value));

  }
}

module.exports.discoverSensors = discoverSensors;
module.exports.displayName = displayName;
module.exports.normalizeValue = normalizeValue;
module.exports.sensorKey = sensorKey;
module.exports.shortHash = shortHash;
