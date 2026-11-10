/* Runs one JavaScript snippet for a guest's agent. Served at /group-guest-worker.js with a CSP
   that allows no network and no further scripts; storage and networking globals are removed
   too, so code a group member talks the agent into can only compute on the files it is handed. */
"use strict";
for (const name of ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "WebTransport", "importScripts", "indexedDB",
  "caches", "Worker", "SharedWorker", "BroadcastChannel", "cookieStore"]) {
  for (let scope = self; scope; scope = Object.getPrototypeOf(scope)) try { delete scope[name]; } catch (_) {}
}
for (let scope = navigator; scope; scope = Object.getPrototypeOf(scope)) try { delete scope.storage; } catch (_) {}
const show = (value) => {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2) ?? String(value); } catch (_) { return String(value); }
};
self.onmessage = async (event) => {
  const { code, files } = event.data;
  const output = [];
  const print = (...values) => output.push(values.map(show).join(" "));
  const console = { log: print, info: print, warn: print, error: print };
  try {
    const value = await new Function("files", "console", '"use strict"; return (async () => {\n' + code + '\n})();')(files, console);
    self.postMessage({ ok: true, output: output.join("\n"), result: value === undefined ? null : show(value) });
  } catch (error) {
    self.postMessage({ ok: false, output: output.join("\n"), error: String((error && error.stack) || error) });
  }
};
