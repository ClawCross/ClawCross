#!/usr/bin/env node
"use strict";

// npm supplies the command; Python owns the CLI after the platform bootstrap.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
if (args.length === 1 && ["--version", "-V", "version"].includes(args[0])) {
  console.log(require(path.join(root, "package.json")).version);
} else if ((!args.length && !process.stdin.isTTY) ||
  (args.length === 1 && ["help", "--help", "-h"].includes(args[0]))) {
  console.log(fs.readFileSync(path.join(root, "src/cli/help.txt"), "utf8").trimEnd());
} else {
  const windows = process.platform === "win32";
  const child = spawn(windows ? "powershell.exe" : "bash", windows
    ? ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(root, "launch/run.ps1"), "clawcross"]
    : [path.join(root, "launch/run.sh"), "clawcross", ...args], {
    stdio: "inherit", cwd: process.cwd(),
    env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8",
      ...(windows ? {CLAWCROSS_CLI_ARGS_JSON: JSON.stringify(args)} : {}) },
  });
  // The terminal also delivers Ctrl+C to Python; let the REPL cancel a turn.
  process.on("SIGINT", () => {});
  process.on("SIGTERM", () => child.kill("SIGTERM"));
  child.on("error", error => {
    console.error(`ClawCross: ${error.message}`);
    process.exitCode = 1;
  });
  child.on("exit", (code, signal) => {
    process.exitCode = code ?? (signal === "SIGINT" ? 130 : 1);
  });
}
