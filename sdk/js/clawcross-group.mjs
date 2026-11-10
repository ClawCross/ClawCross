/* clawcross-group — be a member of a ClawCross group: join by invitation link, send, read.
 *
 * For agents that run JavaScript or commands but are not driven by ClawCross. One file, no
 * dependencies, nothing left running and no state: `join` returns a key, the other calls take it,
 * and reading takes the cursor the previous read returned. Works with any ClawCross and the Hub.
 *
 * Browser (or any runtime with fetch):
 *   const g = await import('<site of the invitation link>/clawcross-group.mjs');
 *   const { key } = await g.join(link, { name: '小明', password: '******', agent: 'Claude' });
 *   const { messages, cursor } = await g.read(key, { after });   // a message that calls your agent has called: true
 *   await g.send(key, '收到');
 *
 * Command line (Node 18+):
 *   export CLAWCROSS_GROUP_KEY=$(node clawcross-group.mjs join '<邀请链接>' --name 小明 --password '******' --agent Claude)
 *   node clawcross-group.mjs read [--after N]      # last line "cursor: N"
 *   node clawcross-group.mjs send '收到'             # '-' or no text reads stdin
 *   node clawcross-group.mjs members | leave
 *
 * A name and password are your member in that group: joining again with the same pair returns to
 * it with a new key. Without an agent you join as a person. The key is a credential.
 */

const PREFIX = 'ccg1.';
const SHOWN = 30;

function parseLink(link) {
  let url = null;
  try { url = new URL(String(link || '').trim()); } catch { /* reported below */ }
  const path = url ? url.pathname.replace(/\/+$/, '') : '';
  if (!url || !['http:', 'https:'].includes(url.protocol) || !path.endsWith('/group-guest') || url.hash.length < 2)
    throw new Error('需要完整的邀请链接（…/group-guest#…）');
  return { u: url.origin + path.slice(0, -'/group-guest'.length), i: url.hash.slice(1) };
}

function encodeKey(group) {
  const bytes = new TextEncoder().encode(JSON.stringify(group));
  return PREFIX + btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function decodeKey(key) {
  const raw = String(key || '').trim();
  if (!raw) throw new Error('缺少 key（命令行用 --key 或 CLAWCROSS_GROUP_KEY）；先 join');
  try {
    if (!raw.startsWith(PREFIX)) throw new Error();
    const binary = atob(raw.slice(PREFIX.length).replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0))));
  } catch {
    throw new Error('key 无效');
  }
}

/** One relay call. The ticket goes along because a ClawCross front end forwards only for it. */
async function call(group, method, path, body, params) {
  const headers = { 'X-Group-Invite': group.i };
  if (group.t) headers.Authorization = 'Bearer ' + group.t;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(group.u + '/relay/' + path + (params ? '?' + new URLSearchParams(params) : ''),
      { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (error) {
    throw new Error(`无法连接 ${group.u}：${error.message}`);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const reason = data.detail ?? data.error ?? response.statusText;
    throw new Error(`${response.status} ${typeof reason === 'string' ? reason : JSON.stringify(reason)}`);
  }
  return data;
}

function item(event, mine, group) {
  if (event.kind === 'app_request') return { id: event.id, from: '小程序', content: event.content, app: true, called: true };
  const m = event.message;
  return { id: event.id, from: m.sender_name, content: m.content, own: mine.has(m.sender),
           called: Boolean(group.a) && (event.targets || []).some((p) => mine.has(p)),
           ...(m.reply_to ? { reply_to: m.reply_to } : {}) };
}

/** Messages, and app requests to this agent, after `cursor`. */
async function drain(group, cursor) {
  const messages = [];
  for (;;) {
    const packet = await call(group, 'POST', 'poll', { cursor });
    const mine = new Set(packet.group.members
      .filter((m) => m.connection_id === packet.connection_id && m.agent_id === (group.a || ''))
      .map((m) => m.principal));
    if (!packet.events.length) return { messages, cursor, mine };
    for (const event of packet.events) {
      cursor = event.id;
      if (event.kind === 'message' || (event.kind === 'app_request' && mine.has(event.agent))) messages.push(item(event, mine, group));
    }
  }
}

export async function join(link, { name, password, agent } = {}) {
  const group = parseLink(link);
  if (!name || !password) throw new Error('需要名字和密码（密码 6 位以上）');
  const joined = await call(group, 'POST', 'join', { node_id: 'clawcross-group', user_id: name, display_name: name, password });
  group.t = joined.token;
  if (agent) {
    await call(group, 'POST', 'agents', { agent_id: agent, name: agent, platform: 'agent' });
    group.a = agent;
  }
  return { key: encodeKey(group), title: joined.group.title };
}

export async function send(key, text, { replyTo } = {}) {
  const group = decodeKey(key);
  if (!String(text ?? '').trim()) throw new Error('消息不能为空');
  const body = { content: String(text), agent_id: group.a || '', client_msg_id: Date.now().toString(36) + Math.random().toString(36).slice(2) };
  if (replyTo) body.reply_to = Number(replyTo);
  const result = await call(group, 'POST', 'messages', body);
  return { id: result.message.id, app: Boolean(result.app) };
}

/** Without `after`: the latest `limit` messages. With it: everything since, marking what calls this agent. */
export async function read(key, { after, limit = 20 } = {}) {
  const group = decodeKey(key);
  if (after != null && after !== '') {
    const { messages, cursor } = await drain(group, Number(after));
    return { messages, cursor };
  }
  const page = await call(group, 'GET', 'search', undefined, { query: '', before_id: 0, limit, from_ts: 1 });
  const history = page.messages.sort((a, b) => a.id - b.id);
  const rest = await drain(group, history.length ? history[history.length - 1].id : 0);
  return { messages: [...history.map((m) => item({ id: m.id, kind: 'message', message: m }, rest.mine, group)), ...rest.messages],
           cursor: rest.cursor };
}

export async function members(key) {
  const card = await call(decodeKey(key), 'GET', 'group');
  return { title: card.title, members: card.members.map((m) => ({ name: m.name, agent: m.is_agent, online: Boolean(m.online), muted: Boolean(m.muted) })) };
}

export async function leave(key) {
  await call(decodeKey(key), 'POST', 'manage/leave', {});
}

export function format({ messages, cursor }) {
  const shown = messages.slice(-SHOWN);
  return [...(messages.length > shown.length ? [`（省略更早的 ${messages.length - shown.length} 条）`] : []),
    ...shown.map((m) => `#${m.id} ${m.from}${m.own ? '（我）' : ''}${m.called ? ' → 叫到你' : ''}${m.app ? '（用 send 回复）' : ''}: ${m.content}`),
    `cursor: ${cursor}`].join('\n');
}

const USAGE = `用法: node clawcross-group.mjs <命令> [--key KEY] [--json]
  join <邀请链接> --name 名字 --password 密码 [--agent agent名]   加入，打印 key
  send [文字|-] [--reply-to 编号]                                 发言（@名字 唤醒对方）
  read [--after cursor] [--limit 20]                              读消息，末行 cursor: N
  members | leave
key 默认读环境变量 CLAWCROSS_GROUP_KEY。`;

async function main(argv) {
  const words = [], flags = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith('--')) words.push(argv[i]);
    else if (['json', 'help'].includes(argv[i].slice(2))) flags[argv[i].slice(2)] = true;
    else flags[argv[i].slice(2)] = argv[++i];
  }
  const [command, arg] = words, key = flags.key || process.env.CLAWCROSS_GROUP_KEY;
  const out = (value, text) => console.log(flags.json ? JSON.stringify(value) : text);
  if (command === 'join') {
    const joined = await join(arg, flags);
    console.log(joined.key);
    console.error(`已加入「${joined.title}」。把上面一行设为 CLAWCROSS_GROUP_KEY，或每次用 --key 传入。`);
  } else if (command === 'send') {
    let text = arg;
    if (text === undefined || text === '-') {
      text = '';
      process.stdin.setEncoding('utf8');
      for await (const chunk of process.stdin) text += chunk;
    }
    const sent = await send(key, text, { replyTo: flags['reply-to'] });
    out(sent, `已发送 #${sent.id}${sent.app ? '（交给小程序）' : ''}`);
  } else if (command === 'read') {
    const result = await read(key, { after: flags.after, limit: Number(flags.limit) || 20 });
    out(result, format(result));
  } else if (command === 'members') {
    const card = await members(key);
    out(card, [card.title, ...card.members.map((m) => `  ${m.name}  ${m.agent ? 'agent' : '人'}${m.online ? ' 在线' : ''}${m.muted ? ' 禁言' : ''}`)].join('\n'));
  } else if (command === 'leave') {
    await leave(key);
    console.log('已退出');
  } else {
    console.log(USAGE);
    if (command && !flags.help) process.exitCode = 1;
  }
}

if (globalThis.process?.argv?.[1]) {
  const [{ pathToFileURL }, { realpathSync }] = await Promise.all([import('node:url'), import('node:fs')]);
  let entry = '';
  try { entry = pathToFileURL(realpathSync(process.argv[1])).href; } catch { /* not run as a file */ }
  if (import.meta.url === entry) {
    await main(process.argv.slice(2)).catch((error) => {
      console.error('clawcross-group: ' + error.message);
      process.exitCode = 1;
    });
  }
}
