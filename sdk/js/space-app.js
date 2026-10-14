/* A small kit for space apps on top of clawcross-space.js (Node 18+, no dependencies).
 *
 *   const { run, page } = require('./space-app');
 *   run({ name: '辩论擂台' }, (app) => {
 *     let topic = '';
 *     app.page(page({ title: '辩论擂台', body: '…', script: '…' }));   // GET /
 *     app.state((member) => ({ topic, me: member }));                   // GET /state, long-polled by App.watch
 *     app.route('POST', '/topic', (member, body) => { topic = body.topic; app.changed(); });
 *     const reply = await app.ask(agentId, '…');                         // one request per agent at a time
 *   });
 *
 *   node my-app.js --server <group server> --key <app key>
 *
 * Pages get `window.App`: App.api(path, body), App.watch(render), App.act(run),
 * App.el(tag, props, ...children), App.fill(node, ...children), App.keep(id, make) and App.svg(markup).
 * Re-render with fill; make inputs with keep so typing survives updates.
 * Keep state small and rendering in the page; the server holds shared state and calls agents.
 */
'use strict';
const { connect } = require('./clawcross-space');

const WATCH_MS = 20_000;

/** Files every app serves next to its page, so maps need no outside CDN: <script src="leaflet.js">. */
const STATIC = {
  '/leaflet.js': ['vendor/leaflet/leaflet.js', 'application/javascript; charset=utf-8'],
  '/leaflet.css': ['vendor/leaflet/leaflet.css', 'text/css; charset=utf-8'],
  '/space-map.js': ['space-map.js', 'application/javascript; charset=utf-8'],
};
const files = new Map();
const staticFile = (name) => files.get(name) ?? files.set(name, require('node:fs').readFileSync(require('node:path').join(__dirname, name))).get(name);
/** The head of a page with a map: Leaflet and SpaceMap (sdk/js/space-map.js). */
const MAP_HEAD = '<link rel="stylesheet" href="leaflet.css"><script src="leaflet.js"></script><script src="space-map.js"></script>';
const ASK_MS = 5 * 60_000;

/** Pulls the first JSON object or array out of an agent's reply (fenced or bare). */
function json(text) {
  const source = String(text || '');
  const fenced = /```(?:json)?\s*([\s\S]*?)```/i.exec(source);
  for (const candidate of [fenced?.[1], source]) {
    if (!candidate) continue;
    const start = candidate.search(/[[{]/);
    if (start < 0) continue;
    for (let end = candidate.length; end > start; end--) {
      if (!']}'.includes(candidate[end - 1])) continue;
      try { return JSON.parse(candidate.slice(start, end)); } catch { /* shorter */ }
    }
  }
  return null;
}

class App {
  constructor(space, name) {
    this.space = space;
    this.name = name;
    this.roster = { title: '', members: [] };
    this.version = 0;
    this.watchers = new Set();
    this.routes = new Map();
    this.queues = new Map();      // per agent: the tail of its request chain
    this.pending = new Map();     // request id -> resolve
    this.listeners = new Set();
    this.html = '';
    this.view = () => ({});
    space.on('agent', (output) => {
      const resolve = this.pending.get(output.request);
      if (resolve) { this.pending.delete(output.request); resolve(output.content); }
      else for (const fn of this.listeners) fn(output);
    });
    space.on('member', () => this.refresh().catch(() => {}));
  }

  async refresh() { this.roster = await this.space.members(); this.changed(); }
  get title() { return this.roster.title; }
  members() { return this.roster.members; }
  agents() { return this.roster.members.filter((m) => m.agent); }
  humans() { return this.roster.members.filter((m) => !m.agent); }
  member(id) { return this.roster.members.find((m) => m.id === id); }
  isOwner(id) { return !!this.member(id)?.owner; }
  /** People and agents on one device: an agent's human is the person sharing its device. */
  ownerOf(agentId) { const agent = this.member(agentId); return this.humans().find((m) => m.device === agent?.device); }
  agentsOf(personId) { const person = this.member(personId); return this.agents().filter((m) => m.device === person?.device); }

  /** Tells every watching page that the state changed. */
  changed() { this.version++; for (const wake of this.watchers) wake(); }

  /** Asks one agent and resolves with its reply. Requests to the same agent wait their turn. */
  ask(agent, prompt, { timeout = ASK_MS } = {}) {
    const previous = this.queues.get(agent) || Promise.resolve();
    const turn = previous.catch(() => {}).then(async () => {
      const id = await this.space.ask(agent, prompt);
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('agent 没有在时限内回复')); }, timeout);
        this.pending.set(id, (content) => { clearTimeout(timer); resolve(content); });
      });
    });
    this.queues.set(agent, turn);
    return turn;
  }

  /** Asks for JSON; resolves with the parsed value, or null when the reply has none. */
  async askJSON(agent, prompt, options) {
    return json(await this.ask(agent, prompt + '\n\n只回复 JSON，不要其他文字。', options));
  }

  /** Agent output no ask is waiting for, e.g. an agent speaking up on its own. */
  onMessage(fn) { this.listeners.add(fn); }

  page(html) { this.html = html; }
  state(fn) { this.view = fn; }
  route(method, path, handler) { this.routes.set(method + ' ' + path, handler); }

  async handle(req, res) {
    // The page itself is the same for everyone and may be cached; state and actions never are.
    const send = (status, body, type = 'application/json; charset=utf-8') =>
      res.writeHead(status, { 'Content-Type': type, 'Cache-Control': type.startsWith('text/html') ? 'private, max-age=600' : 'no-store' })
        .end(type.startsWith('application/json') ? JSON.stringify(body) : body);
    try {
      const url = new URL(req.url, 'http://space');
      const member = { ...(this.member(req.member.id) || {}), id: req.member.id, name: req.member.name };
      if (req.method === 'GET' && url.pathname === '/') return send(200, this.html, 'text/html; charset=utf-8');
      if (req.method === 'GET' && Object.hasOwn(STATIC, url.pathname)) {
        const [file, type] = STATIC[url.pathname];
        return res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'public, max-age=86400' }).end(staticFile(file));
      }
      if (req.method === 'GET' && url.pathname === '/state') {
        // Long poll: answer when the state moves past the page's version, or after a while anyway.
        const seen = Number(url.searchParams.get('v'));
        if (Number.isInteger(seen) && seen === this.version) {
          await new Promise((resolve) => {
            const done = () => { clearTimeout(timer); this.watchers.delete(done); resolve(); };
            const timer = setTimeout(done, WATCH_MS);
            this.watchers.add(done);
            req.on('close', done);
          });
        }
        return send(200, { v: this.version, title: this.title, me: member, owner: !!member.owner,
          members: this.members(), state: await this.view(member) });
      }
      const handler = this.routes.get(req.method + ' ' + url.pathname);
      if (!handler) return send(404, { error: '没有这个页面' });
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString() || '{}') : Object.fromEntries(url.searchParams);
      const result = await handler(member, body, url);
      return send(200, result === undefined ? { ok: true } : result);
    } catch (error) {
      send(error.status || 400, { error: error.message || String(error) });
    }
  }
}

/** The first <svg> in an agent's reply if it is plain drawing: no scripts, links, external
 * resources or embedded documents; otherwise ''. Pages show it as an <img> data URL. */
function safeSvg(text, max = 16000) {
  const svg = /<svg[\s\S]*?<\/svg>/i.exec(String(text || ''))?.[0] || '';
  if (!svg || svg.length > max) return '';
  if (/<(script|foreignObject|image|iframe|use|a|style)\b/i.test(svg)) return '';
  if (/\son[a-z]+\s*=|href\s*=|javascript:|<!ENTITY|@import|url\s*\(\s*['"]?(?!#)/i.test(svg)) return '';
  return svg;
}

/** An error the page shows as is. */
function refuse(message, status = 400) { return Object.assign(new Error(message), { status }); }

const { THEME, appearance } = require('./space-app-theme.js');

const CLIENT = `
window.App=(()=>{
  const error=document.getElementById('app-error');
  async function api(path,body){
    // text/plain keeps a POST "simple": the sandboxed page skips the CORS preflight round trip.
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8'},body:JSON.stringify(body)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||response.statusText);
    return data;
  }
  // Re-rendering mid-composition would cut off IME input (Chinese, Japanese…): wait for it to end.
  let composing=false;
  addEventListener('compositionstart',()=>{composing=true;},true);
  addEventListener('compositionend',()=>{composing=false;},true);
  /** Calls render(snapshot) now and whenever the shared state changes. */
  async function watch(render){
    let v='';
    for(;;){
      try{
        const snapshot=await api('state?v='+v);v=snapshot.v;if(error)error.textContent='';
        while(composing)await new Promise(r=>setTimeout(r,150));
        render(snapshot);
      }
      catch(failure){if(error)error.textContent=failure.message;await new Promise(r=>setTimeout(r,3000));}
    }
  }
  /** Runs an action; its error, if any, shows in #app-error. */
  async function act(run){try{return await run();}catch(failure){if(error)error.textContent=failure.message;}}
  function el(tag,props,...children){
    const node=document.createElement(tag);
    for(const [k,v] of Object.entries(props||{}))if(k.startsWith('on'))node.addEventListener(k.slice(2),v);else if(v!==false&&v!=null)node.setAttribute(k,v===true?'':v);
    for(const child of children.flat(Infinity))if(child!=null&&child!==false)node.append(child.nodeType?child:document.createTextNode(String(child)));
    return node;
  }
  /** Replaces a node's children, skipping empty ones; a focused field kept with App.keep stays focused. */
  function fill(node,...children){
    const active=document.activeElement,range=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null;
    node.replaceChildren(...children.flat(Infinity).filter(c=>c!=null&&c!==false));
    if(active&&active!==document.body&&active.isConnected&&document.activeElement!==active){active.focus();if(range)try{active.setSelectionRange(...range);}catch{}}
    return node;
  }
  /** The element with this id if the page has it, else make() — so re-rendering keeps what people are typing. */
  function keep(id,make){return document.getElementById(id)||make();}
  /** An <img> showing an SVG the server already checked. */
  function svg(markup,props){return el('img',{...(props||{}),src:'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(markup)});}
  return {api,watch,act,el,fill,keep,svg};
})();
`;

/** The page shell every app uses: theme, an error line and `window.App`. Pages use relative URLs. */
function page({ title, appId = '', style = '', body = '', script = '', head = '' }) {
  const skin = appearance(appId);
  if (appId) body = body.replace(/<h1>([\s\S]*?)<\/h1>\s*(?:<p class="muted">([\s\S]*?)<\/p>)?/,
    (_, heading, intro) => skin.header(heading) + ((intro || skin.intro) ? '<p class="app-intro">' + (intro || skin.intro) + '</p>' : ''));
  return `<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><style>${THEME}${style}${skin.css}</style>${head}</head><body><main>${body}<p id="app-error" class="error" role="alert"></main>
<script>${CLIENT}</script><script>${script.replace(/<\/script/gi, '<\\/script')}</script></body></html>`;
}

/** Connects with --server/--key (or CLAWCROSS_SPACE_SERVER/KEY), lets `setup` define the app, then serves it. */
async function run({ name }, setup, options = {}) {
  const args = Object.fromEntries(process.argv.slice(2).flatMap((value, index, all) => value.startsWith('--') ? [[value.slice(2), all[index + 1]]] : []));
  const space = await connect({ server: options.server || args.server || process.env.CLAWCROSS_SPACE_SERVER,
    key: options.key || args.key || process.env.CLAWCROSS_SPACE_KEY, name });
  const app = new App(space, name);
  await app.refresh();
  await setup(app);
  await space.serve((req, res) => app.handle(req, res));
  if (!options.server) {
    space.on('offline', (error) => console.error('暂时连不上群聊服务器：' + error.message));
    space.on('closed', (error) => { console.error(error.message); process.exit(1); });
    console.log(`${name}已连接到空间「${app.title}」`);
  }
  return app;
}

module.exports = { run, page, json, refuse, safeSvg, App, MAP_HEAD };
