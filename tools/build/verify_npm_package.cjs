#!/usr/bin/env node
"use strict";
// Inspect npm's actual file selection, without running hooks, installing, or publishing.
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const manifest = require(path.join(root, "package.json"));
const lock = require(path.join(root, "package-lock.json"));
if (manifest.version !== lock.version || manifest.version !== lock.packages[""].version)
  throw Error("package.json and package-lock.json versions differ");
if (manifest.license !== "Apache-2.0" || manifest.license !== lock.packages[""].license)
  throw Error("npm license metadata must match the repository's Apache-2.0 license");
const fromManifest = process.argv.includes("--manifest");
const files = fromManifest ? new Set(JSON.parse(fs.readFileSync(0, "utf8"))[0].files.map(file => file.path)) : null;
for (const file of ["LICENSE", "bin/clawcross.js", "launch/run.sh", "launch/run.ps1", "launch/runtime_control.py",
  "launch/environment.py", "src/cli/clawcross.py", "src/cli/cli.py", "src/cli/help.txt",
  "src/cli/commands/main.py", "src/backend/server.py", "src/frontend/server.py", "config/.env.example",
  "config/requirements.txt", "data/prompts/base_system.txt"]) {
  if (files ? !files.has(file) : !fs.existsSync(path.join(root, file))) throw Error(`Required runtime asset missing: ${file}`);
}
const privatePath = /(^|\/)(\.env(?!\.example$)[^/]*|users\.json|state\.json|__pycache__|node_modules|\.git|\.venv|venv)(\/|$)|\.(db(?:-(?:wal|shm|journal))?|sqlite3?|pem|key|pyc|tgz)$|^(data\/user_files|logs|dist|test|\.clawcross-dev)\//;
for (const file of files || []) if (privatePath.test(file)) throw Error(`Private/generated file would be published: ${file}`);
console.log(`npm package verified: ${manifest.name}@${manifest.version}${files ? `, ${files.size} runtime files` : ""}`);
