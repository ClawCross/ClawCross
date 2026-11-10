/* Group invitations: one link per group. Opened in a browser it is the guest chat;
   pasted into "加入群聊" on another ClawCross it joins that device as a member. */
(() => {
  'use strict';
  const LINK_KEY = 'clawcross_group_invite_';

  async function request(method, path, body) {
    const response = await fetch('/proxy_groups' + path, {
      method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || '群聊请求失败');
    return data;
  }
  function remembered(gid) {
    try { return localStorage.getItem(LINK_KEY + gid) || ''; } catch (_) { return ''; }
  }
  function remember(gid, link) {
    try { link ? localStorage.setItem(LINK_KEY + gid, link) : localStorage.removeItem(LINK_KEY + gid); } catch (_) { /* private mode */ }
  }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function button(label, kind, onClick) {
    const node = el('button', 'gn-btn gn-btn-' + kind, label);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  }
  function dialog(title, subtitle) {
    const root = el('dialog', 'group-network-dialog');
    const head = el('header', 'gn-head');
    const titles = el('div');
    titles.append(el('h2', 'gn-title', title));
    if (subtitle) titles.append(el('p', 'gn-subtitle', subtitle));
    const close = el('button', 'gn-close');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭');
    close.innerHTML = '<svg class="ui-icon"><use href="/static/icons.svg#x"/></svg>';
    const body = el('div', 'gn-body');
    const status = el('p', 'gn-status');
    status.setAttribute('role', 'status');
    head.append(titles, close);
    root.append(head, body, status);
    const done = () => { root.close(); root.remove(); };
    close.addEventListener('click', done);
    root.addEventListener('cancel', () => root.remove());
    root.addEventListener('click', (event) => { if (event.target === root) done(); });
    document.body.append(root);
    root.showModal();
    return { root, body, status, done };
  }
  async function busy(control, status, label, task) {
    control.disabled = true;
    status.textContent = label;
    status.classList.remove('is-error');
    try { await task(); }
    catch (error) { status.textContent = error.message; status.classList.add('is-error'); }
    finally { control.disabled = false; }
  }
  function notify(message) {
    if (window.toast) window.toast(message); else window.alert(message);
  }
  const text = (zh, en) => document.documentElement.lang === 'zh-CN' ? zh : en;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function ensurePublicEntry(status) {
    const readStatus = async () => {
      const response = await fetch('/proxy_tunnel/status');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '公网状态查询失败');
      return data;
    };
    let state = await readStatus();
    if (state.public_domain) return state.public_domain;
    if (!window.confirm(text(
      '将连接 ClawCross Hub，获得可访问完整页面的专属子域名，并生成邀请链接。继续吗？',
      'Connect to ClawCross Hub for a device subdomain with full web access and create this invitation?'
    ))) throw new Error(text('已取消生成邀请链接', 'Invite creation cancelled'));

    if (!state.running) {
      status.textContent = text('正在连接 ClawCross Hub…', 'Connecting to ClawCross Hub…');
      const response = await fetch('/proxy_tunnel/start', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:'hub'})});
      const started = await response.json();
      if (!response.ok) throw new Error(started.error || 'Hub 连接失败');
    }
    for (let i = 0; i < 60; i++) {
      await wait(1500);
      state = await readStatus();
      if (state.public_domain) return state.public_domain;
    }
    throw new Error(text('Tunnel 已启动，但公网域名仍未就绪。请稍后重试。', 'The tunnel started, but its public URL is not ready yet. Try again shortly.'));
  }
  function changed(group) {
    window.dispatchEvent(new CustomEvent('group-network-changed', { detail: group }));
  }

  let decoder;
  function qrDecoder() {
    if (window.jsQR) return Promise.resolve(window.jsQR);
    if (!decoder) decoder = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/vendor/jsqr/jsQR.js?v=1.4.0';
      script.onload = () => resolve(window.jsQR);
      script.onerror = () => { script.remove(); decoder = null; reject(new Error('二维码识别组件加载失败，请重试或粘贴邀请链接')); };
      document.head.append(script);
    });
    return decoder;
  }
  function invitation(value) {
    try {
      const url = new URL(value.trim());
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password && !url.search &&
        url.pathname.replace(/\/+$/, '').endsWith('/group-guest') && url.hash.length > 1 ? url.href : '';
    } catch (_) { return ''; }
  }
  async function scanInvitation(onResult) {
    const view = dialog('扫一扫', '对准朋友分享的群二维码，或从相册中选择');
    const stage = el('div', 'gn-scan-stage');
    const video = el('video', 'gn-scan-video');
    video.muted = true; video.playsInline = true; video.setAttribute('aria-label', '扫码相机');
    stage.append(video, el('span', 'gn-scan-frame'));
    const camera = button('开启相机', 'primary', startCamera);
    const file = el('input'); file.type = 'file'; file.accept = 'image/*'; file.hidden = true;
    file.setAttribute('aria-label', '选择群二维码图片');
    const album = button('从相册选择', 'secondary', () => file.click());
    const actions = el('div', 'gn-actions'); actions.append(camera, album);
    view.body.append(stage, actions, file, el('p', 'gn-note', '图片仅在本机识别，不会上传。识别后请确认名字并加入。'));
    const canvas = document.createElement('canvas'), context = canvas.getContext('2d', {willReadFrequently:true});
    let stream, timer, stopped = false, serial = 0;
    function stopCamera() {
      serial++;
      clearTimeout(timer);
      stream?.getTracks().forEach(track => track.stop());
      stream = null; video.srcObject = null; camera.disabled = false;
    }
    const close = view.done;
    view.done = () => { stopped = true; stopCamera(); close(); };
    // All exits (X, Escape, backdrop, success) release the camera, including a late permission grant.
    const cleanup = () => { stopped = true; stopCamera(); };
    view.root.addEventListener('close', cleanup, {once:true});
    view.root.addEventListener('cancel', cleanup, {once:true});
    function accept(value) {
      const link = invitation(value);
      if (!link) { view.status.textContent = '这不是群聊邀请二维码，请重新扫描'; return false; }
      view.done(); onResult(link); return true;
    }
    function read(source, width, height, decode) {
      if (!width || !height) return '';
      const scale = Math.min(1, 1280 / Math.max(width, height));
      canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
      context.drawImage(source, 0, 0, canvas.width, canvas.height);
      return decode(context.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height)?.data || '';
    }
    async function startCamera() {
      stopCamera();
      const attempt = serial;
      camera.disabled = true; view.status.classList.remove('is-error');
      if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
        camera.disabled = false; view.status.textContent = '相机需要 HTTPS 或本机访问；也可以从相册选择二维码'; return;
      }
      view.status.textContent = '正在开启相机…';
      try {
        const decode = await qrDecoder();
        if (stopped || attempt !== serial) return;
        const acquired = await navigator.mediaDevices.getUserMedia({audio:false, video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}}});
        if (stopped || !view.root.isConnected || attempt !== serial) { acquired.getTracks().forEach(track => track.stop()); return; }
        stream = acquired; video.srcObject = stream; await video.play();
        if (stopped || attempt !== serial) return;
        view.status.textContent = '将二维码放入取景框';
        const frame = () => {
          if (stopped || attempt !== serial || !view.root.isConnected) return;
          try {
            const value = read(video, video.videoWidth, video.videoHeight, decode);
            if (value && accept(value)) return;
          } catch (_) { /* The next frame may be ready. */ }
          timer = setTimeout(frame, 240);
        };
        frame();
      } catch (_) {
        if (stopped || attempt !== serial) return;
        stopCamera(); view.status.textContent = '无法使用相机，请允许相机权限或从相册选择二维码'; view.status.classList.add('is-error');
      }
    }
    file.addEventListener('change', async () => {
      const imageFile = file.files?.[0]; if (!imageFile) return;
      stopCamera(); album.disabled = true; view.status.classList.remove('is-error'); view.status.textContent = '正在识别图片…';
      const url = URL.createObjectURL(imageFile);
      try {
        const decode = await qrDecoder(), image = new Image();
        image.src = url; await image.decode();
        if (stopped || !view.root.isConnected) return;
        const value = read(image, image.naturalWidth, image.naturalHeight, decode);
        if (!value) throw new Error('没有识别到二维码，请选择清晰完整的群聊二维码图片');
        accept(value);
      } catch (error) {
        if (!stopped) { view.status.textContent = error.message; view.status.classList.add('is-error'); }
      } finally { URL.revokeObjectURL(url); album.disabled = false; file.value = ''; }
    });
    // Request camera access only after an explicit tap. Photo selection works without camera access.
  }

  window.GroupNetworkUI = {
    async rename(gid) {
      let group;
      try { group = await request('GET', '/' + encodeURIComponent(gid)); }
      catch (error) { notify(error.message); return; }
      const view = dialog('修改群名', '群号保持不变，成员和聊天记录都会保留');
      const input = el('input','gn-input');
      input.value = group.title || ''; input.maxLength = 160; input.setAttribute('aria-label','群名');
      const save = button('保存群名','primary',()=>busy(save,view.status,'正在保存…',async()=>{
        if (!input.value.trim()) throw new Error('请填写群名');
        const updated = await request('PATCH','/' + encodeURIComponent(gid),{title:input.value.trim()});
        view.done(); changed(updated);
      }));
      view.body.append(input,el('p','gn-note','群号：' + gid),save);
      input.focus();
    },

    async announcement(gid) {
      let group;
      try { group = await request('GET', '/' + encodeURIComponent(gid)); }
      catch (error) { notify(error.message); return; }
      const view = dialog('群公告', group.title || '');
      const input = el('textarea', 'gn-input');
      input.rows = 7; input.maxLength = 4000; input.value = group.announcement || '';
      input.setAttribute('aria-label', '群公告');
      input.readOnly = String(group.owner || '').startsWith('remote:');
      input.placeholder = input.readOnly ? '暂无群公告' : '写下群的介绍、约定或重要通知';
      view.body.append(input);
      if (!input.readOnly) {
        const save = button('保存公告','primary',()=>busy(save,view.status,'正在保存…',async()=>{
          const updated = await request('PATCH','/' + encodeURIComponent(gid),{announcement:input.value.trim()});
          view.done(); changed(updated);
        }));
        view.body.append(el('p','gn-note','成员及 Agent 都能看到公告；留空可清除。'),save);
      }
    },

    async teamInfo(team) {
      if (!team || team === '__default__') return;
      const path = '/v1/teams/' + encodeURIComponent(team);
      async function teamRequest(method,body) {
        const response=await fetch(path,{method,headers:{'Content-Type':'application/json'},credentials:'same-origin',cache:'no-store',...(body===undefined?{}:{body:JSON.stringify(body)})});
        const data=await response.json();
        if(!response.ok)throw new Error(data.detail || data.error || 'Team 请求失败');
        return data;
      }
      let info;
      try { info=await teamRequest('GET'); } catch(error){notify(error.message);return;}
      const view=dialog('Team 介绍', info.title || team);
      const input=el('textarea','gn-input');input.rows=7;input.maxLength=4000;input.value=info.description || '';
      input.setAttribute('aria-label','Team 介绍');input.placeholder='介绍项目的目标、分工或约定';
      const save=button('保存介绍','primary',()=>busy(save,view.status,'正在保存…',async()=>{
        await teamRequest('PATCH',{description:input.value.trim()});view.done();
        window.dispatchEvent(new CustomEvent('team-info-changed',{detail:{team}}));
      }));
      view.body.append(input,el('p','gn-note','所属 Agent 的动态块与详情工具会提供最新介绍。'),save);
    },

    search(gid) {
      const view = dialog('查找聊天记录','搜索本群全部历史消息');
      const form = el('form','gn-search-controls');
      const input = el('input','gn-input'); input.type='search'; input.maxLength=120; input.placeholder='搜索聊天内容'; input.setAttribute('aria-label','搜索聊天记录');
      const submit = el('button','gn-btn gn-btn-primary','搜索'); submit.type='submit';
      const main=el('div','gn-search-form');main.append(input,submit);
      const filters=el('div','gn-search-filters');
      const sender=el('select','gn-input');sender.setAttribute('aria-label','按成员筛选');sender.append(new Option('全部成员',''));
      const from=el('input','gn-input');from.type='date';from.setAttribute('aria-label','开始日期');
      const to=el('input','gn-input');to.type='date';to.setAttribute('aria-label','结束日期');
      for(const [label,field] of [['成员',sender],['开始日期',from],['结束日期',to]]){const row=el('label','gn-search-filter');row.append(el('span','',label),field);filters.append(row);}
      form.append(main,filters);
      request('GET','/'+encodeURIComponent(gid)).then(group=>{
        if(!view.root.isConnected)return;
        for(const member of group.members || [])sender.append(new Option(member.name || member.principal,member.principal));
      }).catch(()=>{});
      const results = el('div','gn-search-results');
      let before=0, query='', generation=0,criteria={};
      const messages = new Map();
      GroupMessageMenu.bind(results, id => messages.get(id), message => { view.done(); if(window.setGroupReply) window.setGroupReply({...message,group_id:gid}); });
      const more = button('更早的结果','secondary',()=>search(false)); more.hidden=true;
      async function search(reset) {
        const requestGeneration = reset ? ++generation : generation;
        if (reset) {
          query=input.value.trim();before=0;results.replaceChildren();messages.clear();more.hidden=true;
          const start=from.value?new Date(from.value+'T00:00:00'):null;
          const end=to.value?new Date(to.value+'T00:00:00'):null;if(end)end.setDate(end.getDate()+1);
          criteria={sender:sender.value,from_ts:start?Math.floor(start.getTime()/1000):0,to_ts:end?Math.floor(end.getTime()/1000)-1:0};
        }
        if (!query && !Object.values(criteria).some(Boolean)){view.status.textContent='输入关键词，或选择成员、日期。';return;}
        if(criteria.from_ts && criteria.to_ts && criteria.from_ts>criteria.to_ts){view.status.textContent='结束日期不能早于开始日期。';return;}
        await busy(reset ? submit : more, view.status,'正在查找…',async()=>{
          const data = await request('GET','/' + encodeURIComponent(gid) + '/search?' + new URLSearchParams({query,before_id:before,limit:50,...criteria}));
          if (!view.root.isConnected || generation !== requestGeneration) return;
          for (const message of data.messages || []) {
            if(messages.has(message.id))continue;
            const row=el('article','gn-search-result');
            const snippet=el('p');
            const content=String(message.content || '');const index=query?content.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()):-1;
            const start=Math.max(0,index-75),end=Math.min(content.length,Math.max(start+300,index+query.length+75));
            if(start)snippet.append(document.createTextNode('…'));
            if(index>=0){snippet.append(document.createTextNode(content.slice(start,index)),el('mark','',content.slice(index,index+query.length)),document.createTextNode(content.slice(index+query.length,end)));}
            else snippet.append(document.createTextNode(content.slice(start,end)));
            if(end<content.length)snippet.append(document.createTextNode('…'));
            const actions=el('div','gn-search-actions');
            actions.append(button('查看上下文','secondary',()=>window.GroupNetworkUI.context(gid,message,view.done)),button('引用回复','quiet',()=>{view.done();window.setGroupReply?.({...message,group_id:gid});}));
            row.append(el('strong','',message.sender_name || message.sender),el('small','',new Date(message.created_at * 1000).toLocaleString()),snippet,actions);
            messages.set(message.id,message); row.dataset.groupMessageId=message.id;row.tabIndex=0;
            results.append(row);
          }
          before=data.next_before_id || 0;more.hidden=!before;
          view.status.textContent=results.children.length ? `已找到 ${results.children.length} 条消息` : '没有找到匹配的消息';
        });
      }
      form.addEventListener('submit',event=>{event.preventDefault();search(true);});
      view.body.append(form,results,more);input.focus();
    },

    async context(gid,message,closeSearch) {
      const view=dialog('聊天记录','查看这条消息前后的对话');
      try {
        const data=await request('GET','/'+encodeURIComponent(gid)+'/messages/'+message.id+'/context');
        if(!view.root.isConnected)return;
        for(const item of data.messages || []){
          const row=el('article','gn-search-result');row.dataset.groupMessageId=item.id;
          if(Number(item.id)===Number(message.id))row.classList.add('gn-message-focus');
          row.append(el('strong','',item.sender_name || item.sender),el('small','',new Date(item.created_at*1000).toLocaleString()),el('p','',item.content));
          row.append(button('引用回复','quiet',()=>{view.done();closeSearch?.();window.setGroupReply?.({...item,group_id:gid});}));view.body.append(row);
        }
        view.body.querySelector('.gn-message-focus')?.scrollIntoView({block:'center'});
      } catch(error){view.status.textContent=error.message;view.status.classList.add('is-error');}
    },

    async app(gid) {
      const path = '/' + encodeURIComponent(gid);
      let group, catalog, run;
      try { [group, catalog, run] = await Promise.all([request('GET', path), request('GET', '/space-apps'), request('GET', path + '/app-run')]); }
      catch (error) { notify(error.message); return; }
      const view = dialog('小程序', '选一个小程序，由这台 ClawCross 运行；成员打开空间时看到它的页面');
      const state = el('p', 'gn-note'), problem = el('pre', 'gn-note gn-app-error');
      const pick = el('select', 'gn-input'), about = el('p', 'gn-note');
      pick.setAttribute('aria-label', '选择小程序');
      for (const [source, label] of [['mine', '我的小程序'], ['builtin', '自带示例']]) {
        const apps = catalog.apps.filter(app => app.source === source);
        if (!apps.length) continue;
        const group_ = el('optgroup'); group_.label = label;
        for (const app of apps) group_.append(new Option(app.name, app.id));
        pick.append(group_);
      }
      const describe = () => { about.textContent = catalog.apps.find(app => app.id === pick.value)?.about || ''; };
      const paint = () => {
        const name = run.name || run.app;
        state.textContent = run.status === 'running' ? '运行中 · ' + name + (group.app?.online ? ' · 已连上空间' : ' · 正在连接…')
          : run.status === 'starting' ? '正在重新启动 · ' + name
          : run.status === 'exited' ? '已停止 · ' + name
          : group.app ? '由群聊主机运行的小程序 · ' + (group.app.online ? '在线' : '未连接') : '还没有小程序，大家看到的是普通群聊';
        problem.textContent = run.status === 'exited' ? run.error || '' : '';
        problem.hidden = !problem.textContent;
        launch.textContent = run.app && run.status !== 'exited' ? '换成这个' : '启动';
        stop.hidden = !run.app;
        if (run.app && catalog.apps.some(app => app.id === run.app)) pick.value = run.app;
        describe();
      };
      const refresh = async () => { [group, run] = await Promise.all([request('GET', path), request('GET', path + '/app-run')]); paint(); };
      const launch = button('启动', 'primary', () => busy(launch, view.status, '正在启动…', async () => {
        run = await request('POST', path + '/app-run', { app: pick.value });
        view.status.textContent = ''; paint();
        await wait(2500); await refresh();
      }));
      // Stopping also takes the app off the space: it is plain group chat again.
      const stop = button('停止', 'quiet', () => busy(stop, view.status, '正在停止…', async () => {
        run = await request('DELETE', path + '/app-run'); view.status.textContent = '已停止，空间恢复为普通群聊'; await refresh(); changed(group);
      }));
      pick.addEventListener('change', describe);
      const actions = el('div', 'gn-actions'); actions.append(launch, stop);
      view.body.append(state, problem, pick, about, actions,
        el('p', 'gn-note', 'ClawCross 会一直运行它，重启后自动恢复；启动会替换空间当前连着的小程序，停止后空间恢复为普通群聊。把自己的小程序放进 ' + catalog.folder + '/<名字>/server.js，就会出现在这里。'));
      // Advanced: run an app server yourself, anywhere, with a key.
      view.body.append(el('p', 'gn-note', '小程序与群聊在同一主机运行。本地群聊可通过 Hub 公网链接分享；远程群聊的小程序由远程主机管理。'));
      paint();
    },

    scan() { return scanInvitation(link => this.join({link})); },

    join({link = ''} = {}) {
      const view = dialog('加入群聊', '扫描二维码，或粘贴朋友发来的邀请链接');
      const input = el('textarea', 'gn-input gn-link-input');
      input.rows = 3;
      input.placeholder = 'https://…/group-guest#…';
      input.autocomplete = 'off';
      input.spellcheck = false;
      input.value = link;
      input.setAttribute('aria-label', '群聊邀请链接');
      const scan = button('扫一扫 / 从相册识别', 'secondary', () => scanInvitation(value => {
        if (!view.root.isConnected) return;
        input.value = value; ready(); view.status.textContent = '已识别邀请链接，请确认后加入'; name.focus();
      }));
      const name = el('input', 'gn-input');
      name.placeholder = '你在群里的名字'; name.maxLength = 40; name.autocomplete = 'nickname'; name.setAttribute('aria-label', '你在群里的名字');
      name.value = window.__currentUser || '';
      const password = el('input', 'gn-input');
      password.type = 'password'; password.placeholder = '原身份的密码（选填）'; password.maxLength = 128;
      password.autocomplete = 'current-password'; password.setAttribute('aria-label', '密码');
      const advanced = el('details');
      advanced.append(el('summary', 'gn-note', '高级 · 恢复已有密码身份'), password,
        el('p', 'gn-note', '仅用于恢复以前设置过密码的身份。新加入的身份由本机自动保存凭证。'));
      const join = button('加入', 'primary', () => busy(join, view.status, '正在加入…', async () => {
        const fields = {invite: input.value.trim(), agents: [], name: name.value.trim()};
        if (password.value) fields.password = password.value;
        const group = await request('POST', '/join', fields);
        view.done();
        changed({...group,open_group:true});
      }));
      const ready = () => { join.disabled = !invitation(input.value) || !name.value.trim() || !!password.value && password.value.length < 6; };
      ready();
      for (const field of [input, name, password]) field.addEventListener('input', ready);
      view.body.append(scan, input, name, el('p', 'gn-note', '无需设置密码，凭证由 ClawCross 自动保存在本机。再次加入会沿用本机身份，其他设备不能仅凭同名接管。加入后可在群成员里添加自己的 Agent。'), join, advanced);
      setTimeout(() => input.focus(), 50);
    },

    async sharing(gid) {
      let group;
      try { group = await request('GET', `/${encodeURIComponent(gid)}`); }
      catch (error) { notify(error.message); return; }
      const owner = !String(group.owner || '').startsWith('remote:');
      const view = dialog('邀请朋友', group.title ? `「${group.title}」` : '');
      if (!owner) {
        view.body.append(el('p', 'gn-note', '只有群主可以生成邀请链接。请向群主要一个链接。'));
        return;
      }
      const link = el('input', 'gn-input gn-link');
      link.readOnly = true;
      link.placeholder = '还没有邀请链接';
      link.value = remembered(gid);
      const copy = button('复制链接', 'primary', async () => {
        try { await navigator.clipboard.writeText(link.value); view.status.textContent = '已复制'; }
        catch (_) { link.focus(); link.select(); view.status.textContent = '请手动复制'; }
      });
      const share = button('分享群聊', 'secondary', async () => {
        try { await navigator.share({title:group.title || '群聊邀请',text:'邀请你加入「' + (group.title || '群聊') + '」',url:link.value}); }
        catch (error) { if (error.name !== 'AbortError') view.status.textContent = '请复制链接或保存二维码后分享'; }
      });
      const create = button('', 'secondary', () => busy(create, view.status, '正在生成…', async () => {
        await ensurePublicEntry(view.status);
        await refreshTunnelControl();
        const data = await request('POST', `/${encodeURIComponent(gid)}/guest-link`, {});
        link.value = data.url;
        remember(gid, data.url);
        if(data.qr) showQr(data.qr);
        refresh();
        view.status.textContent = '新链接已生成';
      }));
      const stop = button('停止邀请', 'quiet', () => busy(stop, view.status, '正在停止…', async () => {
        await request('POST', `/${encodeURIComponent(gid)}/guest-link`, { disable: true });
        link.value = '';
        remember(gid, '');
        refresh();
        view.status.textContent = '邀请已停止；已加入的人不受影响';
      }));
      const qr = el('img','gn-qr'); qr.alt='扫描二维码加入群聊'; qr.hidden=true;
      const qrCaption = el('p','gn-note gn-qr-caption','扫码与打开下方链接完全相同'); qrCaption.hidden=true;
      const download = el('a','gn-btn gn-btn-secondary','保存二维码'); download.download='group-invite.svg'; download.hidden=true;
      function refresh() {
        const has = Boolean(link.value);
        if(!has) qr.hidden=qrCaption.hidden=download.hidden=true;
        copy.hidden = !has;
        share.hidden = !has || !navigator.share;
        stop.hidden = !has;
        create.textContent = has ? '换一个新链接' : '生成邀请链接';
        create.className = 'gn-btn ' + (has ? 'gn-btn-secondary' : 'gn-btn-primary');
      }
      refresh();
      const actions = el('div', 'gn-actions');
      actions.append(share, copy, create, stop);
      const publicNote = el('p', 'gn-note', text(
        '没有公网域名时，可连接 ClawCross Hub 获得专属 HTTPS 子域名，无需下载组件。Cloudflare 可在公网设置中手动选择。',
        'Connect to ClawCross Hub for a device HTTPS subdomain without extra downloads. Cloudflare remains an option in public access settings.'
      ));
      const cloudflare = el('div', 'gn-component-control');
      if (typeof window.componentControlMarkup === 'function') {
        cloudflare.textContent = text('默认服务：ClawCross Hub','Default service: ClawCross Hub');
      }
      const tunnelStatus = el('p', 'gn-note');
      const closeTunnel = button(text('关闭公网通道', 'Close public tunnel'), 'quiet', () => {
        if (!window.confirm(text(
          '关闭本机公网连接？通过此通道访问的页面和群聊会断线，设备地址保留供下次连接。',
          'Close this device’s public connection? Pages and groups using it will disconnect. The device address is retained for next time.'
        ))) return;
        busy(closeTunnel, view.status, text('正在关闭公网通道…', 'Closing the public tunnel…'), async () => {
          const response = await fetch('/proxy_tunnel/stop', {method:'POST'});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || response.statusText);
          await refreshTunnelControl();
          view.status.textContent = text('公网通道已关闭。', 'The public connection is closed.');
        });
      });
      closeTunnel.hidden = true;
      async function refreshTunnelControl() {
        try {
          const response = await fetch('/proxy_tunnel/status');
          const state = await response.json();
          if (!response.ok || !view.root.isConnected) return;
          closeTunnel.hidden = !state.running;
          tunnelStatus.textContent = state.running
            ? text('本机公网通道正在运行', 'This device’s public connection is running')
            : text('本机公网通道已关闭', 'This device’s public connection is closed');
        } catch (_) { tunnelStatus.textContent = text('公网通道状态暂时不可用', 'Public tunnel status is unavailable'); }
      }
      const steps = el('ul', 'gn-steps');
      for (const text of [
        '朋友扫码或打开链接，用名字和密码加入就能聊天，还能创建在自己浏览器里运行的 Agent，不需要主站账号。',
        '装了 ClawCross 的朋友在「加入群聊」粘贴链接，同样用名字和密码加入，还能带上自己的 agent。',
        '链接 30 天内可用来加入；换新链接或停止邀请后旧链接失效，已加入的人不受影响。',
      ]) steps.append(el('li', '', text));
      view.body.append(qr,qrCaption,download,link, publicNote, cloudflare, tunnelStatus, closeTunnel, actions, steps);
      if (typeof window.initComponentControls === 'function') window.initComponentControls(cloudflare);
      void refreshTunnelControl();
      // Cached links retain their exact QR payload; this does not rotate invitations.
      if (link.value) {
        try {
          const response = await fetch('/proxy_groups/' + encodeURIComponent(gid) + '/guest-qr', {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:link.value})});
          const data=await response.json();
          if(response.ok && data.qr) showQr(data.qr);
          else view.status.textContent=data.error || '二维码暂时不可用，可以复制链接加入';
        } catch (_) { view.status.textContent='二维码暂时不可用，可以复制链接加入'; }
      }
      function showQr(source) { qr.src=source;download.href=source;qr.hidden=qrCaption.hidden=download.hidden=false; }
    },

    async leave(gid) {
      if (!window.confirm('退出此群？你引入的 agent 也会退出。')) return;
      try { await request('POST', `/${encodeURIComponent(gid)}/leave`, {}); changed(); }
      catch (error) { notify(error.message); }
    },

    async toggleExternalAccess(gid) {
      try {
        const current = await request('GET', `/${encodeURIComponent(gid)}`);
        return await this.setExternalAccess(gid, current.external_access_enabled === false);
      } catch (error) { notify(error.message); }
    },

    async setExternalAccess(gid, enabled) {
      if (!enabled && !window.confirm(text(
        '暂停当前群的所有非主机连接？成员、凭证、邀请和历史全部保留，本机连接继续可用。恢复外部联网后可重新连接。',
        'Pause all non-host connections in this group? Members, credentials, invitations and history remain. Host connections continue working. External clients can reconnect when access resumes.'
      ))) return;
      try {
        const group = await request('POST', `/${encodeURIComponent(gid)}/external-access`, {enabled});
        changed(group);
        notify(enabled ? text('已恢复外部联网', 'External access resumed') : text('外部联网已暂停，成员和凭证保留', 'External access paused; members and credentials remain'));
      } catch (error) { notify(error.message); }
    },

    async disconnectExternal(gid) { return this.setExternalAccess(gid, false); },

    async removeLocal(gid, confirmRemoval = true) {
      if (confirmRemoval && !window.confirm(text(
        '从本机移除此群聊？将停止重连并清理连接令牌和聊天缓存。本机身份凭证、远端群聊及成员关系保留，之后可用原昵称再次加入。',
        'Remove this group from this device? Reconnection stops; the connection token and chat cache are deleted. Device login credentials and server membership remain so you can rejoin with your name.'
      ))) return false;
      try {
        await request('DELETE', `/${encodeURIComponent(gid)}/local`);
        remember(gid, '');
        changed({removed_group_id: gid});
        notify(text('群聊已从本机移除', 'Group removed from this device'));
        return true;
      } catch (error) { notify(error.message); return false; }
    },
  };
})();
