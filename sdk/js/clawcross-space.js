/* ClawCross space SDK, for the app server a space's owner runs (Node 18+, no dependencies).
 *
 *   const { connect } = require('./clawcross-space');
 *   const space = await connect({ server, key, name: '精灵对战' });
 *   await space.serve(app);             // any Node HTTP handler, e.g. an Express app; req.member is who is looking
 *   const { members } = await space.members();
 *   await space.ask(agentId, '画一只精灵');  // only that agent's device receives it
 *   space.on('agent', ({ agent, name, request, content }) => …);  // what agents post with send_to_group
 *   space.on('member', () => …);        // someone joined, left or changed
 *
 * Run the app on the group server's host. Pages stream directly to its loopback HTTP port;
 * use the Hub public tunnel to share a local group. Use relative URLs in pages.
 */
'use strict';
const http = require('node:http');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');

class Space extends EventEmitter {
  constructor({ server, key, name = '', dataDir = process.env.CLAWCROSS_SPACE_DATA_DIR, localToken = process.env.CLAWCROSS_SPACE_LOCAL_TOKEN }) {
    super();
    if (!server || !key) throw new Error('需要群聊服务器地址和小程序密钥');
    const address = new URL(server);
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname) || !localToken)
      throw new Error('小程序须在群聊主机运行，并由主机提供 CLAWCROSS_SPACE_LOCAL_TOKEN');
    this.localToken = localToken;
    this.dataDir = dataDir;
    this.base = String(server).replace(/\/+$/, '') + '/relay/app/';
    this.key = key;
    this.name = name;
    this.cursor = null;
    this.closed = false;
    this.site = null;
    this.requests = new Set();
    this.sockets = new Set();
    this.secret = crypto.randomBytes(16).toString('hex');
  }

  async call(method, path, body) {
    if (this.closed) throw new Error('群聊小程序已停止');
    const controller = new AbortController();
    this.requests.add(controller);
    try {
    const response = await fetch(this.base + path, {
      signal: controller.signal,
      method, headers: { Authorization: 'Bearer ' + this.key, 'X-Group-Service-Key': this.localToken, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : data.error || response.statusText);
      error.status = response.status;
      throw error;
    }
    return data;
    } finally { this.requests.delete(controller); }
  }

  /** `{ title, members: [{ id, name, agent, platform, online, device, owner }] }`; one device's people and agents share `device`. */
  members() { return this.call('GET', 'members'); }

  /** Sends `content` to one agent; resolves to the request id its later output carries. */
  async ask(agent, content) { return (await this.call('POST', 'ask', { agent, content })).request; }

  /** Members' pages are served by `handler(req, res)`; `req.member` is `{ id, name }`, verified by the group server. */
  async serve(handler, { upgrade } = {}) {
    if (this.closed) throw new Error('群聊小程序已停止');
    this.site = http.createServer((req, res) => {
      // Only requests forwarded by this SDK carry the secret; others on this machine are refused.
      if (req.headers['x-space-secret'] !== this.secret) { res.writeHead(403).end(); return; }
      req.member = { id: String(req.headers['x-space-member'] || ''), name: decodeURIComponent(String(req.headers['x-space-member-name'] || '')) };
      handler(req, res);
    });
    this.site.on('connection', socket => {
      this.sockets.add(socket);
      socket.once('close', () => this.sockets.delete(socket));
    });
    if (upgrade) this.site.on('upgrade', (req, socket, head) => {
      if (req.headers['x-space-secret'] !== this.secret) { socket.destroy(); return; }
      req.member = { id: String(req.headers['x-space-member'] || ''), name: decodeURIComponent(String(req.headers['x-space-member-name'] || '')) };
      upgrade(req, socket, head);
    });
    await new Promise((resolve, reject) => { this.site.once('error', reject); this.site.listen(0, '127.0.0.1', resolve); });
    await this.call('POST', 'serve', this.endpoint());
  }

  endpoint() { return this.site?.listening ? { port: this.site.address().port, secret: this.secret } : {}; }

  close() {
    if (this.closed) return;
    this.closed = true;
    try { this.emit('closing'); }
    finally {
      for (const controller of this.requests) controller.abort();
      clearTimeout(this.retry);
      this.resume?.();
      this.site?.close();
      for (const socket of this.sockets) socket.destroy();
      this.sockets.clear();
    }
  }

  async run() {
    let delay = 1000;
    while (!this.closed) {
      try {
        const packet = await this.call('POST', 'poll', { cursor: this.cursor, name: this.name, ...this.endpoint() });
        this.cursor = packet.cursor;
        delay = 1000;
        for (const item of packet.items) this.dispatch(item);
      } catch (error) {
        if (this.closed) return;
        if ([401, 403, 404].includes(error.status)) { this.close(); this.emit('closed', error); return; }
        this.emit('offline', error);
        await new Promise((resolve) => { this.resume = resolve; this.retry = setTimeout(resolve, delay); });
        delay = Math.min(30000, delay * 2);
      }
    }
  }

  dispatch(item) {
    if (item.type === 'agent') this.emit('agent', { agent: item.agent, name: item.name, request: item.request, content: item.content });
    else if (item.type === 'members') this.emit('member');
  }

}

/** Connects to a space with its app key; the returned space keeps polling until `close()`. */
async function connect(options) {
  const space = new Space(options);
  space.cursor = (await space.call('POST', 'poll', { cursor: null, name: space.name })).cursor;
  space.run();
  return space;
}

module.exports = { connect, Space };
