#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "scripts", "clawcross.py");

function firstExisting(candidates) {
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

const python = firstExisting([
  process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : null,
  path.join(root, ".venv", "bin", "python"),
]) || process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");

const result = spawnSync(python, [script, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: process.env,
});

if (result.error) {
  console.error(`Failed to launch ClawCross Shell with ${python}: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status === null ? 1 : result.status);
