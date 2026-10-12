/* A guest's own agent. It runs in this browser tab and is an ordinary agent member of the group,
   added through the same /relay calls a ClawCross uses. When a message wakes it, it gets the recent
   messages in one go, may keep a few files and run JavaScript in a locked worker, and its final text
   is posted to the group. One tab at a time runs it; it catches up when the page is opened again. */
(() => {
  'use strict';
  const AGENT_ID = 'browser';
  const CONTEXT_MESSAGES = 40, CONTEXT_CHARS = 12000, MAX_STEPS = 8, TOOL_RESULT_CHARS = 20000, RUN_SECONDS = 5;
  const NO_REPLY = 'NO_REPLY';
  const LIMITS = { files: 200, bytes: 5 * 1024 * 1024, fileBytes: 1024 * 1024 };
  const el = id => document.getElementById(id);
  const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch (_) { return fallback; } };
  const write = (key, value) => { try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} };
  const clip = text => text.length > TOOL_RESULT_CHARS ? text.slice(0, TOOL_RESULT_CHARS) + '\n…（已截断）' : text;
  const time = seconds => new Date(seconds * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

  // ── Workspace: a few text files in IndexedDB, one folder per group ──
  let opened = null;
  function database() {
    return opened ??= new Promise((resolve, reject) => {
      const request = indexedDB.open('clawcross-agent-files', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('files', { keyPath: ['folder', 'path'] }).createIndex('folder', 'folder');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async function call(mode, body) {
    const db = await database();
    return new Promise((resolve, reject) => {
      const request = body(db.transaction('files', mode).objectStore('files'));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  function cleanPath(path) {
    const clean = String(path).trim().replace(/^\.?\/+/, '');
    if (!clean || clean.length > 200 || /[\u0000-\u001f\\]/.test(clean) || clean.split('/').some(part => !part || part === '.' || part === '..')) {
      throw new Error('文件路径无效：' + path);
    }
    return clean;
  }
  function workspace(folder) {
    const files = {
      async list() {
        const rows = await call('readonly', store => store.index('folder').getAll(folder));
        return rows.map(({ path, size, updated_at }) => ({ path, size, updated_at })).sort((a, b) => a.path.localeCompare(b.path));
      },
      async read(path) {
        const row = await call('readonly', store => store.get([folder, cleanPath(path)]));
        if (!row) throw new Error('文件不存在：' + path);
        return row.content;
      },
      async write(path, content) {
        const clean = cleanPath(path), size = new TextEncoder().encode(content).length;
        if (size > LIMITS.fileBytes) throw new Error('单个文件最大 1 MB');
        const others = (await files.list()).filter(file => file.path !== clean);
        if (others.length >= LIMITS.files) throw new Error('工作区最多 200 个文件');
        if (others.reduce((sum, file) => sum + file.size, 0) + size > LIMITS.bytes) throw new Error('工作区总大小最多 5 MB');
        await call('readwrite', store => store.put({ folder, path: clean, content, size, updated_at: Date.now() / 1000 }));
      },
      async remove(path) { await call('readwrite', store => store.delete([folder, cleanPath(path)])); },
      async clear() { for (const file of await files.list()) await files.remove(file.path); }
    };
    return files;
  }

  /** A fresh worker per snippet, which can't reach the network or this site's storage. */
  function runJs(code, files) {
    return new Promise(resolve => {
      const worker = new Worker('/group-guest-worker.js');
      const done = value => { clearTimeout(timer); worker.terminate(); resolve(JSON.stringify(value)); };
      const timer = setTimeout(() => done({ ok: false, error: `运行超过 ${RUN_SECONDS} 秒，已停止` }), RUN_SECONDS * 1000);
      worker.onmessage = event => done(event.data);
      worker.onerror = event => { event.preventDefault(); done({ ok: false, error: event.message || '沙箱加载失败' }); };
      worker.postMessage({ code, files });
    });
  }

  const tool = (name, description, properties) => ({ type: 'function', function: { name, description, strict: true,
    parameters: { type: 'object', properties, required: Object.keys(properties), additionalProperties: false } } });
  const string = { type: 'string' };
  const TOOLS = [
    tool('list_files', '列出工作区里的文件、大小（字节）和修改时间。', {}),
    tool('read_file', '读取工作区里的一个文本文件。', { path: string }),
    tool('write_file', '新建一个文本文件，或整个覆盖已有文件。工作区最多 200 个文件、共 5 MB。', { path: string, content: string }),
    tool('edit_file', '把文件中只出现一次的 old_text 换成 new_text。', { path: string, old_text: string, new_text: string }),
    tool('delete_file', '删除工作区里的一个文件。', { path: string }),
    tool('run_js', `在隔离沙箱里运行 JavaScript：code 是 async 函数体，可以 await，用 return 返回结果，console.log 的输出也会带回；不能联网，最长 ${RUN_SECONDS} 秒。files 列出的工作区文件以 files["路径"] 字符串传入。`,
      { code: string, files: { type: 'array', items: string } })
  ];

  /** Starts the agent of this page's group. `token` reads the guest credential, `refresh` polls the chat. */
  function start({ ticket, token, refresh }) {
    let group = '', title = '', owner = '', members = [], messages = [], requests = [], files = null;
    let holder = false, locking = false, busy = false, failure = '', state = { state: 'off' }, view = 'main';
    const settings = () => group ? read('group-guest-agent:' + group, null) : null;
    const saveSettings = value => write('group-guest-agent:' + group, value);
    const cursor = () => read('group-guest-agent-cursor:' + group, null);
    const saveCursor = value => write('group-guest-agent-cursor:' + group, value);
    const self = () => members.find(m => m.own && m.is_agent);

    async function relay(path, body) {
      const response = await fetch('/relay/' + path, { method: 'POST', credentials: 'omit',
        headers: { 'Content-Type': 'application/json', 'X-Group-Invite': ticket, Authorization: 'Bearer ' + token() },
        body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      const detail = data.detail ?? data.error;
      if (!response.ok) throw new Error(typeof detail === 'string' ? detail : '群服务暂时不可用');
      return data;
    }

    function say(next) {
      if (JSON.stringify(next) === JSON.stringify(state)) return;
      state = next;
      render();
    }

    function lock() {
      if (locking) return;
      locking = true;
      if (!navigator.locks) { holder = true; return; }
      navigator.locks.request('clawcross-agent:' + group, { ifAvailable: true }, granted => {
        if (!granted) return say({ state: 'elsewhere' });
        holder = true;
        void kick();
        return new Promise(() => {});  // held while the page is open
      });
    }

    async function kick() {
      const config = settings(), me = self();
      if (!config || !me) return say({ state: 'off' });
      if (!holder || busy) return;
      if (config.paused) return say({ state: 'paused' });
      const latest = Math.max(messages.length ? messages[messages.length - 1].id : 0, requests.length ? requests[requests.length - 1].id : 0);
      const saved = cursor();
      // A new agent, or one set up again on this device, starts from now rather than the history.
      if (saved === null) saveCursor(latest);
      const from = saved ?? latest;
      // In a space with an app, the app asks this agent directly; otherwise mentions wake it.
      const ask = requests.find(r => r.id > from && r.agent === me.principal);
      if (!ask && !messages.some(m => m.id > from && (m.targets || []).includes(me.principal))) {
        return say(failure ? { state: 'error', detail: failure } : { state: 'idle' });
      }
      busy = true;
      say({ state: 'replying' });
      try {
        if (ask) await answer(config, me, ask);
        else await turn(config, me, latest);
        failure = '';
      } catch (error) {
        failure = error.message;
      } finally {
        saveCursor(ask ? ask.id : latest);
        busy = false;
      }
      return kick();
    }

    async function turn(config, me, upto) {
      const reply = await respond(config, [{ role: 'system', content: instructions(config, me) }, { role: 'user', content: transcript(me) }]);
      if (reply) await relay('messages', { content: reply.slice(0, 60000), agent_id: AGENT_ID, client_msg_id: 'browser:' + upto });
    }

    /** One request from the space's app; the reply goes back to the app. */
    async function answer(config, me, ask) {
      const system = [
        `你是空间「${title}」里的成员「${me.name}」，运行在${owner}的浏览器里。`,
        config.persona.trim() ? '你的人设：' + config.persona.trim() : '',
        '这个空间的小程序直接向你提出请求。你最终输出的文字会原样作为回复交给小程序，按请求里要求的格式写。',
        '需要时可以用工具读写你的工作区文件，或运行 JavaScript 计算；工具结果只有你看得到。'
      ].filter(Boolean).join('\n');
      const reply = await respond(config, [{ role: 'system', content: system }, { role: 'user', content: ask.content }]);
      if (reply) await relay('messages', { content: reply.slice(0, 60000), agent_id: AGENT_ID, client_msg_id: 'browser-app:' + ask.id });
    }

    /** The model's final text after any tool calls; empty when it chose not to reply. */
    async function respond(config, conversation) {
      let reply = '';
      for (let step = 1; step <= MAX_STEPS; step++) {
        const message = (await complete(config, conversation, step < MAX_STEPS)).choices?.[0]?.message;
        if (!message) throw new Error('模型没有返回内容');
        if (!message.tool_calls?.length) { reply = (message.content || '').trim(); break; }
        conversation.push(message);
        for (const call of message.tool_calls) conversation.push({ role: 'tool', tool_call_id: call.id, content: await useTool(call) });
      }
      return reply === NO_REPLY ? '' : reply;
    }

    function instructions(config, me) {
      return [
        `你是群聊「${title}」里的成员「${me.name}」，运行在${owner}的浏览器里。`,
        config.persona.trim() ? '你的人设：' + config.persona.trim() : '',
        '每次被唤醒时，你会收到群里最近的消息，按时间从早到晚排列，最后几条是新的。',
        '你最终输出的文字会原样作为你的一条群消息发出：直接写要说的话，不要加名字或时间前缀。',
        '需要时可以用工具读写你的工作区文件，或运行 JavaScript 计算；工具结果只有你看得到，要告诉群里的内容写在最终回复里。',
        `如果这次不需要你发言，只输出 ${NO_REPLY}。`
      ].filter(Boolean).join('\n');
    }

    function transcript(me) {
      const agents = new Set(members.filter(m => m.is_agent).map(m => m.principal));
      const lines = [];
      let size = 0;
      for (const m of messages.slice(-CONTEXT_MESSAGES).reverse()) {
        const who = m.sender === me.principal ? m.sender_name + '（你）' : agents.has(m.sender) ? m.sender_name + '（agent）' : m.sender_name;
        const quote = m.reply ? `[引用 ${m.reply.sender_name}：${String(m.reply.content).slice(0, 200)}]\n` : '';
        const line = `#${m.id} ${who} ${time(m.created_at)}：\n${quote}${m.content}`;
        size += line.length;
        if (lines.length && size > CONTEXT_CHARS) break;
        lines.unshift(line);
      }
      return '群里最近的消息：\n\n' + lines.join('\n\n');
    }

    async function complete(config, conversation, tools) {
      let response;
      try {
        response = await fetch(config.base_url.replace(/\/+$/, '') + '/chat/completions', { method: 'POST', credentials: 'omit',
          headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + config.api_key },
          body: JSON.stringify({ model: config.model, messages: conversation, tools: TOOLS, tool_choice: tools ? 'auto' : 'none' }) });
      } catch (_) {
        throw new Error('连不上模型服务：地址不对，或这家服务不允许浏览器直接调用');
      }
      const data = await response.json().catch(() => null);
      if (!response.ok || !data) throw new Error(String(data?.error?.message ?? '模型服务返回 ' + response.status));
      return data;
    }

    async function useTool(call) {
      let args;
      try { args = JSON.parse(call.function.arguments || '{}'); } catch (_) { return '失败：参数不是有效的 JSON'; }
      const arg = key => { if (typeof args[key] !== 'string') throw new Error('缺少参数 ' + key); return args[key]; };
      try {
        switch (call.function.name) {
          case 'list_files': { const list = await files.list(); return list.length ? JSON.stringify(list) : '工作区是空的'; }
          case 'read_file': return clip(await files.read(arg('path')));
          case 'write_file': await files.write(arg('path'), arg('content')); return '已保存';
          case 'edit_file': {
            const path = arg('path'), before = await files.read(path), old = arg('old_text');
            const count = old ? before.split(old).length - 1 : 0;
            if (count !== 1) return count ? `失败：old_text 出现了 ${count} 次，请给出更长的唯一片段` : '失败：文件里没有 old_text';
            await files.write(path, before.replace(old, () => arg('new_text')));
            return '已修改';
          }
          case 'delete_file': await files.remove(arg('path')); return '已删除';
          case 'run_js': {
            const handed = {};
            for (const path of Array.isArray(args.files) ? args.files : []) if (typeof path === 'string') handed[path] = await files.read(path);
            return clip(await runJs(arg('code'), handed));
          }
          default: return '失败：没有工具 ' + call.function.name;
        }
      } catch (error) {
        return '失败：' + error.message;
      }
    }

    // ── The 我的 Agent dialog ──
    const statusText = () => ({
      off: self() ? '这个 agent 的设置不在这个浏览器里。重新设置后，就由这个浏览器运行它。' : '还没有设置',
      idle: '运行中。关掉这个页面后它会离线，再打开时会一次处理期间的新消息。',
      replying: '正在回复…',
      paused: '已暂停',
      elsewhere: '它正在这个浏览器的另一个标签页里运行。',
      error: '出错：' + (state.detail || '')
    })[state.state];

    function show(next) {
      view = next;
      render();
    }

    function render() {
      const config = settings(), me = self();
      el('agent-open').classList.toggle('running', Boolean(me) && ['idle', 'replying'].includes(state.state));
      el('agent-open').classList.toggle('failing', state.state === 'error');
      if (!el('agent-dialog').open) return;
      const main = view === 'main' && config && me;
      el('agent-main').hidden = !main;
      el('agent-form').hidden = !(view === 'edit' || (view === 'main' && !main));
      el('agent-files').hidden = view !== 'files';
      if (main) {
        el('agent-status').textContent = `${me.name} · ${config.model}：${statusText()}`;
        el('agent-status').classList.toggle('error', state.state === 'error');
        el('agent-pause').textContent = config.paused ? '继续' : '暂停';
      }
    }

    function fail(error) {
      el('agent-error').textContent = error ? error.message || String(error) : '';
      el('agent-error').hidden = !error;
    }

    function openForm() {
      const config = settings(), me = self();
      el('agent-elsewhere').hidden = !(me && !config);
      el('agent-name').value = config?.name ?? me?.name ?? owner + '的助手';
      el('agent-persona').value = config?.persona ?? '';
      el('agent-base').value = config?.base_url ?? '';
      el('agent-model').value = config?.model ?? '';
      el('agent-key').value = config?.api_key ?? '';
      el('agent-save').textContent = me ? '保存并在这里运行' : '创建 Agent';
      show('edit');
    }

    async function listFiles() {
      const list = await files.list();
      el('agent-file-list').replaceChildren(...list.map(file => {
        const li = document.createElement('li');
        const name = document.createElement('button');
        name.type = 'button'; name.className = 'file-name'; name.textContent = file.path;
        name.addEventListener('click', () => act(async () => editFile(file.path, await files.read(file.path), false)));
        const size = document.createElement('small');
        size.textContent = file.size < 1024 ? file.size + ' B' : Math.ceil(file.size / 1024) + ' KB';
        const download = document.createElement('button');
        download.type = 'button'; download.textContent = '下载';
        download.addEventListener('click', () => act(async () => {
          const url = URL.createObjectURL(new Blob([await files.read(file.path)], { type: 'text/plain;charset=utf-8' }));
          const link = document.createElement('a');
          link.href = url; link.download = file.path.split('/').pop() || 'file.txt'; link.click();
          URL.revokeObjectURL(url);
        }));
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'danger'; remove.textContent = '删除';
        remove.addEventListener('click', () => confirm('删除 ' + file.path + '？') && act(async () => { await files.remove(file.path); await listFiles(); }));
        li.append(name, size, download, remove);
        return li;
      }));
      el('agent-files-empty').hidden = list.length > 0;
    }

    function editFile(path, content, isNew) {
      el('agent-file-editor').hidden = false;
      el('agent-file-path').value = path;
      el('agent-file-path').readOnly = !isNew;
      el('agent-file-content').value = content;
      el('agent-file-content').focus();
    }

    async function act(run) {
      fail(null);
      try { await run(); } catch (error) { fail(error); }
    }

    el('agent-open').addEventListener('click', () => {
      fail(null);
      el('agent-dialog').showModal();
      if (settings() && self()) show('main'); else openForm();
    });
    el('agent-close').addEventListener('click', () => el('agent-dialog').close());
    el('agent-edit').addEventListener('click', openForm);
    el('agent-pause').addEventListener('click', () => {
      const config = settings();
      if (!config) return;
      saveSettings({ ...config, paused: !config.paused });
      void kick();
      render();
    });
    el('agent-files-open').addEventListener('click', () => act(async () => { el('agent-file-editor').hidden = true; show('files'); await listFiles(); }));
    el('agent-files-back').addEventListener('click', () => show('main'));
    el('agent-file-new').addEventListener('click', () => editFile('', '', true));
    el('agent-file-cancel').addEventListener('click', () => { el('agent-file-editor').hidden = true; });
    el('agent-file-editor').addEventListener('submit', event => {
      event.preventDefault();
      act(async () => { await files.write(el('agent-file-path').value, el('agent-file-content').value); el('agent-file-editor').hidden = true; await listFiles(); });
    });
    el('agent-file-upload').addEventListener('click', () => el('agent-file-input').click());
    el('agent-file-input').addEventListener('change', event => {
      const file = event.target.files[0];
      event.target.value = '';
      if (file) act(async () => { await files.write(file.name, await file.text()); await listFiles(); });
    });
    el('agent-form').addEventListener('submit', event => {
      event.preventDefault();
      const button = el('agent-save');
      button.disabled = true;
      const config = { name: el('agent-name').value.trim(), persona: el('agent-persona').value, base_url: el('agent-base').value.trim(),
        model: el('agent-model').value.trim(), api_key: el('agent-key').value.trim(), paused: settings()?.paused ?? false };
      act(async () => {
        await relay('agents', { agent_id: AGENT_ID, name: config.name, platform: 'browser' });
        saveSettings(config);
        await refresh();
        show('main');
      }).finally(() => { button.disabled = false; });
    });
    el('agent-remove').addEventListener('click', () => {
      const me = self();
      if (!me || !confirm('移除这个 agent，并删除它在这个浏览器里的文件？')) return;
      act(async () => {
        await relay('manage/remove_member', { principal: me.principal });
        saveSettings(null);
        saveCursor(null);
        await files.clear();
        await refresh();
        el('agent-dialog').close();
      });
    });

    return {
      /** The chat after a poll: group identity, members, the newest messages and the app's requests. */
      update(data) {
        if (!group && data.identity_key) {
          group = data.identity_key;
          files = workspace(group);
          el('agent-open').hidden = false;
          lock();
        }
        title = data.title;
        owner = data.name;
        members = data.members;
        const seen = new Set(messages.map(m => m.id));
        messages = [...messages, ...data.messages.filter(m => !seen.has(m.id))].sort((a, b) => a.id - b.id).slice(-200);
        const asked = new Set(requests.map(r => r.id));
        requests = [...requests, ...(data.requests || []).filter(r => !asked.has(r.id))].sort((a, b) => a.id - b.id).slice(-50);
        if (!group) return;
        render();
        void kick();
      }
    };
  }

  window.GuestAgent = { start };
})();
