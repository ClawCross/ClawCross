#!/usr/bin/env node
"use strict";
// Inspect npm's actual file selection, without running hooks, installing, or publishing.
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const root = path.resolve(__dirname, "../..");
const manifest = require(path.join(root, "package.json"));
const lock = require(path.join(root, "package-lock.json"));
if (manifest.version !== lock.version || manifest.version !== lock.packages[""].version)
  throw Error("package.json and package-lock.json versions differ");
if (manifest.license !== "Apache-2.0" || manifest.license !== lock.packages[""].license)
  throw Error("npm license metadata must match the repository's Apache-2.0 license");
const fromManifest = process.argv.includes("--manifest");
const npmArgs = ['pack', '--dry-run', '--json', '--ignore-scripts'];
const selection = fromManifest ? fs.readFileSync(0, 'utf8') : process.env.npm_execpath
  ? execFileSync(process.execPath, [process.env.npm_execpath, ...npmArgs], {cwd:root,encoding:'utf8'})
  : execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', npmArgs,
      {cwd:root,encoding:'utf8',shell:process.platform === 'win32'});
const files = new Set(JSON.parse(selection)[0].files.map(file => file.path));
for (const file of ["LICENSE", "bin/clawcross.js", "launch/run.sh", "launch/run.ps1", "launch/runtime_control.py",
  "launch/environment.py", "launch/autostart.py", "src/cli/clawcross.py", "src/cli/cli.py", "src/cli/help.txt",
  "src/cli/commands/main.py", "src/cli/commands/join.py", "src/backend/server.py", "src/frontend/server.py", "config/.env.example",
  "config/requirements.txt", "data/prompts/base_system.txt", "docs/agent-join.md"]) {
  if (files ? !files.has(file) : !fs.existsSync(path.join(root, file))) throw Error(`Required runtime asset missing: ${file}`);
}
const privatePath = /(^|\/)(\.env(?!\.example$)[^/]*|users\.json|state\.json|__pycache__|node_modules|\.git|\.venv|venv)(\/|$)|\.(db(?:-(?:wal|shm|journal))?|sqlite3?|pem|key|pyc|tgz)$|^(data\/user_files|logs|dist|test|\.clawcross-dev)\//;
for (const file of files || []) if (privatePath.test(file)) throw Error(`Private/generated file would be published: ${file}`);
for (const file of files || []) if (/^(?:HANDOFF\.md|docs\/(?:handoff[^/]*\.md|self-evolution\/))/i.test(file))
  throw Error(`Private handoff/report would be published: ${file}`);
console.log(`npm package verified: ${manifest.name}@${manifest.version}${files ? `, ${files.size} runtime files` : ""}`);
