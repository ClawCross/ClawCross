const { test, expect } = require('@playwright/test');

async function installMockWebSocket(page) {
  await page.addInitScript(() => {
    const sockets = [];
    window.__clawcrossSocketUrls = [];
    window.__clawcrossSocketSends = [];

    class MockWebSocket {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      constructor(url) {
        this.url = url;
        this.readyState = MockWebSocket.CONNECTING;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        sockets.push(this);
        window.__clawcrossSocketUrls.push(url);
        setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          if (typeof this.onopen === 'function') this.onopen({ type: 'open' });
        }, 0);
      }

      send(data) {
        window.__clawcrossSocketSends.push({ url: this.url, data: String(data || '') });
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        if (typeof this.onclose === 'function') this.onclose({ type: 'close' });
      }
    }

    window.WebSocket = MockWebSocket;
    window.__emitClawcrossSocket = (payload) => {
      const data = JSON.stringify(payload);
      sockets.forEach((socket) => {
        if (socket.readyState === MockWebSocket.OPEN && typeof socket.onmessage === 'function') {
          socket.onmessage({ data });
        }
      });
    };
  });
}

async function stubStudioNetwork(page, calls, options = {}) {
  const webotState = {
    approvals: [
      {
        approval_id: 'approval-1',
        tool_name: 'run_command',
        status: 'pending',
        request_reason: 'Need shell access for verification',
      },
    ],
  };
  calls.workflowApply = calls.workflowApply || [];
  calls.teamPresetInstall = calls.teamPresetInstall || [];
  calls.teamPresetList = calls.teamPresetList || 0;
  calls.acpxEnsure = calls.acpxEnsure || [];
  calls.projectUpdateCheck = calls.projectUpdateCheck || 0;
  const acpxStatusPayload = options.acpxStatusPayload || { available: false, tools: [] };
  const acpxSessionsPayload = options.acpxSessionsPayload || { ok: true, sessions: [] };
  const acpxHistoryPayload = options.acpxHistoryPayload || { ok: true, history: { entries: [] } };
  const acpxEnsurePayload = options.acpxEnsurePayload || { ok: true, tool: 'cursor', session_key: 'main:cursor:main-session' };
  const acpxHistoryStatus = options.acpxHistoryStatus || 200;
  const acpxEnsureStatus = options.acpxEnsureStatus || 200;
  const currentRuntimeState = {
    status: 'success',
    session_id: 'main-session',
    session_role: 'main',
    workspace: '/tmp/clawcross/main',
    mode: { mode: 'execute', reason: 'Runtime panel smoke' },
    plan: {
      title: 'Execution swarm',
      status: 'active',
      items: [{ step: 'Keep runtime panel synced', status: 'in_progress' }],
      metadata: {
        workflow: {
          preset_id: 'execution_swarm',
          name: 'Execution Swarm',
          description: 'Use a planner/researcher/implementer/verifier split with inbox handoffs.',
          source: 'claw-code parity / browser-native swarm',
          mode: 'execute',
        },
      },
    },
    workflow_presets: [
      {
        preset_id: 'review_gate',
        name: 'Review Gate',
        description: 'Force reviewer discipline before completion.',
        mode: 'review',
      },
      {
        preset_id: 'execution_swarm',
        name: 'Execution Swarm',
        description: 'Use a planner/researcher/implementer/verifier split with inbox handoffs.',
        mode: 'execute',
      },
    ],
    active_workflow: {
      preset_id: 'execution_swarm',
      name: 'Execution Swarm',
      description: 'Use a planner/researcher/implementer/verifier split with inbox handoffs.',
      source: 'claw-code parity / browser-native swarm',
      mode: 'execute',
    },
    todos: {
      items: [{ title: 'Deliver inbox', status: 'pending' }],
    },
    verifications: [],
    approvals: [],
    inbox: [
      {
        message_id: 'inbox-1',
        source_label: 'planner',
        source_session: 'subagent__planner__ada',
        body: 'Need the current session to confirm rollout timing.',
        status: 'queued',
      },
    ],
    artifacts: [],
    runs: [],
    active_run: null,
    relationships: { parent_session: '', children: [] },
    memory: {
      summary: '3 entries · kairos off',
      project_slug: 'clawcross-main',
      entry_count: 3,
      can_dream: true,
      kairos_enabled: false,
      relevant_entries: [{ name: 'deploy_notes' }],
    },
    bridge: {
      status: 'detached',
      attached: false,
      connection_count: 0,
      sessions: [],
      primary: null,
    },
    voice: {
      enabled: false,
      auto_read_aloud: false,
      recording_supported: true,
      tts_model: 'gpt-4o-mini-tts',
      tts_voice: 'alloy',
      stt_model: 'whisper-1',
      last_transcript: '',
      status: 'disabled',
    },
    buddy: {
      compact_face: '^_^',
      name: 'Mochi',
      species: 'capybara',
      rarity: 'rare',
      personality: 'Calm but opinionated',
      reaction: 'Waiting by the prompt',
      available_actions: ['pet', 'bridge'],
    },
  };
  const subagentRuntimeState = {
    status: 'success',
    session_id: 'subagent__coder__curie',
    workspace: '/tmp/clawcross/worktree/curie',
    plan: {
      title: 'Review gate',
      status: 'active',
      items: [
        { step: 'Inspect WeBot runtime surface', status: 'completed' },
        { step: 'Verify approval roundtrip', status: 'in_progress' },
      ],
      metadata: {
        workflow: {
          preset_id: 'review_gate',
          name: 'Review Gate',
          description: 'Force reviewer discipline before completion.',
          source: 'oh-my-openagent / review gate',
          mode: 'review',
        },
      },
    },
    workflow_presets: [
      {
        preset_id: 'review_gate',
        name: 'Review Gate',
        description: 'Force reviewer discipline before completion.',
        mode: 'review',
      },
      {
        preset_id: 'execution_swarm',
        name: 'Execution Swarm',
        description: 'Use a planner/researcher/implementer/verifier split with inbox handoffs.',
        mode: 'execute',
      },
    ],
    active_workflow: {
      preset_id: 'review_gate',
      name: 'Review Gate',
      description: 'Force reviewer discipline before completion.',
      source: 'oh-my-openagent / review gate',
      mode: 'review',
    },
    todos: {
      items: [
        { title: 'Update docs', status: 'completed' },
        { title: 'Add smoke coverage', status: 'pending' },
      ],
    },
    verifications: [
      {
        verification_id: 'verify-curie-1',
        title: 'Flask proxy chain',
        status: 'passed',
        details: 'Integration proxy verified',
      },
    ],
    approvals: webotState.approvals,
    bridge: {
      status: 'detached',
      attached: false,
      connection_count: 0,
      sessions: [],
      primary: null,
    },
    voice: {
      enabled: false,
      auto_read_aloud: false,
      recording_supported: true,
      tts_model: 'gpt-4o-mini-tts',
      tts_voice: 'alloy',
      stt_model: 'whisper-1',
      last_transcript: '',
      status: 'disabled',
    },
    buddy: {
      compact_face: '^_^',
      name: 'Mochi',
      species: 'capybara',
      rarity: 'rare',
      personality: 'Calm but opinionated',
      reaction: 'Watching Curie work',
      available_actions: ['pet', 'bridge'],
    },
    memory: {
      summary: '2 entries · kairos off',
      project_slug: 'clawcross-curie',
      entry_count: 2,
      can_dream: true,
      kairos_enabled: false,
      relevant_entries: [{ name: 'runtime_gap_matrix' }],
    },
    artifacts: [],
    runs: [
      {
        run_id: 'run-curie-1',
        status: 'failed',
        title: 'Review gate verifier',
        recovery: {
          kind: 'approval_blocked',
          summary: 'Run is blocked on a permission or approval decision.',
          suggestion: 'Resolve the pending tool approval, then deliver the inbox or rerun the blocked step.',
        },
      },
    ],
    active_run: {
      run_id: 'run-curie-1',
      status: 'failed',
      title: 'Review gate verifier',
      recovery: {
        kind: 'approval_blocked',
        summary: 'Run is blocked on a permission or approval decision.',
        suggestion: 'Resolve the pending tool approval, then deliver the inbox or rerun the blocked step.',
      },
    },
    relationships: { parent_session: 'main-session', children: [] },
  };

  if (options.currentRuntime) Object.assign(currentRuntimeState, options.currentRuntime);
  if (options.subagentRuntime) Object.assign(subagentRuntimeState, options.subagentRuntime);
  const setupStatus = Object.assign(
    {
      llm_configured: true,
      openclaw_installed: true,
      antigravity_running: false,
      password_set: true,
      current_provider: 'openai',
      current_model: 'gpt-5.4',
      current_base_url: 'https://api.openai.com',
    },
    options.setupStatus || {}
  );
  const proxySettings = Object.assign(
    {
      LLM_PROVIDER: 'openai',
      LLM_API_KEY: '****saved-key',
      LLM_BASE_URL: 'https://api.openai.com',
      LLM_MODEL: 'gpt-5.4',
      TINYFISH_API_KEY: '****tinyfish',
      TINYFISH_BASE_URL: 'https://agent.tinyfish.ai',
      TINYFISH_MONITOR_DB_PATH: '/tmp/tinyfish_monitor.db',
      TINYFISH_MONITOR_TARGETS_PATH: '/tmp/tinyfish_targets.json',
      TINYFISH_MONITOR_ENABLED: 'false',
      TINYFISH_MONITOR_CRON: '',
    },
    options.proxySettings || {}
  );
  const importOpenClawPayload = Object.assign(
    {
      provider: 'openai',
      api_key: 'sk-imported',
      base_url: 'https://api.openai.com',
      model: 'gpt-5.4',
    },
    options.importOpenClawPayload || {}
  );
  const discoverModelsPayload = options.discoverModelsPayload || { models: ['gpt-5.4', 'gpt-4o'] };
  const exportOpenClawPayload = Object.assign(
    {
      ok: true,
      model_ref: 'openai/gpt-5.4',
      restarted: true,
    },
    options.exportOpenClawResponse || {}
  );

  const json = (route, payload, status = 200) =>
    route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });

  await page.route(/https:\/\/cdn\.tailwindcss\.com\/?.*/, (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: 'window.tailwind = window.tailwind || {};',
    })
  );
  await page.route(/https:\/\/cdnjs\.cloudflare\.com\/.*marked.*\.js/, (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: `
        window.marked = {
          __clawcrossConfigured: false,
          parse: (value) => value,
          setOptions() {},
        };
      `,
    })
  );
  await page.route(/https:\/\/cdnjs\.cloudflare\.com\/.*highlight.*\.js/, (route) =>
    route.fulfill({
      contentType: 'application/javascript',
      body: `
        window.hljs = {
          getLanguage() { return true; },
          highlight(code) { return { value: code }; },
          highlightElement() {},
        };
      `,
    })
  );
  await page.route(/https:\/\/cdnjs\.cloudflare\.com\/.*highlight.*\.css/, (route) =>
    route.fulfill({
      contentType: 'text/css',
      body: '',
    })
  );

  await page.route('**/proxy_check_session', (route) => json(route, { valid: true, user_id: 'smoke-user' }));
  await page.route('**/proxy_tools', (route) => json(route, []));
  await page.route('**/proxy_oasis/topics', (route) => json(route, []));
  await page.route('**/proxy_sessions', (route) => json(route, { sessions: [] }));
  await page.route('**/proxy_sessions_status', (route) => json(route, { sessions: [] }));
  await page.route('**/proxy_session_history', (route) => json(route, { messages: [] }));
  await page.route('**/proxy_tunnel/status', (route) => json(route, { running: false, public_domain: '' }));
  await page.route('**/teams', (route) => json(route, { teams: ['Smoke Team'] }));
  await page.route('**/proxy_visual/experts*', (route) => json(route, []));
  await page.route('**/proxy_openclaw_sessions', (route) => json(route, { available: true, agents: [] }));
  await page.route('**/proxy_acpx_status', (route) => json(route, acpxStatusPayload));
  await page.route('**/proxy_acpx_sessions?*', (route) => json(route, acpxSessionsPayload));
  await page.route('**/proxy_acpx_session_history?*', (route) => json(route, acpxHistoryPayload, acpxHistoryStatus));
  await page.route('**/proxy_acpx_session_ensure', async (route) => {
    calls.acpxEnsure.push(await route.request().postDataJSON());
    return json(route, acpxEnsurePayload, acpxEnsureStatus);
  });
  await page.route('**/proxy_webot_subagents', (route) =>
    json(route, {
      status: 'success',
      subagents: [
        {
          agent_id: 'worker-curie',
          name: 'Curie',
          session_id: 'subagent__coder__curie',
          agent_type: 'coder',
          description: 'Runtime smoke worker',
          parent_session: 'main-session',
          status: 'running',
          stored_status: 'running',
          updated_at: '2026-03-31T12:00:00',
          created_at: '2026-03-31T11:55:00',
          last_result: 'Collected runtime evidence.',
          workspace: '/tmp/clawcross/worktree/curie',
          latest_run: { run_id: 'run-curie-1', status: 'running' },
        },
      ],
    })
  );
  await page.route('**/proxy_webot_session_runtime?*', (route) =>
    {
      const url = new URL(route.request().url());
      const sessionId = url.searchParams.get('session_id') || '';
      if (sessionId === 'subagent__coder__curie') {
        return json(route, { ...subagentRuntimeState, session_id: sessionId });
      }
      currentRuntimeState.session_id = sessionId || currentRuntimeState.session_id || 'main-session';
      return json(route, { ...currentRuntimeState });
    }
  );
  await page.route('**/proxy_webot_workflow_apply', async (route) => {
    const payload = await route.request().postDataJSON();
    calls.workflowApply.push(payload);
    const workflow = {
      preset_id: payload.preset_id || 'review_gate',
      name: payload.preset_id === 'execution_swarm' ? 'Execution Swarm' : 'Review Gate',
      description:
        payload.preset_id === 'execution_swarm'
          ? 'Use a planner/researcher/implementer/verifier split with inbox handoffs.'
          : 'Force reviewer discipline before completion.',
      source:
        payload.preset_id === 'execution_swarm'
          ? 'claw-code parity / browser-native swarm'
          : 'oh-my-openagent / review gate',
      mode: payload.preset_id === 'execution_swarm' ? 'execute' : 'review',
    };
    const target =
      payload.session_id === 'subagent__coder__curie' ? subagentRuntimeState : currentRuntimeState;
    target.active_workflow = workflow;
    target.plan = {
      ...(target.plan || {}),
      title: workflow.name,
      metadata: { workflow },
    };
    target.mode = { mode: workflow.mode, reason: `workflow:${workflow.preset_id}` };
    return json(route, { status: 'success', preset: workflow });
  });
  await page.route('**/proxy_webot_tool_approval_resolve', async (route) => {
    calls.approvalActions.push(await route.request().postDataJSON());
    webotState.approvals = [
      {
        ...webotState.approvals[0],
        status: 'approved',
      },
    ];
    subagentRuntimeState.approvals = webotState.approvals;
    return json(route, {
      status: 'success',
      approval: {
        approval_id: 'approval-1',
        tool_name: 'run_command',
        status: 'approved',
        remember: true,
      },
    });
  });
  await page.route('**/proxy_webot_session_inbox_deliver', async (route) => {
    calls.inboxDeliveries = (calls.inboxDeliveries || 0) + 1;
    currentRuntimeState.inbox = currentRuntimeState.inbox.map(item => ({ ...item, status: 'delivered' }));
    return json(route, { status: 'success', delivered: currentRuntimeState.inbox.length });
  });
  await page.route('**/proxy_webot_voice', async (route) => {
    const payload = await route.request().postDataJSON();
    calls.voiceUpdates = calls.voiceUpdates || [];
    calls.voiceUpdates.push(payload);
    currentRuntimeState.voice = {
      ...currentRuntimeState.voice,
      enabled: !!payload.enabled,
      status: payload.enabled ? 'enabled' : 'disabled',
      auto_read_aloud: !!payload.auto_read_aloud,
      tts_model: payload.tts_model || currentRuntimeState.voice.tts_model,
      tts_voice: payload.tts_voice || currentRuntimeState.voice.tts_voice,
      stt_model: payload.stt_model || currentRuntimeState.voice.stt_model,
      last_transcript: payload.last_transcript || currentRuntimeState.voice.last_transcript,
    };
    return json(route, { status: 'success', voice: currentRuntimeState.voice });
  });
  await page.route('**/proxy_webot_bridge_attach', async (route) => {
    const payload = await route.request().postDataJSON();
    calls.bridgeAttach = (calls.bridgeAttach || 0) + 1;
    const sessionId = payload.session_id || currentRuntimeState.session_id || 'main-session';
    currentRuntimeState.session_id = sessionId;
    currentRuntimeState.bridge = {
      status: 'attached',
      attached: true,
      connection_count: 0,
      sessions: [
        {
          bridge_id: 'bridge-main-1',
          session_id: sessionId,
          role: payload.role || 'viewer',
          attach_code: 'ATTACH-42',
          websocket_path: '/webot/ws/smoke-user/bridge-main-1',
          status: 'attached',
          connection_count: 0,
        },
      ],
      primary: {
        bridge_id: 'bridge-main-1',
        session_id: sessionId,
        role: payload.role || 'viewer',
        attach_code: 'ATTACH-42',
        websocket_path: '/webot/ws/smoke-user/bridge-main-1',
        status: 'attached',
        connection_count: 0,
      },
    };
    return json(route, {
      status: 'success',
      bridge: currentRuntimeState.bridge.primary,
    });
  });
  await page.route('**/proxy_webot_bridge_detach', async (route) => {
    calls.bridgeDetach = (calls.bridgeDetach || 0) + 1;
    currentRuntimeState.bridge = {
      status: 'detached',
      attached: false,
      connection_count: 0,
      sessions: [],
      primary: null,
    };
    return json(route, {
      status: 'success',
      bridge: {
        bridge_id: 'bridge-main-1',
        status: 'detached',
      },
    });
  });
  await page.route('**/proxy_webot_kairos', async (route) => {
    const payload = await route.request().postDataJSON();
    calls.kairosUpdates = calls.kairosUpdates || [];
    calls.kairosUpdates.push(payload);
    currentRuntimeState.memory = {
      ...currentRuntimeState.memory,
      kairos_enabled: !!payload.enabled,
      summary: `3 entries · kairos ${payload.enabled ? 'on' : 'off'}`,
    };
    return json(route, { status: 'success', memory: currentRuntimeState.memory });
  });
  await page.route('**/proxy_webot_dream', async (route) => {
    calls.dreamRuns = (calls.dreamRuns || 0) + 1;
    currentRuntimeState.memory = {
      ...currentRuntimeState.memory,
      summary: '4 entries · kairos on · last dream just now',
      entry_count: 4,
      kairos_enabled: true,
      can_dream: false,
    };
    return json(route, { status: 'success', memory: currentRuntimeState.memory });
  });
  await page.route('**/proxy_webot_buddy', async (route) => {
    const payload = await route.request().postDataJSON();
    calls.buddyActions = calls.buddyActions || [];
    calls.buddyActions.push(payload);
    currentRuntimeState.buddy = {
      ...currentRuntimeState.buddy,
      reaction: 'Purring after a bridge sync',
    };
    return json(route, { status: 'success', buddy: currentRuntimeState.buddy });
  });
  await page.route('**/proxy_webot_tool_policy', async (route) => {
    if (route.request().method() !== 'GET') {
      return json(route, {
        status: 'success',
        policy: {
          default_approval: 'manual',
          tools: {
            run_command: { approval: 'manual' },
          },
        },
      });
    }
    return json(route, {
      status: 'success',
      policy: {
        default_approval: 'manual',
        tools: {
          run_command: { approval: 'manual' },
        },
        source: 'user',
        definition_path: '/tmp/clawcross/policy.json',
      },
    });
  });
  await page.route('**/api/setup_status', (route) =>
    json(route, setupStatus)
  );
  await page.route('**/proxy_settings_full', async (route) => {
    if (route.request().method() !== 'GET') {
      return json(route, { status: 'success', updated: [] });
    }
    return json(route, { settings: proxySettings });
  });
  await page.route('**/proxy_update_check', async (route) => {
    calls.projectUpdateCheck += 1;
    return json(route, {
      status: 'idle',
      deployment_mode: 'git',
      branch: 'main',
      current_short_commit: 'smoke',
      latest_short_commit: 'smoke',
      has_update: false,
      dirty: false,
    });
  });
  await page.route('**/api/tinyfish/status*', (route) =>
    json(route, {
      ok: true,
      config: {
        api_key_configured: true,
        targets_path_exists: true,
        base_url: 'https://agent.tinyfish.ai',
        targets_path: '/tmp/tinyfish_targets.json',
        db_path: '/tmp/tinyfish_monitor.db',
        cron: '',
        enabled: false,
      },
      pending_runs: 0,
      recent_runs: [],
      sites: [],
      recent_changes: [],
    })
  );
  await page.route('**/api/import_openclaw_config', (route) => {
    calls.importOpenClaw += 1;
    return json(route, importOpenClawPayload);
  });
  await page.route('**/api/team-presets', (route) => {
    calls.teamPresetList += 1;
    return json(route, {
      ok: true,
      presets: [
        {
          preset_id: 'ming-neige',
          name: '明朝内阁制',
          default_team_name: '明朝内阁制',
          role_count: 19,
          tags: ['ming', 'governance'],
          description: '明朝内阁制治理与部门联动预设。',
        },
        {
          preset_id: 'hanlin-novel-studio',
          name: '翰林院小说创作局',
          default_team_name: '翰林院小说创作局',
          role_count: 6,
          tags: ['creative', 'writing'],
          description: '翰林院创作型团队预设。',
        },
      ],
    });
  });
  await page.route('**/api/team-presets/install', async (route) => {
    const payload = await route.request().postDataJSON();
    calls.teamPresetInstall.push(payload);
    return json(route, {
      ok: true,
      team: payload.team,
      preset: {
        preset_id: payload.preset_id,
        name: payload.preset_id === 'hanlin-novel-studio' ? '翰林院小说创作局' : '明朝内阁制',
      },
      internal_agents: payload.preset_id === 'hanlin-novel-studio' ? 6 : 19,
      experts: payload.preset_id === 'hanlin-novel-studio' ? 6 : 19,
      workflow_files:
        payload.preset_id === 'hanlin-novel-studio'
          ? ['hanlin_novel_studio.yaml']
          : ['ming_neige_baseline.yaml', 'ming_neige_governance.yaml'],
    });
  });
  await page.route('**/api/discover_models', (route) => json(route, discoverModelsPayload));
  await page.route('**/api/export_openclaw_config', async (route) => {
    calls.exportOpenClaw += 1;
    const payload = await route.request().postDataJSON();
    calls.lastExportPayload = payload;
    return json(route, exportOpenClawPayload);
  });
  await page.route('**/api/tinyfish/run', async (route) => {
    calls.tinyfishRun += 1;
    return json(route, {
      ok: true,
      run_ids: ['run-smoke-1'],
    });
  });
}

test('studio workflow tab and settings actions stay responsive', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.removeItem('clawcrossStudioFirstVisitV2');
    localStorage.setItem('oasisTownModeEnabled', '1');
    localStorage.setItem('oasisTownWorkspaceView', 'graph');
  });

  await page.goto('/studio');

  await expect(page.locator('#chat-screen')).toBeVisible();
  await expect(page.locator('#tab-chat')).toHaveClass(/active/);
  await expect(page.locator('#oasis-panel')).toHaveClass(/collapsed-panel/);
  await expect(page.locator('#oasis-town-mode-btn')).toContainText('OFF');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('oasisTownModeEnabled'))).toBe('0');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('oasisTownWorkspaceView'))).toBe('town');

  await page.locator('#tab-orchestrate').click();
  await expect(page.locator('#tab-orchestrate')).toHaveClass(/active/);
  await expect(page.locator('#page-orchestrate')).toHaveClass(/active/);
  await expect.poll(async () => {
    return page.locator('#page-loading-overlay').evaluate((el) => el.classList.contains('loading-visible'));
  }).toBe(false);

  await page.locator('.hamburger-btn').click();
  await page.locator('#hamburger-panel button[onclick*="openSettings(); closeHamburgerMenu();"]').click();

  await expect(page.locator('#settings-modal')).toBeVisible();
  await expect(page.locator('#settings-export-openclaw-btn')).toBeEnabled();
  await expect(page.locator('#tinyfish-run-btn')).toBeVisible();

  await page.locator('#settings-import-openclaw-btn').click();
  await expect.poll(() => calls.importOpenClaw).toBe(1);

  await page.locator('#settings-export-openclaw-btn').click();
  await expect.poll(() => calls.exportOpenClaw).toBe(1);
  expect(calls.lastExportPayload).toMatchObject({
    provider: 'openai',
    base_url: 'https://api.openai.com',
    model: 'gpt-5.4',
  });

  await page.locator('#tinyfish-run-btn').click();
  await expect.poll(() => calls.tinyfishRun).toBe(1);

  expect(pageErrors).toEqual([]);
});

test('studio settings export button allows keyless ollama sync', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await stubStudioNetwork(page, calls, {
    setupStatus: {
      current_provider: 'ollama',
      current_model: 'llama3.2:latest',
      current_base_url: 'http://127.0.0.1:11434',
    },
    proxySettings: {
      LLM_PROVIDER: 'ollama',
      LLM_API_KEY: '',
      LLM_BASE_URL: 'http://127.0.0.1:11434',
      LLM_MODEL: 'llama3.2:latest',
    },
    discoverModelsPayload: { models: ['llama3.2:latest', 'qwen2.5:latest'] },
    exportOpenClawResponse: {
      ok: true,
      model_ref: 'ollama/llama3.2:latest',
      restarted: true,
    },
  });
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.removeItem('clawcrossStudioFirstVisitV2');
  });

  await page.goto('/studio');
  await page.locator('.hamburger-btn').click();
  await page.locator('#hamburger-panel button[onclick*="openSettings(); closeHamburgerMenu();"]').click();

  await expect(page.locator('#settings-modal')).toBeVisible();
  await expect(page.locator('#settings-llm-provider')).toHaveValue('ollama');
  await expect(page.locator('#settings-llm-key')).toHaveValue('');
  await page.locator('#settings-export-openclaw-btn').click();

  await expect.poll(() => calls.exportOpenClaw).toBe(1);
  expect(calls.lastExportPayload).toMatchObject({
    provider: 'ollama',
    api_key: '',
    base_url: 'http://127.0.0.1:11434',
    model: 'llama3.2:latest',
  });

  expect(pageErrors).toEqual([]);
});

test('studio ACP warmup surfaces backend errors inline', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
    acpxEnsure: [],
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await stubStudioNetwork(page, calls, {
    acpxStatusPayload: { available: true, tools: ['cursor'] },
    acpxHistoryPayload: {
      ok: false,
      error: 'No named session "main:cursor:main-session"',
    },
    acpxHistoryStatus: 502,
    acpxEnsurePayload: {
      ok: false,
      error: 'Failed to spawn agent command: cursor-agent acp',
      session_key: 'main:cursor:main-session',
    },
    acpxEnsureStatus: 502,
  });
  await installMockWebSocket(page);
  await page.addInitScript(() => {
    window.alert = (message) => {
      throw new Error(`unexpected alert: ${message}`);
    };
    window.confirm = () => true;
  });

  await page.goto('/studio');
  await page.getByRole('button', { name: 'Cursor' }).click();
  await expect(page.locator('#oc-acp-session-row')).toBeVisible();

  await page.locator('.oc-acp-session-ensure').click();

  await expect.poll(() => calls.acpxEnsure.length).toBe(1);
  expect(calls.acpxEnsure[0]).toMatchObject({ tool: 'cursor' });
  await expect(page.locator('#oc-acp-session-status')).toContainText(/预热失败|Warm up failed/);
  await expect(page.locator('#oc-acp-session-status')).toContainText('cursor-agent acp');
  expect(pageErrors).toEqual([]);
});

test('studio webot current runtime card stays synced over bridge websocket', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
    bridgeAttach: 0,
    bridgeDetach: 0,
    voiceUpdates: [],
    buddyActions: [],
    kairosUpdates: [],
    dreamRuns: 0,
    inboxDeliveries: 0,
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await installMockWebSocket(page);
  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.removeItem('clawcrossSessionRuntimePanelHeightV1');
  });

  await page.goto('/studio');
  await page.locator('.hamburger-btn').click();
  await page.locator('#hamburger-panel button[onclick*="toggleSessionSidebar(); closeHamburgerMenu();"]').click();

  await expect(page.locator('#webot-current-session')).toBeVisible();
  await expect(page.locator('#webot-current-session')).toContainText('Current Session');
  await expect(page.locator('#webot-current-session')).toContainText(/Execution swarm|Execution Swarm/);
  await expect(page.locator('#webot-current-session')).toContainText('Memory');
  await expect(page.locator('#webot-current-session')).toContainText('Buddy');
  await expect(page.locator('#webot-current-session')).toContainText('Waiting by the prompt');

  await page.locator('#webot-current-session button').filter({ hasText: 'Attach' }).click();
  await expect.poll(() => calls.bridgeAttach).toBe(1);
  await expect.poll(() => page.evaluate(() => window.__clawcrossSocketUrls.length)).toBe(1);
  await expect(page.locator('#webot-current-session')).toContainText('attach=ATTACH-42');

  const currentSessionId = await page.locator('#webot-current-session .webot-current-card-caption').evaluate((el) => {
    return String(el.textContent || '').split(' · ')[0].trim();
  });

  await page.evaluate(({ sessionId }) => {
    window.__emitClawcrossSocket({
      type: 'runtime_update',
      changed_session_id: sessionId,
      runtime: {
        status: 'success',
        session_id: sessionId,
        session_role: 'main',
        workspace: '/tmp/clawcross/main',
        mode: { mode: 'execute', reason: 'Bridge live update' },
        plan: {
          title: 'Main session plan',
          status: 'active',
          items: [{ step: 'Keep runtime panel synced', status: 'completed' }],
        },
        todos: { items: [{ title: 'Deliver inbox', status: 'completed' }] },
        verifications: [],
        approvals: [],
        inbox: [{ message_id: 'inbox-1', source_label: 'planner', body: 'Delivered from socket', status: 'delivered' }],
        artifacts: [],
        runs: [],
        active_run: null,
        relationships: { parent_session: '', children: [] },
        memory: {
          summary: '4 entries · kairos on · last dream just now',
          project_slug: 'clawcross-main',
          entry_count: 4,
          can_dream: false,
          kairos_enabled: true,
          relevant_entries: [{ name: 'deploy_notes' }],
        },
        bridge: {
          status: 'attached',
          attached: true,
          connection_count: 1,
          sessions: [{
            bridge_id: 'bridge-main-1',
            session_id: sessionId,
            role: 'viewer',
            attach_code: 'ATTACH-42',
            websocket_path: '/webot/ws/smoke-user/bridge-main-1',
            status: 'attached',
            connection_count: 1,
          }],
          primary: {
            bridge_id: 'bridge-main-1',
            session_id: sessionId,
            role: 'viewer',
            attach_code: 'ATTACH-42',
            websocket_path: '/webot/ws/smoke-user/bridge-main-1',
            status: 'attached',
            connection_count: 1,
          },
        },
        voice: {
          enabled: true,
          auto_read_aloud: false,
          recording_supported: true,
          tts_model: 'gpt-4o-mini-tts',
          tts_voice: 'alloy',
          stt_model: 'whisper-1',
          last_transcript: 'Bridge runtime synced',
          status: 'enabled',
        },
        buddy: {
          compact_face: '^_^',
          name: 'Mochi',
          species: 'capybara',
          rarity: 'rare',
          personality: 'Calm but opinionated',
          reaction: 'Bridge sync received',
          available_actions: ['pet', 'bridge'],
        },
      },
    });
  }, { sessionId: currentSessionId });

  await expect(page.locator('#webot-current-session')).toContainText('socket=live');
  await expect(page.locator('#webot-current-session')).toContainText('clients=1');
  await expect(page.locator('#webot-current-session')).toContainText('Bridge sync received');
  await expect(page.locator('#webot-current-session')).toContainText('Bridge runtime synced');
  await expect(page.locator('#webot-current-session')).toContainText('kairos on');

  await page.locator('#webot-current-session button').filter({ hasText: 'Pet' }).click();
  await expect.poll(() => calls.buddyActions.length).toBe(1);
  await expect(page.locator('#webot-current-session')).toContainText('Purring after a bridge sync');

  expect(pageErrors).toEqual([]);
});

test('studio webot runtime sidebar shows runtime state and resolves approvals', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.removeItem('clawcrossSessionRuntimePanelHeightV1');
  });

  await page.goto('/studio');
  await page.locator('.hamburger-btn').click();
  await page.locator('#hamburger-panel button[onclick*="toggleSessionSidebar(); closeHamburgerMenu();"]').click();

  await expect(page.locator('#session-sidebar')).toBeVisible();
  const runtimePanel = page.locator('#webot-subagent-panel');
  const divider = page.locator('#session-panel-divider');
  const runtimeHeightBefore = await runtimePanel.evaluate((el) => Math.round(el.getBoundingClientRect().height));
  const dividerBox = await divider.boundingBox();
  await page.mouse.move(dividerBox.x + dividerBox.width / 2, dividerBox.y + dividerBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(dividerBox.x + dividerBox.width / 2, dividerBox.y + dividerBox.height / 2 + 48, { steps: 8 });
  await page.mouse.up();
  const runtimeHeightAfter = await runtimePanel.evaluate((el) => Math.round(el.getBoundingClientRect().height));
  expect(runtimeHeightAfter).toBeLessThan(runtimeHeightBefore);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('clawcrossSessionRuntimePanelHeightV1'))).not.toBeNull();

  await expect(page.locator('#webot-subagent-list')).toContainText('Curie');
  await expect(page.locator('#webot-subagent-detail')).toContainText('Review gate');
  await expect(page.locator('#webot-subagent-detail')).toContainText('/tmp/clawcross/worktree/curie');
  await expect(page.locator('#webot-subagent-detail')).toContainText('Flask proxy chain');

  await page
    .locator('#webot-subagent-detail button')
    .filter({ hasText: /批准并记住|Approve \+ remember/ })
    .click();

  await expect.poll(() => calls.approvalActions.length).toBe(1);
  expect(calls.approvalActions[0]).toMatchObject({
    approval_id: 'approval-1',
    action: 'approve',
    remember: true,
    session_id: 'subagent__coder__curie',
  });

  await expect(page.locator('#webot-policy-status')).toContainText(/Approval 已处理|Approval resolved/);
  await expect(page.locator('#webot-subagent-detail')).toContainText(/approved|APPROVED/);

  expect(pageErrors).toEqual([]);
});

test('studio webot runtime surfaces recovery hints and applies workflow presets', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
    workflowApply: [],
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
    localStorage.removeItem('clawcrossSessionRuntimePanelHeightV1');
  });

  await page.goto('/studio');
  await page.locator('.hamburger-btn').click();
  await page.locator('#hamburger-panel button[onclick*="toggleSessionSidebar(); closeHamburgerMenu();"]').click();

  await expect(page.locator('#webot-subagent-detail')).toContainText('Workflow');
  await expect(page.locator('#webot-subagent-detail')).toContainText('Resolve the pending tool approval');

  await page
    .locator('#webot-current-session button')
    .filter({ hasText: 'Execution Swarm' })
    .click();

  await expect.poll(() => calls.workflowApply.length).toBe(1);
  expect(calls.workflowApply[0]).toMatchObject({ preset_id: 'execution_swarm' });
  expect(String(calls.workflowApply[0].session_id || '')).not.toBe('');
  await expect(page.locator('#webot-current-session')).toContainText('Execution Swarm');
  expect(pageErrors).toEqual([]);
});

test('studio builtin preset modal installs team presets', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
    teamPresetInstall: [],
    teamPresetList: 0,
  };
  const pageErrors = [];

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', async (dialog) => {
    pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    await dialog.dismiss();
  });

  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
  });

  await page.goto('/studio');
  await page.evaluate(() => {
    window.__openedBuiltinTeam = '';
    const originalOpenGroup = window.openGroup;
    window.openGroup = async (teamName) => {
      window.__openedBuiltinTeam = String(teamName || '');
      if (typeof originalOpenGroup === 'function') {
        return undefined;
      }
    };
  });

  await page.evaluate(() => {
    window.currentGroupId = 'Smoke Team';
  });
  await page.evaluate(async () => {
    const modal = document.getElementById('builtin-preset-modal');
    if (modal) modal.style.display = 'flex';
    if (typeof window.loadBuiltinTeamPresets === 'function') {
      await window.loadBuiltinTeamPresets();
    }
    return modal ? modal.style.display : '';
  });

  await expect.poll(() => calls.teamPresetList).toBe(1);
  await expect(page.locator('#builtin-preset-list')).toContainText('明朝内阁制');
  await expect(page.locator('#builtin-preset-list')).toContainText('翰林院小说创作局');

  await page.evaluate(async () => {
    const input = document.getElementById('builtin-preset-team-name');
    if (input) input.value = 'Smoke Preset Team';
    if (typeof window.installBuiltinTeamPreset === 'function') {
      await window.installBuiltinTeamPreset('ming-neige');
    }
  });

  await expect.poll(() => calls.teamPresetInstall.length).toBe(1);
  expect(calls.teamPresetInstall[0]).toMatchObject({
    preset_id: 'ming-neige',
    team: 'Smoke Preset Team',
  });
  await expect.poll(() => page.evaluate(() => window.__openedBuiltinTeam)).toBe('Smoke Preset Team');
  expect(pageErrors).toEqual([]);
});

test('studio oasis swarm uses pretext-backed multiline labels', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
  };
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await installMockWebSocket(page);
  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
  });

  await page.goto('/studio');

  const metrics = await page.evaluate(() => {
    const node = buildOasisSwarmNodeMetrics({
      id: 'objective-1',
      label: 'A multilingual objective label 春天到了 بدأت الرحلة with extra context for wrapping',
      type: 'objective',
      degree: 3,
      activity: { posts: 0, events: 0 },
    });
    renderOasisSwarmPanel({
      topic_id: 'topic-pretext',
      swarm: {
        status: 'ready',
        summary: 'Swarm layout preview',
        prediction: 'Testing label geometry.',
        mode: 'prediction',
        graph: {
          nodes: [
            { id: 'objective-1', label: 'A multilingual objective label 春天到了 بدأت الرحلة with extra context for wrapping', type: 'objective', degree: 3 },
            { id: 'agent-1', label: 'Planner', type: 'agent', degree: 2 },
          ],
          edges: [
            { id: 'edge-1', source: 'objective-1', target: 'agent-1', label: 'Long relationship label for pretext truncation check', weight: 0.8 },
          ],
        },
        scenarios: [],
        nudges: [],
        signals: [],
        graphrag: { provider: 'local', memory_count: 0, collections: [] },
      },
      participants: [{ name: 'Planner', posts: 2, events: 1 }],
    });
    return {
      ready: Boolean(window.ClawcrossTextLayout && typeof window.ClawcrossTextLayout.measureDisplay === 'function'),
      labelLineCount: node.labelLineCount,
      labelLines: node.labelLines,
      edgeLabels: Array.from(document.querySelectorAll('#oasis-swarm-canvas text')).map((el) => el.textContent || ''),
      tspanCount: document.querySelectorAll('#oasis-swarm-canvas tspan').length,
    };
  });

  expect(metrics.ready).toBeTruthy();
  expect(metrics.labelLineCount).toBeGreaterThan(1);
  expect(metrics.labelLines.length).toBeGreaterThan(1);
  expect(metrics.tspanCount).toBeGreaterThan(1);
  expect(metrics.edgeLabels.join(' ')).toContain('Long relationship');
  expect(pageErrors).toEqual([]);
});

test('oasis town runtime mounts, draws canvas, and accepts live updates', async ({ page }) => {
  const calls = {
    importOpenClaw: 0,
    exportOpenClaw: 0,
    tinyfishRun: 0,
    lastExportPayload: null,
    approvalActions: [],
  };
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await installMockWebSocket(page);
  await stubStudioNetwork(page, calls);
  await page.addInitScript(() => {
    window.alert = () => {};
    window.confirm = () => true;
  });

  await page.goto('/studio');
  await expect.poll(() => page.evaluate(() => Boolean(window.OasisTown))).toBe(true);

  const mounted = await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'oasis-town-runtime-smoke-host';
    host.style.width = '640px';
    host.style.height = '420px';
    host.style.position = 'fixed';
    host.style.left = '0';
    host.style.top = '0';
    host.style.zIndex = '9999';
    host.style.background = '#4a7c59';
    document.body.appendChild(host);

    const detail = {
      topic_id: 'topic-town-smoke',
      question: 'Should agents coordinate in a visible town?',
      user_id: 'smoke-user',
      status: 'running',
      current_round: 1,
      max_rounds: 3,
      discussion: true,
      conclusion: null,
      posts: [
        {
          id: 1,
          author: 'Planner',
          content: 'Map the work before implementation.',
          upvotes: 2,
          downvotes: 0,
          timestamp: 1770000000,
          elapsed: 1,
        },
        {
          id: 2,
          author: 'Verifier',
          content: 'Keep the browser smoke tied to canvas output.',
          upvotes: 3,
          downvotes: 0,
          timestamp: 1770000010,
          elapsed: 3,
        },
      ],
      timeline: [
        { elapsed: 0, event: 'topic_created', agent: 'System', detail: 'Town smoke started' },
        { elapsed: 2, event: 'agent_posted', agent: 'Planner', detail: 'Initial plan' },
      ],
    };
    window.OasisTown.mount(host, detail);
    return { hasRuntime: Boolean(window.OasisTown), hostChildren: host.children.length };
  });

  expect(mounted.hasRuntime).toBeTruthy();
  await expect(page.locator('#oasis-town-runtime-smoke-host canvas')).toBeVisible();
  await expect.poll(() =>
    page.locator('#oasis-town-runtime-smoke-host canvas').evaluate((canvas) => ({
      width: canvas.width,
      height: canvas.height,
    }))
  ).toMatchObject({ width: expect.any(Number), height: expect.any(Number) });

  const canvasState = await page.evaluate(async () => {
    const canvas = document.querySelector('#oasis-town-runtime-smoke-host canvas');
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const probe = document.createElement('canvas');
    probe.width = 64;
    probe.height = 64;
    const ctx = probe.getContext('2d');
    let variedPixels = 0;
    let dataUrlLength = 0;
    if (canvas && ctx) {
      ctx.drawImage(canvas, 0, 0, 64, 64);
      const data = ctx.getImageData(0, 0, 64, 64).data;
      const r0 = data[0];
      const g0 = data[1];
      const b0 = data[2];
      for (let i = 0; i < data.length; i += 4) {
        if (Math.abs(data[i] - r0) + Math.abs(data[i + 1] - g0) + Math.abs(data[i + 2] - b0) > 12) {
          variedPixels += 1;
        }
      }
      dataUrlLength = canvas.toDataURL('image/png').length;
    }
    window.OasisTown.update({
      topic_id: 'topic-town-smoke',
      question: 'Should agents coordinate in a visible town?',
      user_id: 'smoke-user',
      status: 'running',
      current_round: 2,
      max_rounds: 3,
      discussion: true,
      conclusion: null,
      posts: [
        {
          id: 1,
          author: 'Planner',
          content: 'Map the work before implementation.',
          upvotes: 2,
          downvotes: 0,
          timestamp: 1770000000,
          elapsed: 1,
        },
        {
          id: 2,
          author: 'Verifier',
          content: 'Keep the browser smoke tied to canvas output.',
          upvotes: 5,
          downvotes: 0,
          timestamp: 1770000010,
          elapsed: 3,
        },
        {
          id: 3,
          author: 'Implementer',
          content: 'Runtime accepted an update without remounting.',
          upvotes: 1,
          downvotes: 0,
          timestamp: 1770000020,
          elapsed: 5,
        },
      ],
      timeline: [
        { elapsed: 0, event: 'topic_created', agent: 'System', detail: 'Town smoke started' },
        { elapsed: 2, event: 'agent_posted', agent: 'Planner', detail: 'Initial plan' },
        { elapsed: 5, event: 'agent_posted', agent: 'Implementer', detail: 'Update applied' },
      ],
    });
    return {
      canvasCount: document.querySelectorAll('#oasis-town-runtime-smoke-host canvas').length,
      width: canvas ? canvas.width : 0,
      height: canvas ? canvas.height : 0,
      variedPixels,
      dataUrlLength,
    };
  });

  expect(canvasState.canvasCount).toBe(1);
  expect(canvasState.width).toBeGreaterThan(100);
  expect(canvasState.height).toBeGreaterThan(100);
  expect(canvasState.variedPixels).toBeGreaterThan(20);
  expect(canvasState.dataUrlLength).toBeGreaterThan(1000);
  expect(pageErrors).toEqual([]);
});
