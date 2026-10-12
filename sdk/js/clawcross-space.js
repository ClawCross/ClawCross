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
 * The app server needs no public address: it connects out to the space's group server, and the
 * members' pages reach `serve()` through that connection. Use relative URLs in pages.
 */
'use strict';
const http = require('node:http');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');

const REPLY_BYTES = 1536 * 1024;

class Space extends EventEmitter {
  constructor({ server, key, name = '' }) {
    super();
    if (!server || !key) throw new Error('需要群聊服务器地址和小程序密钥');
    this.base = String(server).replace(/\/+$/, '') + '/relay/app/';
    this.key = key;
    this.name = name;
    this.cursor = null;
    this.closed = false;
    this.site = null;
    this.secret = crypto.randomBytes(16).toString('hex');
  }

  async call(method, path, body) {
    const response = await fetch(this.base + path, {
      method, headers: { Authorization: 'Bearer ' + this.key, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : data.error || response.statusText);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  /** `{ title, members: [{ id, name, agent, platform, online, device, owner }] }`; one device's people and agents share `device`. */
  members() { return this.call('GET', 'members'); }

  /** Sends `content` to one agent; resolves to the request id its later output carries. */
  async ask(agent, content) { return (await this.call('POST', 'ask', { agent, content })).request; }

  /** Members' pages are served by `handler(req, res)`; `req.member` is `{ id, name }`, verified by the group server. */
  serve(handler) {
    this.site = http.createServer((req, res) => {
      // Only requests forwarded by this SDK carry the secret; others on this machine are refused.
      if (req.headers['x-space-secret'] !== this.secret) { res.writeHead(403).end(); return; }
      req.member = { id: String(req.headers['x-space-member'] || ''), name: decodeURIComponent(String(req.headers['x-space-member-name'] || '')) };
      handler(req, res);
    });
    return new Promise((resolve) => this.site.listen(0, '127.0.0.1', resolve));
  }

  close() {
    this.closed = true;
    this.site?.close();
  }

  async run() {
    let delay = 1000;
    while (!this.closed) {
      try {
        const packet = await this.call('POST', 'poll', { cursor: this.cursor, name: this.name });
        this.cursor = packet.cursor;
        delay = 1000;
        for (const item of packet.items) this.dispatch(item);
      } catch (error) {
        if (error.status === 401) { this.emit('closed', error); this.close(); return; }
        this.emit('offline', error);
        await new Promise((resolve) => setTimeout(resolve, delay));
        delay = Math.min(30000, delay * 2);
      }
    }
  }

  dispatch(item) {
    if (item.type === 'http') this.forward(item).catch((error) => this.emit('offline', error));
    else if (item.type === 'agent') this.emit('agent', { agent: item.agent, name: item.name, request: item.request, content: item.content });
    else if (item.type === 'members') this.emit('member');
  }

  async forward(item) {
    const reply = await new Promise((resolve) => {
      if (!this.site) { resolve({ status: 404, headers: { 'content-type': 'text/plain; charset=utf-8' }, body: Buffer.from('小程序还没有页面') }); return; }
      const request = http.request({
        host: '127.0.0.1', port: this.site.address().port, method: item.method, path: item.path + (item.query ? '?' + item.query : ''),
        headers: { ...item.headers, 'x-space-secret': this.secret, 'x-space-member': item.member.id,
          'x-space-member-name': encodeURIComponent(item.member.name) }
      }, (response) => {
        const chunks = []; let size = 0;
        response.on('data', (chunk) => { size += chunk.length; if (size <= REPLY_BYTES) chunks.push(chunk); });
        response.on('end', () => resolve(size > REPLY_BYTES
          ? { status: 502, headers: { 'content-type': 'text/plain; charset=utf-8' }, body: Buffer.from('小程序回复超过 1.5 MiB') }
          : { status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
      });
      request.on('error', (error) => resolve({ status: 502, headers: { 'content-type': 'text/plain; charset=utf-8' }, body: Buffer.from(String(error.message)) }));
      request.end(Buffer.from(item.body || '', 'base64'));
    });
    const headers = Object.fromEntries(Object.entries(reply.headers).filter(([, value]) => typeof value === 'string'));
    await this.call('POST', 'respond', { id: item.id, status: reply.status, headers, body: reply.body.toString('base64') });
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
