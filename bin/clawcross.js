#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const script = path.join(root, "scripts", "clawcross.py");
const runScript = process.platform === "win32"
  ? path.join(root, "selfskill", "scripts", "run.ps1")
  : path.join(root, "selfskill", "scripts", "run.sh");

const runCommands = new Set([
  "start",
  "start-foreground",
  "start-fg",
  "stop",
  "restart",
  "setup",
  "status",
  "logs",
  "doctor",
  "configure",
  "auto-model",
  "check-openclaw",
  "start-tunnel",
  "stop-tunnel",
  "evolve-skill",
]);

function loadDotEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }
  const values = {};
  for (const rawLine of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (key) {
      values[key] = value;
    }
  }
  return values;
}

function firstExisting(candidates) {
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function isAgentRunning(port) {
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const req = http.get(
      {
        hostname: "127.0.0.1",
        port,
        path: "/v1/models",
        timeout: 700,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode >= 200 && res.statusCode < 500);
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

const python = firstExisting([
  process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : null,
  path.join(root, ".venv", "bin", "python"),
]) || process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");

async function main() {
  const args = process.argv.slice(2);
  const env = loadDotEnv(path.join(root, "config", ".env"));
  const agentPort = Number.parseInt(process.env.PORT_AGENT || env.PORT_AGENT || "51200", 10);
  const command = args[0] || (
    await isAgentRunning(agentPort) ? undefined : "start"
  );
  const launcherArgs = args.length === 0 && command === "start" ? ["start"] : args;
  const useRunScript = runCommands.has(command);
  const launcher = useRunScript
    ? (process.platform === "win32"
      ? ["powershell", ["-ExecutionPolicy", "Bypass", "-File", runScript, ...launcherArgs]]
      : ["bash", [runScript, ...launcherArgs]])
    : [python, [script, ...args]];

  const result = spawnSync(launcher[0], launcher[1], {
    stdio: "inherit",
    env: process.env,
    cwd: useRunScript ? root : process.cwd(),
  });

  if (result.error) {
    console.error(`Failed to launch ClawCross with ${launcher[0]}: ${result.error.message}`);
    process.exit(1);
  }

  process.exit(result.status === null ? 1 : result.status);
}

main().catch((error) => {
  console.error(`Failed to launch ClawCross: ${error.message}`);
  process.exit(1);
});
