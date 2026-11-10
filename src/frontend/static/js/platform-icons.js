/* Shared, locally hosted platform marks. SVG identifiers are allowlisted. */
(() => {
  const brands=new Set(['amp','antigravity','claude','cline','codebuddy','codex','copilot','cursor','gemini','goose','junie','kilocode','kimi','kiro','minimax','openclaw','opencode','qoder','qwen','trae','zai']);
  const aliases={'claude-code':'claude','gemini-cli':'gemini','cursor-agent':'cursor','github-copilot':'copilot','qwen-code':'qwen',kilo:'kilocode',zcode:'zai'};
  function markup(platform){
    let key=String(platform||'webot').toLowerCase();key=aliases[key]||key;
    if(brands.has(key))return `<svg class="platform-mark studio-platform-icon" data-platform="${key}" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/platform-icons.svg?v=20261108#${key}"/></svg>`;
    const bot=['webot','internal'].includes(key);
    return `<svg class="platform-mark studio-platform-icon" data-platform="${bot?'webot':'acp'}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${bot?'<rect x="4" y="6" width="16" height="14" rx="4"/><path d="M12 3v3M2 12h2m16 0h2M9 11v3m6-3v3m-6 3h6"/>':'<rect x="3" y="4" width="18" height="16" rx="4"/><path d="m7 9 3 3-3 3m6 0h4"/>'}</svg>`;
  }
  window.PlatformIcons={markup};
})();
