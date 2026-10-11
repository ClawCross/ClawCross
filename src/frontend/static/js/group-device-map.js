/* Group members as a picture: one card per connected device with its person and agents, linked to the
   group in the middle, online devices in colour. Shared by the guest page and the message center.
   Positions are set through the CSSOM and SVG attributes only, so it runs under a strict CSP. */
(() => {
  'use strict';
  const SVG = 'http://www.w3.org/2000/svg';
  const ICONS = {
    client: [['path', { d: 'M5 5h14a1 1 0 0 1 1 1v9H4V6a1 1 0 0 1 1-1z' }], ['path', { d: 'M2 18.5h20' }]],
    browser: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M3 12h18' }], ['path', { d: 'M12 3c2.6 2.5 3.8 5.5 3.8 9s-1.2 6.5-3.8 9c-2.6-2.5-3.8-5.5-3.8-9S9.4 5.5 12 3z' }]],
    person: [['circle', { cx: 12, cy: 8, r: 4 }], ['path', { d: 'M5 20a7 7 0 0 1 14 0' }]],
    agent: [['rect', { x: 4, y: 8, width: 16, height: 11, rx: 3 }], ['path', { d: 'M12 4v4M2 13v3M22 13v3M9 13v1.5M15 13v1.5' }]],
    group: [['circle', { cx: 9, cy: 8, r: 3.5 }], ['path', { d: 'M3 20a6 6 0 0 1 12 0' }], ['path', { d: 'M16 4.7a3.5 3.5 0 0 1 0 6.6M17.5 14.3A6 6 0 0 1 21 20' }]]
  };
  const TEXT = {
    zh: { yours: { browser: '你的浏览器', client: '你的电脑' }, of: { browser: name => name + ' 的浏览器', client: name => name + ' 的电脑' },
      online: '在线', offline: '离线', summary: (devices, online) => `${devices} 台设备 · ${online} 台在线`, nobody: '没有人' },
    en: { yours: { browser: 'Your browser', client: 'Your computer' }, of: { browser: name => name + "'s browser", client: name => name + "'s computer" },
      online: 'Online', offline: 'Offline', summary: (devices, online) => `${devices} devices · ${online} online`, nobody: 'Nobody' }
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function shape(tag, attributes, className) {
    const element = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attributes || {})) element.setAttribute(key, value);
    if (className) element.setAttribute('class', className);
    return element;
  }
  function icon(name) {
    const svg = shape('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' }, 'dm-icon');
    for (const [tag, attributes] of ICONS[name]) svg.append(shape(tag, attributes));
    return svg;
  }

  /** Members grouped by the device that brought them; the viewer's own device comes first. */
  function devices(members) {
    const found = new Map();
    for (const m of members) {
      const id = m.device ?? m.connection_id ?? 'local';
      if (!found.has(id)) found.set(id, { kind: m.device_kind === 'browser' ? 'browser' : 'client', own: false, online: false, person: '', agents: [] });
      const device = found.get(id);
      const own = m.own ?? m.remote === false;
      if (own) device.own = true;
      if (m.online ?? own) device.online = true;
      if (m.is_agent) device.agents.push({ name: m.name || m.principal, platform: m.agent?.platform || m.platform || '' });
      else device.person = m.name || m.principal;
    }
    return [...found.values()].sort((a, b) => Number(b.own) - Number(a.own));
  }

  function card(device, text) {
    const element = node('div', 'dm-device' + (device.online ? '' : ' dm-offline') + (device.own ? ' dm-own' : ''));
    const owner = device.person || device.agents[0]?.name || text.nobody;
    const title = node('div', 'dm-device-title');
    title.append(icon(device.kind), node('span', '', device.own ? text.yours[device.kind] : text.of[device.kind](owner)));
    const people = node('div', 'dm-people');
    if (device.person) {
      const person = node('span', 'dm-chip dm-person');
      person.append(icon('person'), node('span', '', device.person));
      people.append(person);
    }
    for (const agent of device.agents) {
      const chip = node('span', 'dm-chip dm-agent');
      chip.append(icon('agent'), node('span', '', agent.name));
      if (agent.platform) chip.title = agent.platform;
      people.append(chip);
    }
    const state = node('div', 'dm-state', device.online ? text.online : text.offline);
    state.prepend(node('i', 'dm-dot'));
    element.append(title, people, state);
    return element;
  }

  function connect(root, lines, hub, cards) {
    const box = root.getBoundingClientRect(), center = hub.getBoundingClientRect();
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    lines.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
    lines.replaceChildren();
    const hubX = center.left + center.width / 2 - box.left, hubY = center.top + center.height / 2 - box.top;
    cards.forEach(({ element, online }, index) => {
      const rect = element.getBoundingClientRect();
      const x = rect.left + rect.width / 2 - box.left, y = (rect.top < center.top ? rect.bottom : rect.top) - box.top;
      const bend = (hubY - y) / 2;
      // From the device to the group: that is the way the dashes and the dot travel.
      const d = `M${x} ${y} C${x} ${y + bend} ${hubX} ${hubY - bend} ${hubX} ${hubY}`;
      lines.append(shape('path', { d }, 'dm-track'));
      if (!online) return;
      lines.append(shape('path', { d }, 'dm-flow'));
      if (reduced) return;
      // Invisible until its turn starts, then it fades in, travels and fades out.
      const timing = { dur: '4.8s', begin: `${(index % 5) * 0.9}s`, repeatCount: 'indefinite' };
      const packet = shape('circle', { r: 3.5, opacity: 0 }, 'dm-packet');
      packet.append(shape('animateMotion', { ...timing, path: d }),
        shape('animate', { ...timing, attributeName: 'opacity', values: '0;1;1;0', keyTimes: '0;0.12;0.85;1' }));
      lines.append(packet);
    });
  }

  /** Draws `members` of the group `title` into `container`; cheap to call after every poll. */
  function render(container, { title, members, lang }) {
    const text = TEXT[lang === 'en' ? 'en' : 'zh'];
    const list = devices(members || []);
    const key = JSON.stringify([title, lang, list]);
    if (container._deviceMapKey === key) return container._deviceMapRedraw?.();
    container._deviceMapKey = key;
    container._deviceMapWatch?.disconnect();

    const root = node('div', 'dm');
    const lines = shape('svg', { 'aria-hidden': 'true' }, 'dm-lines');
    const top = node('div', 'dm-row'), bottom = node('div', 'dm-row');
    const hub = node('div', 'dm-hub');
    const mark = node('span', 'dm-hub-icon');
    mark.append(icon('group'));
    hub.append(mark, node('strong', '', title || ''), node('small', '', text.summary(list.length, list.filter(d => d.online).length)));
    const cards = list.map((device, index) => {
      const element = card(device, text);
      (index < Math.ceil(list.length / 2) ? top : bottom).append(element);
      return { element, online: device.online };
    });
    root.append(lines, top, hub, bottom);
    container.replaceChildren(root);

    const redraw = () => { if (root.offsetParent !== null) connect(root, lines, mark, cards); };
    container._deviceMapRedraw = redraw;
    container._deviceMapWatch = new ResizeObserver(redraw);
    container._deviceMapWatch.observe(root);
    redraw();
  }

  window.GroupDeviceMap = { render };
})();
