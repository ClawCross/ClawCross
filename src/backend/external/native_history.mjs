// Read an existing ACP session through session/load. No prompt or new session.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {spawn, spawnSync} from 'node:child_process';
import {Readable, Writable} from 'node:stream';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const binary = fs.realpathSync(input.acpx);
const require = createRequire(binary);
const sdk = await import(pathToFileURL(require.resolve('@agentclientprotocol/sdk')).href);
const registryModule = await import(pathToFileURL(path.join(path.dirname(binary), 'agent-registry.js')).href);
let overrides = {};
try {
  const config = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.acpx', 'config.json'), 'utf8'));
  for (const [name, value] of Object.entries(config.agents || {})) {
    if (typeof value?.command === 'string') overrides[name] = value.command;
  }
} catch {}
const moduleRoot = path.dirname(path.dirname(path.dirname(binary)));
const registry = registryModule.createAgentRegistry({overrides, resolvePackageRoot: name => {
  const candidate = path.join(moduleRoot, name);
  if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate;
  try { return path.dirname(require.resolve(name + '/package.json')); } catch {}
  // acpx may already have launched this adapter from npm's local execution cache.
  // Resolve the existing package directly; never run npm or download it here.
  const cache = path.join(process.env.npm_config_cache || path.join(os.homedir(), '.npm'), '_npx');
  try {
    const directories=fs.readdirSync(cache, {withFileTypes:true}).filter(entry=>entry.isDirectory());
    for (const entry of directories.reverse()) {
      const cached=path.join(cache,entry.name,'node_modules',name);
      if (fs.existsSync(path.join(cached,'package.json'))) return cached;
    }
  } catch {}
  return undefined;
}});
const inspection = registry.inspect(input.platform);
const argv = input.argv || (inspection?.launch.kind === 'installed' ? inspection.launch.argv : null);
if (!argv?.length) {
  process.stdout.write(JSON.stringify({type:'result', status:'unavailable', detail:'ACP adapter is not installed; no components were downloaded.'})+'\n');
  process.exit(0);
}
const child = spawn(argv[0], argv.slice(1), {cwd: input.cwd, stdio:['pipe','pipe','pipe'], windowsHide:true, detached:process.platform !== 'win32',
  env: {...process.env, npm_config_offline:'true'}});
// Keep native stderr (which may contain credentials) out of the client response.
child.stderr.resume();
child.on('error', () => {
  process.stdout.write(JSON.stringify({type:'result', status:'error', detail:'Could not start the installed ACP adapter.'})+'\n');
  process.exit(2);
});
let outputBytes = 0;
let count = 0;
let replayError = null;
const emit = value => {
  const line = JSON.stringify(value)+'\n';
  outputBytes += Buffer.byteLength(line);
  if (outputBytes > 64*1024*1024) throw new Error('Native history exceeds the 64 MiB import limit');
  process.stdout.write(line);
};
const deny = async () => { throw new Error('History loading does not permit client file or terminal operations'); };
const stop = () => {
  child.stdin.destroy();
  if (!child.pid) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid',String(child.pid),'/T','/F'], {stdio:'ignore',windowsHide:true});
  } else {
    try { process.kill(-child.pid,'SIGTERM'); } catch { child.kill(); }
  }
};
const timeout = setTimeout(() => { stop(); process.exit(2); }, input.timeout_ms || 45000);
process.on('SIGTERM', () => { stop(); process.exit(2); });
process.on('SIGINT', () => { stop(); process.exit(2); });
try {
  const connection = new sdk.ClientSideConnection(() => ({
    sessionUpdate: async packet => {
      if (packet.sessionId !== input.session_id) return;
      if (replayError) return;
      try { count++; emit({type:'update', update:packet.update}); } catch(error) { replayError=error.message; }
    },
    requestPermission: async () => ({outcome:{outcome:'cancelled'}}),
    readTextFile:deny, writeTextFile:deny, createTerminal:deny, terminalOutput:deny,
    releaseTerminal:deny, waitForTerminalExit:deny, killTerminal:deny,
  }), sdk.ndJsonStream(Writable.toWeb(child.stdin), Readable.toWeb(child.stdout)));
  const init = await connection.initialize({protocolVersion:1, clientInfo:{name:'ClawCross history',version:'1'},
    clientCapabilities:{fs:{readTextFile:false,writeTextFile:false},terminal:false}});
  if (!init.agentCapabilities?.loadSession) {
    emit({type:'result',status:'unsupported',detail:'This ACP adapter does not support session/load history replay.'});
  } else {
    await connection.loadSession({sessionId:input.session_id,cwd:input.cwd,mcpServers:[]});
    // ACP requires replay to complete before the load response.
    if (replayError) process.stdout.write(JSON.stringify({type:'result',status:'error',detail:replayError})+'\n');
    else emit({type:'result',status:'loaded',events:count});
  }
} catch {
  emit({type:'result',status:'error',detail:'ACP history loading failed. Check adapter authentication and whether the native session is still available.'});
} finally {
  clearTimeout(timeout); stop();
}
