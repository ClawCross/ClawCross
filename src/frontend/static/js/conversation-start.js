/* Starting points are editable drafts. Opening one never creates an Agent or sends a request. */
(function () {
  'use strict';
  const copy = {
    zh: {
      eyebrow: '从一个想法开始', title: '今天，一起做点什么？',
      intro: '提个问题，交给我一项任务，或一起创造新的东西。',
      hint: '直接输入就好，细节可以边聊边完善。',
      ideas: [
        ['sparkles', '一起想办法', '理清思路，找到下一步', '我有一个想法，想和你一起梳理。请先问我最重要的一个问题。'],
        ['users', '组建协作团队', '让合适的 Agent 分工合作', '帮我组建一个协作团队。请先了解我的目标，再帮我安排合适的成员、分工和任务；需要删除内容或调整权限时先向我确认。'],
        ['window', '做一个小程序', '把想法变成可分享的体验', '我想做一个可以和朋友分享的 ClawCross 小程序。请先阅读项目的小程序开发说明，和我确认想法，再开始制作。']
      ]
    },
    en: {
      eyebrow: 'START WITH AN IDEA', title: 'What shall we make today?',
      intro: 'Ask a question, hand over a task, or create something together.',
      hint: 'Just start typing. We can work out the details together.',
      ideas: [
        ['sparkles', 'Think it through', 'Find clarity and a next step', 'I have an idea to work through. Start by asking me the most important question.'],
        ['users', 'Build a team', 'Bring the right Agents together', 'Help me build a team. Understand my goal first, then help arrange the right members, roles and tasks. Confirm deletions and permission changes with me first.'],
        ['window', 'Make a mini app', 'Create an experience to share', 'Help me build a ClawCross mini app to share with friends. Read the project’s mini app development guide first, then discuss my idea before building it.']
      ]
    }
  };
  const text = () => copy[document.documentElement.lang.startsWith('en') ? 'en' : 'zh'];
  const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const keys = ['idea', 'team', 'app'];
  function welcome({label = '', note = ''} = {}) {
    const c = text();
    return `<section class="conversation-welcome" data-conversation-welcome data-welcome-label="${escape(label)}" aria-labelledby="conversation-welcome-title">
      <div class="welcome-emblem"><img src="/static/assets/clawcross-mark.svg" alt=""></div>
      <p class="welcome-eyebrow">${escape(label || c.eyebrow)}</p>
      <h1 id="conversation-welcome-title">${escape(c.title)}</h1><p class="welcome-intro">${escape(c.intro)}</p>
      <div class="welcome-ideas">${c.ideas.map(([icon, title, desc], i) => `<button type="button" class="welcome-idea" data-compose="${keys[i]}">
        <svg class="ui-icon"><use href="/static/icons.svg#${icon}"/></svg><strong>${escape(title)}</strong><span>${escape(desc)}</span><span class="welcome-idea-arrow" aria-hidden="true">↗</span></button>`).join('')}</div>
      <p class="welcome-hint">${escape(note || c.hint)}</p></section>`;
  }
  function draft(key) {
    const index = keys.indexOf(key), input = document.getElementById('user-input');
    if (index < 0 || !input) return;
    const prompt = text().ideas[index][3];
    input.value = input.value.trim() ? input.value.trimEnd() + '\n' + prompt : prompt;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
  function consumeIntent() {
    const url = new URL(location.href), intent = url.searchParams.get('compose');
    if (!keys.includes(intent)) return;
    draft(intent);
    url.searchParams.delete('compose');
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-conversation-welcome] [data-compose]');
    if (button) draft(button.dataset.compose);
  });
  window.ConversationStart = {welcome, draft, consumeIntent};
})();
