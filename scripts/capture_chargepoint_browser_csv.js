#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const DATA_DIR = path.join(ROOT, "data");
const DOWNLOAD_DIR = path.join(DATA_DIR, "chargepoint-downloads");
const STATUS_PATH = path.join(DATA_DIR, "latest_chargepoint_browser_csv.json");

function usage() {
  return [
    "Usage:",
    "  capture_chargepoint_browser_csv.js --data-url DATA_URL [--import]",
    "  capture_chargepoint_browser_csv.js --input chargepoint.csv [--import]",
    "  capture_chargepoint_browser_csv.js --clipboard [--import]",
    "  pbpaste | capture_chargepoint_browser_csv.js --import",
    "",
    "The ChargePoint Driver Portal's Download CSV link is a data:text/csv URL.",
    "This helper stores that browser-exported CSV under data/chargepoint-downloads",
    "and can immediately import it through fetch_chargepoint_sessions.py.",
  ].join("\n");
}

function argValue(args, name) {
  const index = args.indexOf(name);
  if (index === -1) {
    return null;
  }
  return args[index + 1] || "";
}

function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

function readClipboard() {
  const result = spawnSync("pbpaste", { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error("Could not read macOS clipboard with pbpaste.");
  }
  return result.stdout;
}

function decodeInput(raw) {
  const trimmed = String(raw || "").trim();
  if (!trimmed) {
    throw new Error("No ChargePoint CSV data was provided.");
  }
  if (trimmed.startsWith("data:text/csv")) {
    const comma = trimmed.indexOf(",");
    if (comma === -1) {
      throw new Error("CSV data URL is missing the comma separator.");
    }
    return decodeURIComponent(trimmed.slice(comma + 1));
  }
  if (trimmed.startsWith("Start,End,") || trimmed.includes("\n")) {
    return trimmed.endsWith("\n") ? trimmed : `${trimmed}\n`;
  }
  throw new Error("Input was neither a ChargePoint CSV data URL nor CSV text.");
}

function rowCount(csv) {
  return csv
    .split(/\r?\n/)
    .slice(1)
    .filter((line) => line.trim()).length;
}

function timestampForFile() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function writeJson(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(payload, null, 2)}\n`);
}

function importCsv(csvPath) {
  const result = spawnSync(
    process.env.PYTHON || "python3",
    ["scripts/fetch_chargepoint_sessions.py"],
    {
      cwd: ROOT,
      encoding: "utf8",
      env: {
        ...process.env,
        CHARGEPOINT_MODE: "browser_csv",
        CHARGEPOINT_CSV_PATH: csvPath,
      },
    }
  );
  return {
    ok: result.status === 0,
    status: result.status,
    stdout: result.stdout.trim(),
    stderr: result.stderr.trim(),
  };
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes("--help") || args.includes("-h")) {
    console.log(usage());
    return 0;
  }

  let raw = argValue(args, "--data-url");
  const inputPath = argValue(args, "--input");
  if (inputPath) {
    raw = fs.readFileSync(path.resolve(inputPath), "utf8");
  } else if (args.includes("--clipboard")) {
    raw = readClipboard();
  } else if (!raw) {
    raw = readStdin();
  }

  const csv = decodeInput(raw);
  const rows = rowCount(csv);
  if (rows <= 0) {
    throw new Error("ChargePoint CSV contained no session rows.");
  }

  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
  const outPath = path.join(DOWNLOAD_DIR, `ChargePoint_Charging_Activity_${timestampForFile()}.csv`);
  fs.writeFileSync(outPath, csv);

  const payload = {
    csvPath: outPath,
    finishedAt: new Date().toISOString(),
    imported: false,
    rows,
    status: "saved",
  };

  if (args.includes("--import")) {
    const importResult = importCsv(outPath);
    payload.imported = importResult.ok;
    payload.importResult = importResult;
    payload.status = importResult.ok ? "imported" : "import_failed";
    if (!importResult.ok) {
      writeJson(STATUS_PATH, payload);
      console.error(JSON.stringify(payload, null, 2));
      return importResult.status || 1;
    }
  }

  writeJson(STATUS_PATH, payload);
  console.log(JSON.stringify(payload, null, 2));
  return 0;
}

try {
  process.exitCode = main();
} catch (error) {
  const payload = {
    error: error instanceof Error ? error.message : String(error),
    finishedAt: new Date().toISOString(),
    status: "failed",
  };
  writeJson(STATUS_PATH, payload);
  console.error(JSON.stringify(payload, null, 2));
  process.exitCode = 1;
}
