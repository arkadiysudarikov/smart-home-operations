#!/usr/bin/env node
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

const LOCAL_TZ = "America/Los_Angeles";
const BASE = "https://www.alarm.com";
const ENERGY_URL = `${BASE}/web/Energy/EnergyConsumption.aspx`;
const MAX_PAGES = 160;
const RANGES = ["24h", "7d", "21d", "6m", "12m"];
const EXPECTED_VIDEO_RULES = [
  "Alarm Video",
  "Entry Delay Video",
  "Entry Door Video",
  "Family Slider Video",
  "Garage Door Video",
  "Sideyard Gate Video",
];
const TRACKED_AUTOMATION_RULES = [
  "Garage Door 2207 Opens - Garage Light 100%",
  "Garage Door 2210 Opens - Garage Light 100%",
  "Garage Door Contact Opens - Garage Light 100%",
  "When Garage Door is Opened, then Turn Lights On",
  "Panel Camera Alarm Image Uploads",
  "Panel Camera Disarm Image Uploads",
  "Peek-Ins & Manual Image Uploads",
  "Fire Safety",
  "Sensor Left Open Energy Saver",
  "Smart Away",
  "Smart Humidity Control",
  "Weather Event - Energy Savings",
  ...EXPECTED_VIDEO_RULES,
];
const STATE_NAMES = {
  partitions: { 0: "Unknown", 1: "Disarmed", 2: "Armed stay", 3: "Armed away", 4: "Armed night" },
  sensors: { 0: "Unknown", 1: "Closed", 2: "Open", 3: "Idle", 4: "Active", 5: "Dry", 6: "Wet" },
  lights: { 2: "On", 3: "Off" },
  locks: { 1: "Locked", 2: "Unlocked" },
  garages: { 1: "Open", 2: "Closed" },
  thermostats: { 1: "Off", 2: "Heating", 3: "Cooling", 4: "Auto" },
  remoteTemperatureSensors: { 1: "Ok" },
};

function findAlarmModule() {
  const local = path.join(os.homedir(), ".local");
  for (const nodeDir of fs.existsSync(local) ? fs.readdirSync(local).sort().reverse() : []) {
    const candidate = path.join(
      local,
      nodeDir,
      "lib/node_modules/homebridge-node-alarm-dot-com/node_modules/node-alarm-dot-com/dist/core.js"
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

function rootDir() {
  return path.resolve(__dirname, "..");
}

function sourceRootDir() {
  return path.join(os.homedir(), "Documents", "Smart Home");
}

function ensureDirs(root) {
  fs.mkdirSync(path.join(root, "config"), { recursive: true });
  fs.mkdirSync(path.join(root, "data"), { recursive: true });
  fs.mkdirSync(path.join(root, "reports"), { recursive: true });
}

function loadSourcesConfig(root) {
  const configPath = path.join(root, "config/sources.json");
  if (!fs.existsSync(configPath)) {
    return {};
  }
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch {
    return {};
  }
}

function stripTags(html) {
  return String(html || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

function decodeHtml(value) {
  return String(value || "")
    .replace(/&amp;/g, "&")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function numberFromHtmlById(html, id) {
  const re = new RegExp(`id=["']${id}["'][^>]*>([\\s\\S]*?)<\\/[^>]+>`, "i");
  const match = re.exec(html);
  if (!match) {
    return null;
  }
  const text = stripTags(match[1]);
  const num = /-?[\d,.]+/.exec(text);
  return num ? Number(num[0].replace(/,/g, "")) : null;
}

function parseDashboard(html) {
  const projected =
    numberFromHtmlById(html, "ctl00_phBody_ucDashboard_lblProjectedTooltip1") ||
    numberFromHtmlById(html, "ctl00_phBody_lblGoalProjected");
  const budget =
    numberFromHtmlById(html, "ctl00_phBody_ucDashboard_lblGoalTooltip1") ||
    numberFromHtmlById(html, "ctl00_phBody_lblGoal");
  return {
    monthToDateKwh: numberFromHtmlById(html, "ctl00_phBody_ucDashboard_lblProgressTooltip1"),
    samePointLastMonthKwh: numberFromHtmlById(html, "ctl00_phBody_ucDashboard_lblLastMonth"),
    goalOverrunKwh: numberFromHtmlById(html, "ctl00_phBody_ucDashboard_lblGoalDelta"),
    energyClampBudgetKwh: budget,
    energyClampProjectedKwh: projected,
    energyClampLastBillingKwh: numberFromHtmlById(html, "ctl00_phBody_lblGoalLastMonth"),
    energyClampAverageBillingKwh: numberFromHtmlById(html, "ctl00_phBody_lblGoalAvgMonth"),
  };
}

function parseMeters(html) {
  const meters = [];
  const optionRe = /<option\b([^>]*)>([\s\S]*?)<\/option>/gi;
  let match;
  while ((match = optionRe.exec(html))) {
    const attrs = match[1] || "";
    if (!/selected/i.test(attrs) && !/(parent-option|sub-option)/i.test(attrs)) {
      continue;
    }
    const value = /value=["']?([^"'\s>]+)/i.exec(attrs);
    if (!value) {
      continue;
    }
    meters.push({
      id: value[1],
      name: stripTags(match[2]),
      kind: /sub-option/i.test(attrs) ? "submeter" : "meter",
      selected: /selected/i.test(attrs),
    });
  }
  return meters.filter((meter, index, all) => all.findIndex((item) => item.id === meter.id) === index);
}

function parseInstantWatts(html, existingRows) {
  // Alarm.com's live instant-power JSON endpoint is often empty. Keep existing rows
  // unless the rendered table becomes parseable enough to replace them confidently.
  return Array.isArray(existingRows) ? existingRows : [];
}

function dateKeyFromMs(ms) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: LOCAL_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(ms));
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

function localIsoNow() {
  const date = new Date();
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: LOCAL_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "shortOffset",
  });
  const parts = formatter.formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const offsetMatch = String(map.timeZoneName || "").match(/GMT([+-])(\d{1,2})(?::(\d{2}))?/);
  const offsetHours = offsetMatch ? Number(offsetMatch[2]) : 7;
  const offsetMinutes = offsetMatch && offsetMatch[3] ? Number(offsetMatch[3]) : 0;
  const offsetSign = offsetMatch ? offsetMatch[1] : "-";
  const absHours = String(offsetHours).padStart(2, "0");
  const absMinutes = String(offsetMinutes).padStart(2, "0");
  return `${map.year}-${map.month}-${map.day}T${map.hour}:${map.minute}:${map.second}${offsetSign}${absHours}:${absMinutes}`;
}

function parseAdcMs(raw) {
  const match = /\d+/.exec(String(raw || ""));
  return match ? Number(match[0]) : null;
}

async function fetchText(url, auth, options = {}) {
  const res = await fetch(url, {
    method: "GET",
    redirect: "manual",
    headers: {
      Cookie: auth.cookie,
      "User-Agent": "Mozilla/5.0 SmartHomeMonitor/1.0",
      Referer: options.referer || BASE,
      ajaxrequestuniquekey: auth.ajaxKey,
      Accept: options.accept || "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    },
  });
  const text = await res.text();
  return {
    url,
    status: res.status,
    contentType: res.headers.get("content-type") || "",
    location: res.headers.get("location") || "",
    text,
  };
}

async function fetchJson(url, auth, options = {}) {
  const res = await fetchText(url, auth, {
    ...options,
    accept: options.accept || "application/vnd.api+json,application/json,*/*",
  });
  if (res.status < 200 || res.status >= 300 || !res.text.trim()) {
    return { ok: false, status: res.status, body: null };
  }
  try {
    return { ok: true, status: res.status, body: JSON.parse(res.text) };
  } catch {
    return { ok: false, status: res.status, body: null };
  }
}

async function fetchBinary(url, auth, options = {}) {
  const res = await fetch(url, {
    method: "GET",
    redirect: "manual",
    headers: {
      Cookie: auth.cookie,
      "User-Agent": "Mozilla/5.0 SmartHomeMonitor/1.0",
      Referer: options.referer || BASE,
      ajaxrequestuniquekey: auth.ajaxKey,
      Accept: options.accept || "*/*",
    },
  });
  return {
    url,
    status: res.status,
    contentType: res.headers.get("content-type") || "",
    location: res.headers.get("location") || "",
    buffer: Buffer.from(await res.arrayBuffer()),
  };
}

async function fetchEnergyRange(auth, meterIds, range) {
  const url = new URL(`${BASE}/web/Energy/EnergyData.ashx`);
  url.searchParams.set("end", String(Date.now()));
  url.searchParams.set("range", range);
  url.searchParams.set("meter", meterIds.join(","));
  url.searchParams.set("res", range.slice(-1));
  url.searchParams.set("units", "0");
  const res = await fetchText(url.toString(), auth, { referer: ENERGY_URL, accept: "application/json,*/*" });
  if (res.status !== 200 || !res.text.trim()) {
    return [];
  }
  return JSON.parse(res.text);
}

function sumSeries(series) {
  return Math.round(
    (series || []).reduce((sum, point) => sum + Number(point.Item2 || 0), 0) * 1000
  ) / 1000;
}

function buildDailyRows(seriesByRange, dashboard) {
  const currentValue = Number(dashboard.monthToDateKwh || 0);
  const rows = [];
  const source = seriesByRange["21d"] || [];
  const parent = source.find((item) => item.deviceDesc === "Energy Clamp") || source[0];
  const points = parent ? parent.InternalData || [] : [];
  let best = [];
  let bestDelta = Infinity;
  for (let start = 0; start < points.length; start += 1) {
    const total = points.slice(start).reduce((sum, point) => sum + Number(point.Item2 || 0), 0);
    const delta = Math.abs(total - currentValue);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = points.slice(start).map((point) => dateKeyFromMs(parseAdcMs(point.Item1)));
    }
  }
  const keepDates = new Set(best);
  for (const device of source) {
    for (const point of device.InternalData || []) {
      const ms = parseAdcMs(point.Item1);
      const date = dateKeyFromMs(ms);
      if (keepDates.has(date)) {
        rows.push({ date, meter: device.deviceDesc || device.label, kwh: Number(Number(point.Item2 || 0).toFixed(3)) });
      }
    }
  }
  return {
    rows,
    startDate: best[0] || null,
    dashboardDeltaKwh: bestDelta === Infinity ? null : Number(bestDelta.toFixed(3)),
  };
}

async function captureEnergy(auth, existingAlarm) {
  const page = await fetchText(ENERGY_URL, auth, { referer: `${BASE}/web/system/home` });
  const dashboard = parseDashboard(page.text);
  const meters = parseMeters(page.text);
  const meterIds = meters.length ? meters.map((meter) => meter.id) : ["1207", "1208", "1226"];
  const seriesByRange = {};
  for (const range of RANGES) {
    seriesByRange[range] = await fetchEnergyRange(auth, meterIds, range);
  }
  const daily = buildDailyRows(seriesByRange, dashboard);
  const periodKwh = [];
  for (const range of RANGES) {
    for (const device of seriesByRange[range] || []) {
      periodKwh.push({
        period: range,
        meter: device.deviceDesc || device.label,
        kwh: sumSeries(device.InternalData),
      });
    }
  }
  const payload = {
    capturedFrom: ENERGY_URL,
    capturedAtLocal: localIsoNow(),
    timeZoneAssumption:
      existingAlarm.timeZoneAssumption ||
      "Alarm.com Power Use Now timestamps are displayed as UTC and converted to America/Los_Angeles for pairing.",
    billingCycleDailyStartDate: daily.startDate,
    dashboard,
    instantWatts: parseInstantWatts(page.text, existingAlarm.instantWatts),
    dailyKwh: daily.rows,
    periodKwh,
  };
  return {
    page: {
      url: ENERGY_URL,
      status: page.status,
      bytes: page.text.length,
      title: extractTitle(page.text),
    },
    meters,
    dashboardDeltaKwh: daily.dashboardDeltaKwh,
    readings: payload,
  };
}

function extractTitle(html) {
  const match = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html || "");
  return match ? stripTags(match[1]) : "";
}

function normalizeUrl(raw, fromUrl) {
  const href = decodeHtml(raw || "").trim();
  if (!href || href.startsWith("#") || /^javascript:/i.test(href) || /^mailto:/i.test(href) || /^tel:/i.test(href)) {
    return null;
  }
  let url;
  try {
    url = new URL(href, fromUrl || BASE);
  } catch {
    return null;
  }
  if (url.hostname !== "www.alarm.com") {
    return null;
  }
  url.hash = "";
  if (!url.pathname.toLowerCase().startsWith("/web/")) {
    return null;
  }
  const text = `${url.pathname}?${url.searchParams.toString()}`.toLowerCase();
  if (/\.(?:css|js|png|gif|jpg|jpeg|svg|ico|woff2?|map)(?:$|\?)/.test(text)) {
    return null;
  }
  if (/logout|signout|delete|remove|armdisarm|arm|disarm|lock|unlock|open|close|bypass|command|ajax/.test(text)) {
    return null;
  }
  return url.toString();
}

function extractLinks(html, fromUrl) {
  const found = [];
  const linkRe = /\bhref=["']([^"']+)["']/gi;
  let match;
  while ((match = linkRe.exec(html || ""))) {
    const url = normalizeUrl(match[1], fromUrl);
    if (url) {
      found.push(url);
    }
  }
  return [...new Set(found)];
}

function extractForms(html) {
  const forms = [];
  const formRe = /<form\b([^>]*)>([\s\S]*?)<\/form>/gi;
  let match;
  while ((match = formRe.exec(html || ""))) {
    const attrs = match[1] || "";
    const method = (/method=["']?([^"'\s>]+)/i.exec(attrs) || [null, "GET"])[1].toUpperCase();
    const inputs = [...match[2].matchAll(/<(?:input|select|textarea)\b[^>]*(?:name|id)=["']?([^"'\s>]+)/gi)].map((item) => item[1]);
    forms.push({ method, fieldCount: inputs.length, fields: inputs.slice(0, 20) });
  }
  return forms.slice(0, 8);
}

function sanitizePortalUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return rawUrl;
  }
}

function classifyPage(url, html, contentType) {
  const text = stripTags(html).toLowerCase();
  const pathName = new URL(url).pathname.toLowerCase();
  const tags = [];
  for (const [tag, patterns] of Object.entries({
    energy: ["energy", "kwh", "power use"],
    security: ["security", "arming", "sensor", "partition"],
    video: ["video", "camera", "clip"],
    automation: ["automation", "device", "scene", "rule"],
    users: ["user", "login", "access code"],
    notifications: ["notification", "alert", "recipient"],
    billing: ["billing", "invoice", "payment"],
    settings: ["setting", "profile", "account"],
  })) {
    if (patterns.some((pattern) => text.includes(pattern) || pathName.includes(pattern))) {
      tags.push(tag);
    }
  }
  return {
    title: extractTitle(html),
    tags,
    contentType,
    bytes: html.length,
    forms: extractForms(html),
    linkCount: extractLinks(html, url).length,
  };
}

async function crawlPortal(auth, seeds) {
  const queue = [...new Set(seeds)];
  const seen = new Set();
  const pages = [];
  while (queue.length && pages.length < MAX_PAGES) {
    const url = queue.shift();
    if (!url || seen.has(url)) {
      continue;
    }
    seen.add(url);
    try {
      const res = await fetchText(url, auth);
      const redirectedToLogin = /\/login/i.test(res.location) || /txtUserName|loginform/i.test(res.text);
      pages.push({
        url: sanitizePortalUrl(url),
        status: res.status,
        redirectedToLogin,
        ...classifyPage(url, res.text, res.contentType),
      });
      if (res.status === 200 && !redirectedToLogin && /text\/html/i.test(res.contentType)) {
        for (const link of extractLinks(res.text, url)) {
          if (!seen.has(link) && queue.length + pages.length < MAX_PAGES * 2) {
            queue.push(link);
          }
        }
      }
    } catch (error) {
      pages.push({ url, status: 0, error: String(error.message || error) });
    }
  }
  return pages;
}

function writeJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2) + "\n");
}

function loadExistingAlarm(root) {
  const filePath = path.join(root, "config/alarm_energy_readings.json");
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return {};
  }
}

function loadPreviousCapture(root) {
  const filePath = path.join(root, "data/latest_alarm_com.json");
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return {};
  }
}

function loadActivityAuditFallback(root) {
  const filePath = path.join(root, "data/alarm_com_media_activity_audit.json");
  try {
    const audit = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!audit || !Number.isFinite(audit.totalEvents) || audit.totalEvents <= 0) {
      return null;
    }
    const mediaRecent = audit.mediaEvents?.recent || [];
    const sensorRecent = audit.sensorEvents?.recent || [];
    const validationTargetTrips = sensorRecent.filter(isMediaValidationTargetEvent);
    const recent = [...mediaRecent, ...validationTargetTrips]
      .sort((left, right) => String(right.localTime || "").localeCompare(String(left.localTime || "")))
      .slice(0, 50);
    return {
      ok: true,
      stale: true,
      status: audit.status,
      source: "Alarm.com media/activity audit",
      generatedAt: audit.generatedAt,
      totalEvents: audit.totalEvents,
      latestEventAt: recent[0]?.localTime || mediaRecent[0]?.localTime || audit.generatedAt || null,
      recent,
      byDay: audit.sensorEvents?.byDay || audit.mediaEvents?.byDay || [],
      byDevice: audit.sensorEvents?.byDevice || audit.mediaEvents?.byDevice || [],
      byDescription: audit.sensorEvents?.byDescription || audit.mediaEvents?.byDescription || [],
      mediaTriggerHealth: {
        ok: true,
        totalEvents: audit.totalEvents,
        tripLikeSensorEvents: audit.sensorEvents?.tripLikeCount || 0,
        validationTargets: ["Entry Door", "Sideyard Gate"],
        validationTargetTripEvents: validationTargetTrips.length,
        latestValidationTargetTripAt: validationTargetTrips[0]?.localTime || null,
        mediaEvents: audit.mediaEvents?.count || 0,
        postDisarmMediaEvents: audit.mediaEvents?.byDescription?.find((item) => /post-disarm/i.test(item.name || ""))?.count || 0,
        sensorTriggeredMediaEvents: Math.max(0, (audit.mediaEvents?.count || 0) - (audit.mediaEvents?.byDescription?.find((item) => /post-disarm/i.test(item.name || ""))?.count || 0)),
        mediaByDay: audit.mediaEvents?.byDay || [],
        sensorTripsByDay: audit.sensorEvents?.byDay || [],
        validationTargetTripsByDay: topCounts(countBy(validationTargetTrips, (event) => String(event.localTime).slice(0, 10)), 14),
        mediaByDescription: audit.mediaEvents?.byDescription || [],
        mediaByDevice: audit.mediaEvents?.byDevice || [],
        recentValidationTargetTrips: validationTargetTrips.slice(0, 12),
        recentMedia: mediaRecent,
      },
    };
  } catch {
    return null;
  }
}

function selectActivityFallback(root, previousCapture) {
  const previousActivity = previousCapture.activity || null;
  const auditActivity = loadActivityAuditFallback(root);
  const previousGenerated = Date.parse(previousActivity?.generatedAt || previousActivity?.refreshFailedAt || 0);
  const auditGenerated = Date.parse(auditActivity?.generatedAt || 0);
  if (auditActivity && (!previousActivity || !Number.isFinite(previousGenerated) || auditGenerated >= previousGenerated)) {
    return auditActivity;
  }
  if (previousActivity?.ok || (previousActivity?.recent || []).length) {
    return { ...previousActivity, source: previousActivity.source || "cached activity history" };
  }
  return auditActivity;
}

function writeAlarmReadings(root, readings) {
  writeJson(path.join(root, "config/alarm_energy_readings.json"), readings);
  const src = sourceRootDir();
  if (path.resolve(root) !== path.resolve(src) && fs.existsSync(src)) {
    writeJson(path.join(src, "config/alarm_energy_readings.json"), readings);
  }
}

function summarizePages(pages) {
  const tags = {};
  for (const page of pages) {
    for (const tag of page.tags || ["uncategorized"]) {
      tags[tag] = (tags[tag] || 0) + 1;
    }
  }
  return {
    crawled: pages.length,
    ok: pages.filter((page) => page.status === 200 && !page.redirectedToLogin).length,
    redirectedToLogin: pages.filter((page) => page.redirectedToLogin).length,
    byTag: tags,
  };
}

function pickAttributes(attributes, keys) {
  const out = {};
  for (const key of keys) {
    if (attributes && Object.prototype.hasOwnProperty.call(attributes, key)) {
      out[key] = attributes[key];
    }
  }
  return out;
}

function stateName(group, value) {
  if (value === undefined || value === null) {
    return undefined;
  }
  return STATE_NAMES[group]?.[value] || String(value);
}

function sanitizeActivityDescription(raw) {
  return String(raw || "")
    .replace(/\s+\([^)]+\)\s*$/g, "")
    .replace(/\b(Disarmed|Armed(?: Stay| Away| Night)?) by .+$/i, "$1")
    .replace(/\b(?:email|phone|contactId|login)=([^&\s]+)/gi, "$1=<redacted>");
}

function summarizeDevice(device, kind) {
  const attrs = device.attributes || {};
  const common = pickAttributes(attrs, [
    "description",
    "deviceType",
    "deviceRole",
    "state",
    "desiredState",
    "displayStateText",
    "openClosedStatus",
    "isBypassed",
    "isMonitoringEnabled",
    "isMalfunctioning",
    "batteryLevelClassification",
    "batteryLevelNull",
    "canReceiveCommands",
    "remoteCommandsEnabled",
    "hasPermissionToChangeState",
    "manufacturer",
    "managedDeviceType",
  ]);
  common.stateText = attrs.displayStateText || stateName(kind, attrs.state);
  if (kind === "thermostats") {
    Object.assign(
      common,
      pickAttributes(attrs, [
        "ambientTemp",
        "humidityLevel",
        "state",
        "inferredState",
        "fanMode",
        "heatSetpoint",
        "coolSetpoint",
        "scheduleMode",
        "hasRtsIssue",
      ])
    );
  }
  if (kind === "lights") {
    Object.assign(common, pickAttributes(attrs, ["isDimmer", "lightLevel", "stateTrackingEnabled"]));
  }
  if (kind === "locks") {
    Object.assign(common, pickAttributes(attrs, ["supportsTemporaryUserCodes", "supportsScheduledUserCodes"]));
  }
  if (kind === "remoteTemperatureSensors") {
    Object.assign(common, pickAttributes(attrs, ["ambientTemp", "humidityLevel", "isPaired", "tempForwardingActive", "supportsHumidity"]));
  }
  return {
    id: device.id,
    type: device.type,
    ...common,
  };
}

function summarizeSystemState(state) {
  const groups = ["partitions", "sensors", "lights", "locks", "garages", "thermostats"];
  const components = {};
  const issues = [];
  const relationshipCounts = {};
  for (const [name, rel] of Object.entries(state.relationships || {})) {
    const data = rel?.data;
    if (Array.isArray(data) && data.length) {
      relationshipCounts[name] = data.length;
    } else if (data && typeof data === "object" && data.id) {
      relationshipCounts[name] = 1;
    }
  }
  for (const group of groups) {
    components[group] = (state[group] || []).map((device) => summarizeDevice(device, group));
    for (const item of components[group]) {
      const problem =
        item.isMalfunctioning ||
        item.hasRtsIssue ||
        item.batteryLevelClassification === "Low" ||
        item.batteryLevelClassification === "Critical" ||
        item.displayStateText === "Malfunction" ||
        item.state === "Malfunction";
      if (problem) {
        issues.push({
          group,
          id: item.id,
          description: item.description,
          state: item.stateText || item.displayStateText || item.state || item.openClosedStatus,
          batteryLevelClassification: item.batteryLevelClassification,
        });
      }
    }
  }
  return {
    id: state.id,
    attributes: pickAttributes(state.attributes || {}, [
      "description",
      "systemGroupName",
      "hasPartitionsArmed",
      "hasPartitionsInAlarmAtPanel",
      "primaryPartitionId",
      "noDisarmWhenClearingAlarms",
    ]),
    counts: Object.fromEntries(groups.map((group) => [group, components[group].length])),
    relationshipCounts,
    issues,
    components,
  };
}

function idsFromRelationship(state, name) {
  return (state.relationships?.[name]?.data || []).map((item) => item.id).filter(Boolean);
}

function idsQuery(ids) {
  return ids.map((id) => `ids%5B%5D=${encodeURIComponent(id)}`).join("&");
}

async function fetchRemoteTemperatureSensors(auth, state) {
  const ids = idsFromRelationship(state, "remoteTemperatureSensors");
  if (!ids.length) {
    return [];
  }
  const result = await fetchJson(`${BASE}/web/api/devices/remoteTemperatureSensors/?${idsQuery(ids)}`, auth);
  if (!result.ok || !Array.isArray(result.body?.data)) {
    return [];
  }
  return result.body.data.map((device) => summarizeDevice(device, "remoteTemperatureSensors"));
}

async function fetchScenes(auth, state) {
  const ids = idsFromRelationship(state, "scenes");
  if (!ids.length) {
    return [];
  }
  const result = await fetchJson(`${BASE}/web/api/automation/scenes?${idsQuery(ids)}`, auth);
  const scenes = Array.isArray(result.body?.value) ? result.body.value : [];
  return scenes.map((scene) => ({
    id: scene.id,
    name: scene.name,
    canBeExecuted: scene.canBeExecuted,
    canBeEdited: scene.canBeEdited,
    sortOrder: scene.sortOrder,
    actionSetType: scene.actionSetType,
  }));
}

function localDateTime(raw) {
  if (!raw) {
    return "";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return String(raw);
  }
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: LOCAL_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day} ${map.hour}:${map.minute}:${map.second}`;
}

function localPartsInTimeZone(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

function zonedLocalDateToUtcDate(year, month, day, hour, minute, second) {
  const desiredUtc = Date.UTC(year, month - 1, day, hour, minute, second);
  const initial = new Date(desiredUtc);
  const rendered = localPartsInTimeZone(initial, LOCAL_TZ);
  const renderedUtc = Date.UTC(
    Number(rendered.year),
    Number(rendered.month) - 1,
    Number(rendered.day),
    Number(rendered.hour),
    Number(rendered.minute),
    Number(rendered.second)
  );
  return new Date(desiredUtc + (desiredUtc - renderedUtc));
}

function parseAlarmLocalDate(raw) {
  const match = String(raw || "")
    .trim()
    .match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([ap])m$/i);
  if (!match) {
    const fallback = new Date(raw);
    return Number.isNaN(fallback.getTime()) ? null : fallback;
  }
  let hour = Number(match[4]);
  const ampm = match[7].toLowerCase();
  if (ampm === "p" && hour < 12) hour += 12;
  if (ampm === "a" && hour === 12) hour = 0;
  return zonedLocalDateToUtcDate(
    Number(match[3]),
    Number(match[1]),
    Number(match[2]),
    hour,
    Number(match[5]),
    Number(match[6] || 0)
  );
}

function countBy(items, keyFn) {
  const counts = {};
  for (const item of items) {
    const key = keyFn(item) || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function topCounts(counts, limit = 10) {
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([name, count]) => ({ name, count }));
}

function isMediaEvent(event) {
  return /camera|image|video|clip|record|snapshot|uploaded|upload/i.test(
    `${event.deviceDescription || ""} ${event.description || ""} ${JSON.stringify(event.deviceTypeFilter || [])}`
  );
}

function isPostDisarmMediaEvent(event) {
  return /post-disarm|disarm image|disarm images/i.test(`${event.description || ""}`);
}

function isSensorTripLikeEvent(event) {
  return (
    /Activated|Opened|Wet|Alarm|Tamper/i.test(`${event.description || ""}`) ||
    /motion|door|window|slider|water|sensor/i.test(`${event.deviceDescription || ""}`)
  );
}

function isMediaValidationTargetEvent(event) {
  return (
    /entry door|sideyard gate/i.test(`${event.deviceDescription || ""}`) &&
    /Activated|Opened|Alarm|Tamper/i.test(`${event.description || ""}`)
  );
}

function mediaTriggerHealth(events) {
  const media = events.filter(isMediaEvent);
  const sensorTrips = events.filter(isSensorTripLikeEvent);
  const validationTargetTrips = events.filter(isMediaValidationTargetEvent);
  const postDisarm = media.filter(isPostDisarmMediaEvent);
  const sensorTriggeredMedia = media.filter((event) => !isPostDisarmMediaEvent(event));
  return {
    ok: true,
    totalEvents: events.length,
    tripLikeSensorEvents: sensorTrips.length,
    validationTargets: ["Entry Door", "Sideyard Gate"],
    validationTargetTripEvents: validationTargetTrips.length,
    latestValidationTargetTripAt: validationTargetTrips[0]?.localTime || null,
    mediaEvents: media.length,
    postDisarmMediaEvents: postDisarm.length,
    sensorTriggeredMediaEvents: sensorTriggeredMedia.length,
    mediaByDay: topCounts(countBy(media, (event) => String(event.localTime).slice(0, 10)), 14),
    sensorTripsByDay: topCounts(countBy(sensorTrips, (event) => String(event.localTime).slice(0, 10)), 14),
    validationTargetTripsByDay: topCounts(countBy(validationTargetTrips, (event) => String(event.localTime).slice(0, 10)), 14),
    mediaByDescription: topCounts(countBy(media, (event) => event.description), 12),
    mediaByDevice: topCounts(countBy(media, (event) => event.deviceDescription), 12),
    recentValidationTargetTrips: validationTargetTrips.slice(0, 12),
    recentMedia: media.slice(0, 12),
  };
}

function findAlarmDevice(payload, name) {
  return flattenDevices(payload.alarmState).find((item) => item.name === name) || null;
}

function buildGateValidation(payload, alarmHardware) {
  const hardware = (alarmHardware || []).filter((item) => /flex io|gate/i.test(`${item.name || ""} ${item.purpose || ""}`));
  const sideyardGate = findAlarmDevice(payload, "Sideyard Gate");
  const sideyardRule = (payload.videoRules?.rules || []).find((rule) => rule.name === "Sideyard Gate Video") || null;
  const media = payload.activity?.mediaTriggerHealth || null;
  const sideyardTrips = (media?.recentValidationTargetTrips || []).filter((event) =>
    /sideyard gate/i.test(`${event.deviceDescription || ""}`)
  );
  const sideyardMedia = (media?.recentMedia || []).filter((event) => /sideyard/i.test(`${event.deviceDescription || ""} ${event.description || ""}`));
  const gateCameraTrouble = (payload.troubleConditions?.rows || []).filter((item) =>
    /sideyard|backyard/i.test(`${item.description || ""} ${item.emberDeviceId || ""}`)
  );
  const blockers = [];
  if (!hardware.length) blockers.push("Flex IO / gate-control hardware not recorded in config");
  if (!sideyardGate) blockers.push("Sideyard Gate device not visible in Alarm.com state");
  if (!sideyardRule) blockers.push("Sideyard Gate Video rule not found");
  if (sideyardRule?.isPaused) blockers.push("Sideyard Gate Video rule is paused");
  if (!payload.activity?.ok) blockers.push(`Activity history unavailable (${payload.activity?.status || "n/a"})`);

  const canValidateEvents = payload.activity?.ok && media?.ok;
  const currentGateState = String(sideyardGate?.state || "").toLowerCase();
  const eventStatus = canValidateEvents
    ? sideyardTrips.length
      ? sideyardMedia.length
        ? "validated"
        : "trip_seen_no_sideyard_media_seen"
      : "no_recent_sideyard_trip"
    : "blocked";
  return {
    generatedAt: payload.generatedAt,
    hardwarePresent: hardware.length > 0,
    hardware,
    device: sideyardGate,
    videoRule: sideyardRule
      ? {
          name: sideyardRule.name,
          isPaused: sideyardRule.isPaused,
          trigger: sideyardRule.trigger,
          action: sideyardRule.action,
          timeframe: sideyardRule.timeframe,
        }
      : null,
    activityAvailable: Boolean(payload.activity?.ok),
    status: blockers.length ? "attention" : eventStatus,
    blockers,
    latestSideyardTripAt: sideyardTrips[0]?.localTime || null,
    recentSideyardTrips: sideyardTrips.slice(0, 8),
    recentSideyardMedia: sideyardMedia.slice(0, 8),
    diagnosis: gateCameraTrouble.length
      ? `Alarm.com reports camera trouble for ${gateCameraTrouble.map((item) => item.description).join("; ")}.`
      : currentGateState === "open"
        ? "Sideyard Gate is currently Open; another open test will not create a fresh Alarm.com open event until Alarm.com first sees it Closed."
        : null,
    cameraTrouble: gateCameraTrouble,
  };
}

function flattenDevices(alarmState) {
  const systems = alarmState?.systems || [];
  const rows = [];
  for (const system of systems) {
    for (const [group, items] of Object.entries(system.components || {})) {
      if (!Array.isArray(items)) {
        continue;
      }
      for (const item of items) {
        rows.push({
          group,
          id: item.id,
          type: item.type,
          name: item.description || item.name || item.id,
          state: item.stateText || item.displayStateText || stateName(group, item.state) || "",
          rawState: item.state,
          desiredState: item.desiredState,
          isBypassed: item.isBypassed,
          isMonitoringEnabled: item.isMonitoringEnabled,
          isMalfunctioning: item.isMalfunctioning,
          batteryLevelClassification: item.batteryLevelClassification,
          remoteCommandsEnabled: item.remoteCommandsEnabled,
          lightLevel: item.lightLevel,
          ambientTemp: item.ambientTemp,
          humidityLevel: item.humidityLevel,
        });
      }
    }
  }
  return rows.sort((a, b) => `${a.group}:${a.name}:${a.id}`.localeCompare(`${b.group}:${b.name}:${b.id}`));
}

function deriveChanges(previous, current) {
  const previousDevices = new Map(flattenDevices(previous.alarmState).map((item) => [item.id, item]));
  const currentDevices = flattenDevices(current.alarmState);
  const deviceTransitions = [];
  for (const item of currentDevices) {
    const prior = previousDevices.get(item.id);
    if (!prior) {
      deviceTransitions.push({ type: "new_device", group: item.group, id: item.id, name: item.name, state: item.state });
      continue;
    }
    const keys = ["state", "desiredState", "isBypassed", "isMonitoringEnabled", "isMalfunctioning", "batteryLevelClassification", "lightLevel", "ambientTemp", "humidityLevel"];
    for (const key of keys) {
      if ((prior[key] ?? null) !== (item[key] ?? null)) {
        deviceTransitions.push({
          type: "device_change",
          group: item.group,
          id: item.id,
          name: item.name,
          field: key,
          from: prior[key] ?? null,
          to: item[key] ?? null,
        });
      }
    }
  }

  const previousEvents = new Set(((previous.activity || {}).recent || []).map((event) => event.id).filter(Boolean));
  const newActivity = ((current.activity || {}).recent || []).filter((event) => event.id && !previousEvents.has(event.id));
  return {
    generatedAt: current.generatedAt,
    deviceTransitions: deviceTransitions.slice(0, 50),
    newActivity: newActivity.slice(0, 50),
  };
}

function summarizeHistoryEvent(event) {
  return {
    eventDate: event.eventDate || event.date,
    localTime: localDateTime(event.eventDate || event.date),
    deviceDescription: event.deviceDescription || "",
    globalDeviceId: event.globalDeviceId || "",
    description: sanitizeActivityDescription(event.description || event.eventTypeName || ""),
    eventType: event.eventType,
    deviceTypeFilter: event.deviceTypeFilter || [],
    id: event.id,
  };
}

function activityResultFromEvents(events, options = {}) {
  return {
    ok: true,
    stale: false,
    refreshOk: true,
    status: options.status || 200,
    source: options.source || "historyEvents",
    endpointError: options.endpointError || null,
    totalEvents: events.length,
    latestEventAt: events[0]?.eventDate || null,
    recent: events.slice(0, 50),
    byDay: topCounts(countBy(events, (event) => String(event.localTime).slice(0, 10)), 14),
    byDevice: topCounts(countBy(events, (event) => event.deviceDescription), 12),
    byDescription: topCounts(countBy(events, (event) => event.description), 12),
    mediaTriggerHealth: mediaTriggerHealth(events),
  };
}

function decodeActivityExport(buffer) {
  if (buffer[0] === 0xff && buffer[1] === 0xfe) {
    return buffer.subarray(2).toString("utf16le");
  }
  if (buffer[0] === 0xfe && buffer[1] === 0xff) {
    return Buffer.from(buffer.subarray(2)).swap16().toString("utf16le");
  }
  return buffer.toString("utf8");
}

function parseActivityExport(text) {
  const rows = String(text || "")
    .trim()
    .split(/\r?\n/)
    .map((line) => line.split("\t").map((field) => field.replace(/^"|"$/g, "").replace(/""/g, '"')));
  const header = rows.shift() || [];
  const indexes = {
    device: header.findIndex((field) => /^device$/i.test(field)),
    event: header.findIndex((field) => /^event$/i.test(field)),
    time: header.findIndex((field) => /^time$/i.test(field)),
  };
  if (Object.values(indexes).some((index) => index < 0)) {
    return [];
  }
  const events = [];
  for (const row of rows) {
    const deviceDescription = row[indexes.device] || "";
    const description = sanitizeActivityDescription(row[indexes.event] || "");
    const date = parseAlarmLocalDate(row[indexes.time] || "");
    if (!date) {
      continue;
    }
    const eventDate = date.toISOString();
    events.push({
      eventDate,
      localTime: localDateTime(eventDate),
      deviceDescription,
      globalDeviceId: "",
      description,
      eventType: null,
      deviceTypeFilter: [],
      id: `E-${Buffer.from(`${deviceDescription}|${description}|${eventDate}`).toString("base64url").slice(0, 64)}`,
    });
  }
  return events.sort((left, right) => String(right.eventDate).localeCompare(String(left.eventDate)));
}

async function fetchActivityHistoryExport(auth, endpointError) {
  const urlResult = await fetchJson(`${BASE}/web/api/activity/historyEvents/getExportToCsvUrl`, auth, {
    referer: `${BASE}/web/system/activity`,
  });
  const exportPath = urlResult.body?.value;
  if (!urlResult.ok || !exportPath) {
    return null;
  }
  const endDate = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  const startDate = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const queryParams = { page: 1, pageSize: 500, startDate, endDate };
  const exportUrl = new URL(exportPath, BASE);
  exportUrl.searchParams.set("filterParameters", JSON.stringify(queryParams));
  exportUrl.searchParams.set("startDate", JSON.stringify(startDate));
  exportUrl.searchParams.set("endDate", JSON.stringify(endDate));
  exportUrl.searchParams.set("exportFormat", "csv");
  const exportResult = await fetchBinary(exportUrl.toString(), auth, {
    referer: `${BASE}/web/system/activity`,
    accept: "text/csv,application/text,*/*",
  });
  if (exportResult.status !== 200 || !exportResult.buffer.length) {
    return null;
  }
  const events = parseActivityExport(decodeActivityExport(exportResult.buffer));
  if (!events.length) {
    return null;
  }
  return {
    ...activityResultFromEvents(events, {
      status: exportResult.status,
      source: "Alarm.com activity export",
      endpointError,
    }),
    exportContentType: exportResult.contentType,
  };
}

async function fetchActivityHistory(auth, fallbackActivity = null) {
  const result = await fetchJson(`${BASE}/web/api/activity/historyEvents`, auth);
  const endpointError = result.ok ? null : `historyEvents returned ${result.status}`;
  if (!result.ok) {
    const exportActivity = await fetchActivityHistoryExport(auth, endpointError);
    if (exportActivity) {
      return exportActivity;
    }
  }
  if (!result.ok && (fallbackActivity?.ok || (fallbackActivity?.recent || []).length)) {
    return {
      ...fallbackActivity,
      ok: true,
      stale: true,
      refreshOk: false,
      refreshStatus: result.status,
      refreshFailedAt: new Date().toISOString(),
      endpointError,
    };
  }
  const rawEvents = Array.isArray(result.body?.value) ? result.body.value : [];
  const events = rawEvents.map(summarizeHistoryEvent).filter((event) => event.eventDate);
  return activityResultFromEvents(events, { status: result.status, source: "historyEvents", endpointError });
}

async function fetchRecordingRules(auth) {
  const url = `${BASE}/web/api/automation/rules/rules?filter%5Bsearch%5D=&filter%5BdeviceType%5D=15&filter%5BfilterTags%5D%5B%5D=18`;
  const result = await fetchJson(url, auth);
  const rawRules = Array.isArray(result.body?.data)
    ? result.body.data
    : Array.isArray(result.body?.value)
      ? result.body.value
      : [];
  const rules = rawRules.map(summarizeAutomationRule);
  const names = new Set(rules.map((rule) => rule.name));
  return {
    ok: result.ok,
    status: result.status,
    checkedAt: new Date().toISOString(),
    expected: EXPECTED_VIDEO_RULES,
    ruleCount: rules.length,
    missingExpected: EXPECTED_VIDEO_RULES.filter((name) => !names.has(name)),
    pausedExpected: rules.filter((rule) => EXPECTED_VIDEO_RULES.includes(rule.name) && rule.isPaused).map((rule) => rule.name),
    rules,
  };
}

function cleanRuleText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function automationRuleCategory(rule) {
  if (rule.displayType === 8 || rule.id.startsWith("etr-")) {
    return "Video";
  }
  if (/Garage Door .*Garage Light|Garage Light/.test(rule.name) || /Garage Light/.test(rule.action)) {
    return "Garage Light";
  }
  if (/Panel Camera|Peek-In|Image Upload/i.test(rule.name)) {
    return "Panel Camera";
  }
  if (/Thermostat|Energy|Humidity|Smart Away|Fire Safety/i.test(`${rule.name} ${rule.action}`)) {
    return "Thermostat/Energy";
  }
  return "Automation";
}

function summarizeAutomationRule(rule) {
  const attrs = rule.attributes || rule;
  const query = attrs.editPageQueryParameters || {};
  const summary = {
    id: rule.id || attrs.id || query.selectedDisplay || query.SelectedDisplay || "",
    name: cleanRuleText(attrs.name),
    isPaused: Boolean(attrs.isPaused),
    canBeEdited: attrs.canBeEdited,
    canBePaused: attrs.canBePaused,
    trigger: cleanRuleText(attrs.triggerCondition?.description),
    action: cleanRuleText(attrs.action?.description),
    timeframe: cleanRuleText(attrs.timeframe?.description),
    followUpAction: cleanRuleText(attrs.followUpAction?.description) || null,
    followUpTriggerCondition: cleanRuleText(attrs.followUpTriggerCondition?.description) || null,
    toggleText: attrs.toggleText || null,
    displayType: attrs.displayType ?? null,
    editPage: attrs.editPage ?? null,
    eventType: query.EventType || query.eventType || null,
    warning: cleanRuleText(attrs.warningTextForRestrictedRule),
    disabledTooltip: cleanRuleText(attrs.tooltipForDisabledEditAndToggle),
  };
  summary.category = automationRuleCategory(summary);
  return summary;
}

async function fetchAutomationRules(auth) {
  const url = `${BASE}/web/api/automation/rules/rules?filter%5Bsearch%5D=`;
  const result = await fetchJson(url, auth, { referer: `${BASE}/web/system/automation/rules` });
  const rawRules = Array.isArray(result.body?.data)
    ? result.body.data
    : Array.isArray(result.body?.value)
      ? result.body.value
      : [];
  const rules = rawRules.map(summarizeAutomationRule);
  const names = new Set(rules.map((rule) => rule.name));
  const garageLightRules = rules.filter((rule) => rule.category === "Garage Light");
  return {
    ok: result.ok,
    status: result.status,
    checkedAt: new Date().toISOString(),
    tracked: TRACKED_AUTOMATION_RULES,
    ruleCount: rules.length,
    missingTracked: TRACKED_AUTOMATION_RULES.filter((name) => !names.has(name)),
    pausedTracked: rules.filter((rule) => TRACKED_AUTOMATION_RULES.includes(rule.name) && rule.isPaused).map((rule) => rule.name),
    garageLightRules,
    rules,
  };
}

function buildAutomationRuleReview(payload) {
  const rules = payload.automationRules?.rules || [];
  const activity = payload.activity || {};
  const byDescription = activity.byDescription || [];
  const mediaHealth = activity.mediaTriggerHealth || {};
  const ruleByName = new Map(rules.map((rule) => [rule.name, rule]));
  const garageDirect = (payload.automationRules?.garageLightRules || []).filter((rule) => !rule.isPaused);
  const garageDuplicate = ruleByName.get("When Garage Door is Opened, then Turn Lights On");
  const sensorSaver = ruleByName.get("Sensor Left Open Energy Saver");
  const garageVideo = ruleByName.get("Garage Door Video");
  const sideyardVideo = ruleByName.get("Sideyard Gate Video");
  const sideyardLightRule = rules.find(
    (rule) =>
      !rule.isPaused &&
      /Sideyard Gate/i.test(rule.trigger || "") &&
      /Turn ON Sideyard Light/i.test(rule.action || "")
  );
  const garageMediaCount = byDescription
    .filter((item) => /Garage Door Video/i.test(item.name))
    .reduce((sum, item) => sum + (Number(item.count) || 0), 0);
  const gateCameraTrouble = (payload.troubleConditions?.rows || []).filter((item) =>
    /Sideyard|Backyard/i.test(`${item.description || ""} ${item.detail || ""}`)
  );

  const rows = [];
  rows.push({
    area: "Garage light latency",
    status: garageDirect.length >= 3 ? "covered" : "attention",
    recommendation:
      garageDirect.length >= 3
        ? "Keep Alarm.com as the instant-on owner for garage-door open events; Home/Smart Home should only hold, restore, or turn off."
        : "Add direct Alarm.com garage-door-open to Garage Light 100% rules before relying on Home app latency.",
  });
  if (garageDuplicate) {
    rows.push({
      area: "Garage duplicate",
      status: garageDuplicate.isPaused ? "fixed" : "attention",
      recommendation: garageDuplicate.isPaused
        ? "Generic garage-open light rule is paused; the three named direct rules remain active."
        : "Pause the generic garage-open light rule to avoid duplicated light commands.",
    });
  }
  if (sensorSaver) {
    const isNoop = /0°F.*0°F|Change target temp or mode/i.test(sensorSaver.action || "");
    rows.push({
      area: "Sensor Left Open Energy Saver",
      status: sensorSaver.isPaused ? "fixed" : isNoop ? "attention" : "covered",
      recommendation: sensorSaver.isPaused
        ? "Paused because the active action was effectively a 0°F/0°F thermostat trim."
        : isNoop
          ? "Pause it or configure real heat/cool trims; as captured, it does not do useful work."
          : "Configured with a nonzero thermostat action.",
    });
  }
  rows.push({
    area: "Garage lock unlock",
    status: "candidate",
    recommendation:
      "Add Garage Lock unlocked -> Garage Light 100% in the Alarm.com rule wizard if the regular-rule builder exposes lock activity as a trigger.",
  });
  rows.push({
    area: "Sideyard gate lighting",
    status: sideyardLightRule ? "covered" : "candidate",
    recommendation: sideyardLightRule
      ? "Alarm.com now turns Sideyard Light on when Sideyard Gate opens, limited to after sunset with the captured follow-up off behavior."
      : "Add Sideyard Gate opened -> Sideyard Light on for fast safety lighting; keep off/restore behavior outside Alarm.com unless a hold condition is explicit.",
  });
  if (garageVideo) {
    rows.push({
      area: "Garage Door Video",
      status: garageMediaCount > 0 ? "covered" : "verify",
      recommendation:
        garageMediaCount > 0
          ? "Recent activity includes Garage Door Video clips."
          : "Rule is active but recent activity did not show Garage Door Video media; run a controlled open test before adding close-video rules.",
    });
  }
  if (sideyardVideo) {
    rows.push({
      area: "Sideyard Gate Video",
      status: gateCameraTrouble.length ? "blocked" : mediaHealth.latestValidationTargetTripAt ? "verify" : "covered",
      recommendation: gateCameraTrouble.length
        ? "Fix Sideyard/Backyard camera trouble before relying on gate video validation."
        : "Close/open the gate once after the portal shows Closed to verify fresh Sideyard Gate video media.",
    });
  }
  return rows;
}

function summarizeTroubleCondition(item) {
  const attrs = item.attributes || item;
  const detail = String(attrs.extraData?.description || attrs.description || "")
    .replace(/\s+/g, " ")
    .trim();
  return {
    id: attrs.id || item.id || "",
    description: attrs.description || "",
    deviceId: attrs.deviceId ?? attrs.extraData?.deviceId ?? null,
    emberDeviceId: attrs.emberDeviceId || "",
    severity: attrs.severity ?? null,
    troubleConditionType: attrs.troubleConditionType ?? null,
    detail: detail.slice(0, 500),
  };
}

async function fetchTroubleConditions(auth) {
  const result = await fetchJson(`${BASE}/web/api/troubleConditions/troubleConditions?forceRefresh=true`, auth);
  const rawRows = Array.isArray(result.body?.data)
    ? result.body.data
    : Array.isArray(result.body?.value)
      ? result.body.value
      : [];
  const rows = result.ok ? rawRows.map(summarizeTroubleCondition) : [];
  return {
    ok: result.ok,
    status: result.status,
    checkedAt: new Date().toISOString(),
    count: rows.length,
    rows,
  };
}

async function captureSystemStates(alarm, auth) {
  const systems = [];
  for (const systemId of auth.systems || []) {
    const state = await alarm.getCurrentState(systemId, auth);
    const summary = summarizeSystemState(state);
    summary.components.remoteTemperatureSensors = await fetchRemoteTemperatureSensors(auth, state);
    summary.components.scenes = await fetchScenes(auth, state);
    summary.counts.remoteTemperatureSensors = summary.components.remoteTemperatureSensors.length;
    summary.counts.scenes = summary.components.scenes.length;
    systems.push(summary);
  }
  return {
    ok: true,
    systems,
    counts: systems.reduce((acc, system) => {
      for (const [key, value] of Object.entries(system.counts || {})) {
        acc[key] = (acc[key] || 0) + value;
      }
      return acc;
    }, {}),
    relationshipCounts: systems.reduce((acc, system) => {
      for (const [key, value] of Object.entries(system.relationshipCounts || {})) {
        acc[key] = (acc[key] || 0) + value;
      }
      return acc;
    }, {}),
    issues: systems.flatMap((system) => system.issues || []),
  };
}

async function checkWebsocketToken(alarm, auth) {
  try {
    const token = await alarm.getWebSocketToken(auth);
    return {
      ok: Boolean(token && token.value && token.endpoint),
      endpointHost: token?.endpoint ? new URL(token.endpoint).host : null,
      hasErrors: Boolean(
        (Array.isArray(token?.errors) && token.errors.length) ||
          (Array.isArray(token?.validationErrors) && token.validationErrors.length) ||
          (Array.isArray(token?.processingErrors) && token.processingErrors.length)
      ),
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error.message || error).slice(0, 300),
    };
  }
}

function table(lines, headers, rows) {
  lines.push("| " + headers.join(" | ") + " |");
  lines.push("|" + headers.map(() => "---").join("|") + "|");
  if (!rows.length) {
    lines.push("| " + headers.map((_, index) => (index === 0 ? "none" : "")).join(" | ") + " |");
    return;
  }
  for (const row of rows) {
    lines.push("| " + row.map((value) => String(value ?? "n/a").replace(/\|/g, "/")).join(" | ") + " |");
  }
}

function addDeviceTables(lines, alarmState) {
  const systems = alarmState.systems || [];
  const all = (group) => systems.flatMap((system) => system.components?.[group] || []);
  const relationshipCounts = alarmState.relationshipCounts || {};
  lines.push(`- Fetched counts: \`${JSON.stringify(alarmState.counts)}\``);
  lines.push(`- Full relationship inventory: \`${JSON.stringify(relationshipCounts)}\``);
  lines.push(`- Issues: \`${alarmState.issues.length}\``);

  const partitions = all("partitions");
  const sensors = all("sensors");
  const openSensors = sensors.filter((item) => ["Open", "Active", "Activated", "Wet"].includes(item.stateText));
  const bypassedSensors = sensors.filter((item) => item.isBypassed);
  const lights = all("lights");
  const locks = all("locks");
  const garages = all("garages");
  const thermostats = all("thermostats");
  const remoteTemps = all("remoteTemperatureSensors");
  const scenes = all("scenes");

  lines.push("", "### Security", "");
  table(lines, ["Device", "State", "Bypassed", "Monitoring"], [
    ...partitions.map((item) => [item.description, item.stateText, item.isBypassed ?? "", item.isMonitoringEnabled ?? ""]),
    ...openSensors.map((item) => [item.description, item.stateText, item.isBypassed, item.isMonitoringEnabled]),
  ]);
  lines.push(`- Open/active/wet sensors: \`${openSensors.length}\``);
  lines.push(`- Bypassed sensors: \`${bypassedSensors.length}\``);

  lines.push("", "### Sensor Inventory", "");
  table(
    lines,
    ["Device", "State", "Type", "Bypassed", "Monitoring"],
    sensors.map((item) => [item.description, item.stateText, item.deviceType, item.isBypassed, item.isMonitoringEnabled])
  );

  lines.push("", "### Access", "");
  table(lines, ["Device", "State", "Remote commands"], [
    ...locks.map((item) => [item.description, item.stateText, item.remoteCommandsEnabled]),
    ...garages.map((item) => [item.description, item.stateText, item.remoteCommandsEnabled]),
  ]);

  lines.push("", "### Lights", "");
  table(
    lines,
    ["State", "Count", "Examples"],
    Object.entries(
      lights.reduce((acc, item) => {
        const key = item.stateText || "Unknown";
        acc[key] = acc[key] || [];
        acc[key].push(item.description);
        return acc;
      }, {})
    ).map(([state, names]) => [state, names.length, names.slice(0, 6).join(", ")])
  );
  table(
    lines,
    ["Light", "State", "Level", "Tracking"],
    lights.map((item) => [item.description, item.stateText, item.lightLevel, item.stateTrackingEnabled])
  );

  lines.push("", "### HVAC", "");
  table(lines, ["Device", "State", "Temp", "Humidity", "Fan"], [
    ...thermostats.map((item) => [item.description, item.stateText, item.ambientTemp, item.humidityLevel, item.fanMode]),
    ...remoteTemps.map((item) => [item.description, item.stateText, item.ambientTemp, item.humidityLevel, ""]),
  ]);
  if ((relationshipCounts.remoteTemperatureSensors || 0) > remoteTemps.length) {
    lines.push(
      `- Remote temperature sensors visible as relationships: \`${relationshipCounts.remoteTemperatureSensors || 0}\`; detail endpoint returned no usable rows.`
    );
  }

  lines.push("", "### Automation", "");
  table(lines, ["Scene", "Executable", "Editable"], scenes.map((scene) => [scene.name, scene.canBeExecuted, scene.canBeEdited]));
  lines.push(`- Cameras visible as inventory relationships: \`${relationshipCounts.cameras || 0}\``);
  lines.push(`- Image sensors visible as inventory relationships: \`${relationshipCounts.imageSensors || 0}\``);
  lines.push(`- Geolocation devices/fences visible: \`${relationshipCounts.geoDevices || 0}\` / \`${relationshipCounts.fences || 0}\``);
}

function addActivityTables(lines, activity) {
  lines.push("", "## Activity History", "");
  if (!activity?.ok) {
    lines.push(`- Activity fetch failed with status \`${activity?.status || "n/a"}\`.`);
    return;
  }
  if (activity.refreshOk === false) {
    lines.push(
      `- Live activity refresh failed with status \`${activity.refreshStatus || "n/a"}\`; using \`${activity.source || "cached activity history"}\`.`
    );
  }
  lines.push(`- Events returned: \`${activity.totalEvents}\``);
  table(lines, ["Day", "Events"], (activity.byDay || []).map((item) => [item.name, item.count]));
  lines.push("", "### Busiest Devices", "");
  table(lines, ["Device", "Events"], (activity.byDevice || []).map((item) => [item.name, item.count]));
  lines.push("", "### Event Types", "");
  table(lines, ["Event", "Count"], (activity.byDescription || []).map((item) => [item.name, item.count]));
  lines.push("", "### Recent Events", "");
  table(
    lines,
    ["Time", "Device", "Event"],
    (activity.recent || []).slice(0, 12).map((event) => [event.localTime, event.deviceDescription, event.description])
  );

  const media = activity.mediaTriggerHealth;
  if (media?.ok) {
    lines.push("", "### Media Trigger Health", "");
    lines.push(`- Trip-like sensor events: \`${media.tripLikeSensorEvents}\``);
    lines.push(`- Validation target trips: \`${media.validationTargetTripEvents || 0}\` (${(media.validationTargets || []).join(", ") || "none"})`);
    lines.push(`- Latest validation target trip: \`${media.latestValidationTargetTripAt || "none"}\``);
    lines.push(`- Media/image/video events: \`${media.mediaEvents}\``);
    lines.push(`- Post-disarm media events: \`${media.postDisarmMediaEvents}\``);
    lines.push(`- Sensor-triggered media events: \`${media.sensorTriggeredMediaEvents}\``);
    table(lines, ["Media event", "Count"], (media.mediaByDescription || []).map((item) => [item.name, item.count]));
    table(
      lines,
      ["Validation time", "Device", "Event"],
      (media.recentValidationTargetTrips || []).slice(0, 8).map((event) => [event.localTime, event.deviceDescription, event.description])
    );
    table(
      lines,
      ["Time", "Device", "Event"],
      (media.recentMedia || []).slice(0, 8).map((event) => [event.localTime, event.deviceDescription, event.description])
    );
  }
}

function addChangeTables(lines, changes) {
  lines.push("", "## Since Previous Capture", "");
  if (!changes) {
    lines.push("- No previous capture was available for comparison.");
    return;
  }
  const transitions = changes.deviceTransitions || [];
  const newActivity = changes.newActivity || [];
  lines.push(`- Device field changes: \`${transitions.length}\``);
  lines.push(`- Newly seen activity events: \`${newActivity.length}\``);
  if (transitions.length) {
    lines.push("", "### Device Changes", "");
    table(
      lines,
      ["Device", "Field", "From", "To"],
      transitions.slice(0, 12).map((item) => [item.name, item.field || item.type, item.from, item.to ?? item.state])
    );
  }
  if (newActivity.length) {
    lines.push("", "### New Activity", "");
    table(
      lines,
      ["Time", "Device", "Event"],
      newActivity.slice(0, 12).map((event) => [event.localTime, event.deviceDescription, event.description])
    );
  }
}

function writeTelemetryArtifacts(root, payload) {
  const devices = {
    generatedAt: payload.generatedAt,
    devices: flattenDevices(payload.alarmState),
    relationshipCounts: payload.alarmState?.relationshipCounts || {},
    issues: payload.alarmState?.issues || [],
    changes: payload.changes?.deviceTransitions || [],
  };
  const activity = {
    generatedAt: payload.generatedAt,
    ok: payload.activity?.ok || false,
    stale: payload.activity?.stale || false,
    refreshOk: payload.activity?.refreshOk,
    refreshStatus: payload.activity?.refreshStatus,
    refreshFailedAt: payload.activity?.refreshFailedAt,
    status: payload.activity?.status,
    totalEvents: payload.activity?.totalEvents || 0,
    latestEventAt: payload.activity?.latestEventAt || null,
    recent: payload.activity?.recent || [],
    byDay: payload.activity?.byDay || [],
    byDevice: payload.activity?.byDevice || [],
    byDescription: payload.activity?.byDescription || [],
    mediaTriggerHealth: payload.activity?.mediaTriggerHealth || null,
    newActivity: payload.changes?.newActivity || [],
  };
  writeJson(path.join(root, "data/alarm_com_devices.json"), devices);
  writeJson(path.join(root, "data/alarm_com_activity.json"), activity);
  if (payload.automationRules?.ok) {
    writeJson(path.join(root, "data/alarm_com_automation_rules.json"), payload.automationRules);
  }
  if (payload.gateValidation) {
    writeJson(path.join(root, "data/alarm_com_gate_validation.json"), payload.gateValidation);
  }
}

function writeReport(root, payload) {
  const sourcesConfig = loadSourcesConfig(root);
  const alarmHardware = sourcesConfig.installed_hardware?.alarm_com || [];
  payload.gateValidation = buildGateValidation(payload, alarmHardware);
  const lines = [
    "# Alarm.com Portal Capture",
    "",
    `- Generated: \`${payload.generatedAt}\``,
    `- Login: \`${payload.login.ok ? "ok" : "failed"}\``,
    `- Systems visible: \`${payload.login.systemCount || 0}\``,
    `- Energy capture: \`${payload.energy.ok ? "ok" : "failed"}\``,
    `- Device state capture: \`${payload.alarmState?.ok ? "ok" : "failed"}\``,
    `- Activity history capture: \`${
      payload.activity?.ok ? (payload.activity?.refreshOk === false ? "cached" : "ok") : "failed"
    }\``,
    `- Automation rules: \`${payload.automationRules?.ok ? `${payload.automationRules.ruleCount} found` : "failed"}\``,
    `- Video recording rules: \`${payload.videoRules?.ok ? `${payload.videoRules.ruleCount} found` : "failed"}\``,
    `- Trouble conditions: \`${payload.troubleConditions?.ok ? `${payload.troubleConditions.count} found` : "failed"}\``,
    `- Websocket token check: \`${payload.websocketToken?.ok ? "ok" : "failed"}\``,
  ];
  if (payload.energy.ok) {
    const dash = payload.energy.dashboard || {};
    lines.push(
      `- Energy dashboard current period: \`${dash.monthToDateKwh ?? "n/a"}\` kWh`,
      `- Energy daily rows vs dashboard gap: \`${payload.energy.dashboardDeltaKwh ?? "n/a"}\` kWh`,
      `- Energy meters: \`${(payload.energy.meters || []).map((meter) => meter.name).join(", ") || "n/a"}\``
    );
  }
  if (alarmHardware.length) {
    lines.push("", "## Known Installed Hardware", "");
    for (const item of alarmHardware) {
      lines.push(`- ${item.name}: ${item.purpose || "installed"}`);
    }
  }
  if (payload.automationRules?.ok) {
    lines.push("", "## Automation Rules", "");
    lines.push(`- Checked: \`${payload.automationRules.checkedAt}\``);
    lines.push(`- Rules found: \`${payload.automationRules.ruleCount}\``);
    lines.push(`- Missing tracked rules: \`${payload.automationRules.missingTracked.join(", ") || "none"}\``);
    lines.push(`- Paused tracked rules: \`${payload.automationRules.pausedTracked.join(", ") || "none"}\``);
    if ((payload.automationRules.garageLightRules || []).length) {
      lines.push(`- Garage-light direct rules: \`${payload.automationRules.garageLightRules.length}\``);
    }
    table(
      lines,
      ["Rule", "Category", "Paused", "Trigger", "Action", "Timeframe"],
      payload.automationRules.rules.map((rule) => [
        rule.name,
        rule.category,
        rule.isPaused,
        rule.trigger,
        rule.action,
        rule.timeframe,
      ])
    );
    lines.push("", "### Automation Rule Review", "");
    table(
      lines,
      ["Area", "Status", "Recommendation"],
      buildAutomationRuleReview(payload).map((item) => [item.area, item.status, item.recommendation])
    );
  }
  if (payload.videoRules?.ok) {
    lines.push("", "## Video Recording Rules", "");
    lines.push(`- Checked: \`${payload.videoRules.checkedAt}\``);
    lines.push(`- Rules found: \`${payload.videoRules.ruleCount}\``);
    lines.push(`- Missing expected rules: \`${payload.videoRules.missingExpected.join(", ") || "none"}\``);
    lines.push(`- Paused expected rules: \`${payload.videoRules.pausedExpected.join(", ") || "none"}\``);
    table(
      lines,
      ["Rule", "Paused", "Trigger", "Action", "Timeframe"],
      payload.videoRules.rules.map((rule) => [rule.name, rule.isPaused, rule.trigger, rule.action, rule.timeframe])
    );
  }
  if (payload.troubleConditions?.ok) {
    lines.push("", "## Trouble Conditions", "");
    table(
      lines,
      ["Issue", "Device ID", "Severity", "Detail"],
      (payload.troubleConditions.rows || []).map((item) => [
        item.description || item.id,
        item.emberDeviceId || item.deviceId || "n/a",
        item.severity ?? "n/a",
        item.detail || "",
      ])
    );
  }
  if (payload.gateValidation) {
    const gate = payload.gateValidation;
    lines.push("", "## Sideyard Gate Validation", "");
    lines.push(`- Flex IO / gate-control hardware recorded: \`${gate.hardwarePresent}\``);
    lines.push(`- Sideyard Gate device state: \`${gate.device?.state || "not visible"}\``);
    lines.push(`- Sideyard Gate remote commands: \`${gate.device?.remoteCommandsEnabled ?? "n/a"}\``);
    lines.push(`- Sideyard Gate Video rule: \`${gate.videoRule ? (gate.videoRule.isPaused ? "paused" : "active") : "missing"}\``);
    lines.push(`- Activity validation status: \`${gate.status}\``);
    lines.push(`- Latest Sideyard Gate trip: \`${gate.latestSideyardTripAt || "none"}\``);
    if (gate.diagnosis) {
      lines.push(`- Diagnosis: ${gate.diagnosis}`);
    }
    if (gate.blockers.length) {
      lines.push(`- Blockers: \`${gate.blockers.join("; ")}\``);
    }
    table(
      lines,
      ["Trip time", "Device", "Event"],
      (gate.recentSideyardTrips || []).map((event) => [event.localTime, event.deviceDescription, event.description])
    );
    table(
      lines,
      ["Media time", "Device", "Event"],
      (gate.recentSideyardMedia || []).map((event) => [event.localTime, event.deviceDescription, event.description])
    );
  }
  if (payload.alarmState?.ok) {
    lines.push("", "## Device State", "");
    addDeviceTables(lines, payload.alarmState);
    if (payload.alarmState.issues.length) {
      for (const issue of payload.alarmState.issues) {
        lines.push(
          `- ${issue.group}: \`${issue.description || issue.id}\` state=\`${issue.state || "n/a"}\` battery=\`${issue.batteryLevelClassification || "n/a"}\``
        );
      }
    }
  }
  addChangeTables(lines, payload.changes);
  addActivityTables(lines, payload.activity);
  if (payload.portal) {
    lines.push("", "## Portal Inventory", "");
    lines.push(`- Pages crawled: \`${payload.portal.summary.crawled}\``);
    lines.push(`- Successful pages: \`${payload.portal.summary.ok}\``);
    lines.push(`- Login redirects: \`${payload.portal.summary.redirectedToLogin}\``);
    lines.push(`- Categories: \`${JSON.stringify(payload.portal.summary.byTag)}\``);
    lines.push("", "| Page | Status | Tags | Forms | Links |");
    lines.push("|---|---:|---|---:|---:|");
    for (const page of payload.portal.pages) {
      lines.push(
        `| \`${page.title || new URL(page.url).pathname}\` | ${page.status} | ${(page.tags || []).join(", ") || "uncategorized"} | ${(page.forms || []).length || 0} | ${page.linkCount || 0} |`
      );
    }
  }
  if (payload.errors.length) {
    lines.push("", "## Errors", "");
    for (const error of payload.errors) {
      lines.push(`- ${error}`);
    }
  }
  writeTelemetryArtifacts(root, payload);
  writeJson(path.join(root, "data/latest_alarm_com.json"), payload);
  fs.writeFileSync(path.join(root, "reports/alarm_com.md"), lines.join("\n") + "\n");
}

async function main() {
  const args = new Set(process.argv.slice(2));
  const shouldCrawl = args.has("--crawl");
  const root = rootDir();
  ensureDirs(root);
  const previousCapture = loadPreviousCapture(root);
  const payload = {
    generatedAt: new Date().toISOString(),
    login: { ok: false },
    energy: { ok: false },
    alarmState: { ok: false },
    activity: { ok: false },
    automationRules: { ok: false },
    videoRules: { ok: false },
    troubleConditions: { ok: false },
    websocketToken: { ok: false },
    portal: null,
    changes: null,
    errors: [],
  };

  try {
    const config = loadAlarmConfig();
    const alarm = require(findAlarmModule());
    const auth = await alarm.login(config.username, config.password, config.mfaCookie);
    payload.login = {
      ok: true,
      systemCount: Array.isArray(auth.systems) ? auth.systems.length : 0,
      identityCount: Array.isArray(auth.identities?.data) ? auth.identities.data.length : null,
    };
    try {
      payload.alarmState = await captureSystemStates(alarm, auth);
    } catch (error) {
      payload.errors.push(`Device state capture failed: ${error.message || error}`);
    }
    try {
      payload.activity = await fetchActivityHistory(auth, selectActivityFallback(root, previousCapture));
    } catch (error) {
      payload.errors.push(`Activity history capture failed: ${error.message || error}`);
    }
    try {
      payload.videoRules = await fetchRecordingRules(auth);
    } catch (error) {
      payload.errors.push(`Video recording rule capture failed: ${error.message || error}`);
    }
    try {
      payload.automationRules = await fetchAutomationRules(auth);
    } catch (error) {
      payload.errors.push(`Automation rule capture failed: ${error.message || error}`);
    }
    try {
      payload.troubleConditions = await fetchTroubleConditions(auth);
    } catch (error) {
      payload.errors.push(`Trouble condition capture failed: ${error.message || error}`);
    }
    payload.websocketToken = await checkWebsocketToken(alarm, auth);
    try {
      const energy = await captureEnergy(auth, loadExistingAlarm(root));
      payload.energy = {
        ok: true,
        capturedAtLocal: energy.readings.capturedAtLocal,
        dashboard: energy.readings.dashboard,
        dashboardDeltaKwh: energy.dashboardDeltaKwh,
        meters: energy.meters,
        page: energy.page,
      };
      writeAlarmReadings(root, energy.readings);
    } catch (error) {
      payload.errors.push(`Energy capture failed: ${error.message || error}`);
    }

    if (shouldCrawl) {
      const seeds = [
        `${BASE}/web/system/home`,
        `${BASE}/web/Default.aspx`,
        ENERGY_URL,
        `${BASE}/web/Video/Video.aspx`,
        `${BASE}/web/Notifications/Notifications.aspx`,
        `${BASE}/web/Users/Users.aspx`,
        `${BASE}/web/Automation/Rules.aspx`,
        `${BASE}/web/Automation/Scenes.aspx`,
        `${BASE}/web/Devices/Devices.aspx`,
        `${BASE}/web/Settings/Settings.aspx`,
      ];
      const pages = await crawlPortal(auth, seeds);
      payload.portal = {
        summary: summarizePages(pages),
        pages,
      };
    }
  } catch (error) {
    payload.errors.push(`Alarm.com login failed: ${error.message || error}`);
  }

  if (payload.login.ok && previousCapture.generatedAt) {
    payload.changes = deriveChanges(previousCapture, payload);
  }

  writeReport(root, payload);
  console.log(path.join(root, "reports/alarm_com.md"));
  // Do not break the rest of the monitor chain when Alarm.com auth is stale.
  process.exit(0);
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
