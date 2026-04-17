// ── Orchestration State ──
const orch = {
    experts: [],
    nodes: [],
    edges: [],
    groups: [],
    selectedNodes: new Set(),
    nid: 1, eid: 1, gid: 1,
    dragging: null,
    connecting: null,
    selecting: null,
    panning: null,       // 画布拖拽平移状态
    spaceDown: false,    // 空格键按下状态
    contextMenu: null,
    sessionStatuses: {},
    // Team mode
    teamEnabled: false,
    teamName: '',
    // Zoom & pan state
    zoom: 1,
    panX: 0,
    panY: 0,
};

// ── Team mode helpers ──
async function orchLoadTeamList() {
    const sel = document.getElementById('orch-team-select');
    if (!sel) return;
    try {
        const resp = await fetch('/teams');
        const data = await resp.json();
        const teams = data.teams || [];
        const currentVal = sel.value;
        sel.innerHTML = '<option value="">(公共)</option>';
        teams.forEach(team => {
            const opt = document.createElement('option');
            opt.value = team;
            opt.textContent = team;
            sel.appendChild(opt);
        });
        // Add "New..." option
        const newOpt = document.createElement('option');
        newOpt.value = '__new__';
        newOpt.textContent = '➕ New...';
        sel.appendChild(newOpt);
        // Restore selection if still valid
        if (currentVal && (teams.includes(currentVal) || currentVal === '__new__')) {
            sel.value = currentVal;
        }
    } catch (e) {
        console.error('Failed to load team list:', e);
    }
}

function orchTeamSelectChanged() {
    const sel = document.getElementById('orch-team-select');
    const val = sel.value;
    if (val === '__new__') {
        // Prompt for new team name
        const newName = prompt(t('orch_prompt_new_team') || 'Enter new team name:');
        if (newName && newName.trim()) {
            const trimmedName = newName.trim();
            // Create the team
            orchCreateTeamByName(trimmedName);
        } else {
            // Reset to previous selection
            sel.value = orch.teamName || '';
        }
        return;
    }
    orch.teamName = val || '';
    orch.teamEnabled = !!orch.teamName;
    orchShowTeamButtons(!!orch.teamName);
    orchLoadExperts();
    orchLoadSessionAgents();
    orchLoadOpenClawSessions();
}

function _orchTeamQuery() {
    // Returns query string part for team, e.g. '?team=myteam' or ''
    return orch.teamName ? '?team=' + encodeURIComponent(orch.teamName) : '';
}

function orchCanDeleteExpert(exp) {
    return !!exp && (exp.deletable === true || exp.source === 'custom' || exp.source === 'team');
}

function orchCanDeleteOpenClawAgent(agentName) {
    return !!agentName && agentName.toLowerCase() !== 'main';
}

function orchGetTextLayout() {
    if (typeof window === 'undefined') return null;
    return window.ClawcrossTextLayout || null;
}

async function orchDeleteOpenClawAgent(agentName, options = {}) {
    if (!orchCanDeleteOpenClawAgent(agentName)) {
        orchToast(t('orch_oc_delete_main_blocked'));
        return false;
    }

    const displayName = options.displayName || agentName;
    if (!confirm(t('orch_oc_delete_confirm', { name: displayName }))) return false;

    try {
        const resp = await fetch('/proxy_openclaw_remove', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: agentName }),
        });
        const result = await resp.json().catch(() => ({}));
        if (!resp.ok || !result.ok) {
            throw new Error(result.error || 'Delete failed');
        }

        const teamName = options.teamName || '';
        if (teamName) {
            const unlinkResp = await fetch(`/teams/${encodeURIComponent(teamName)}/members/external`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ global_name: agentName }),
            });
            const unlinkResult = await unlinkResp.json().catch(() => ({}));
            if (!unlinkResp.ok) {
                throw new Error(unlinkResult.error || 'Failed to remove team mapping');
            }
            if (options.reloadMembers && typeof loadTeamMembers === 'function') {
                await loadTeamMembers();
            }
        }

        orchToast(t('orch_oc_delete_success', { name: displayName }));
        await orchLoadOpenClawSessions();
        return true;
    } catch (e) {
        orchToast(t('orch_oc_delete_failed', { name: displayName }) + ': ' + e.message);
        return false;
    }
}

// ── Team management functions ──
function orchShowTeamButtons(show) {
    const btns = ['orch-team-create-btn'];
    btns.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = show ? '' : 'none';
    });
}

async function orchCreateTeamByName(teamName) {
    if (!teamName || !teamName.trim()) {
        orchToast(t('orch_toast_team_name_required') || 'Please enter team name');
        return;
    }
    teamName = teamName.trim();
    try {
        const resp = await fetch('/teams', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ team: teamName })
        });
        const data = await resp.json();
        if (data.success) {
            orchToast(t('orch_toast_team_created') || 'Team created');
            orch.teamName = teamName;
            // Refresh team list and select the new team
            await orchLoadTeamList();
            const sel = document.getElementById('orch-team-select');
            sel.value = teamName;
            orchShowTeamButtons(true);
            orchLoadExperts();
            orchLoadSessionAgents();
            orchLoadOpenClawSessions();
        } else {
            orchToast(data.error || t('orch_toast_team_create_failed') || 'Failed to create team');
        }
    } catch (e) {
        orchToast(t('orch_toast_network_error') || 'Network error');
    }
}

async function orchDeleteTeam() {
    const teamName = orch.teamName.trim();
    if (!teamName) return;
    if (!confirm(t('orch_confirm_delete_team') || `Delete team "${teamName}" and all its agents?`)) return;
    try {
        const resp = await fetch('/teams/' + encodeURIComponent(teamName), { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            orchToast(t('orch_toast_team_deleted') || `Team deleted (${data.deleted_agents || 0} agents removed)`);
            const sel = document.getElementById('orch-team-select');
            sel.value = '';
            orch.teamName = '';
            orchShowTeamButtons(false);
            orchLoadExperts();
            orchLoadSessionAgents();
            orchLoadOpenClawSessions();
        } else {
            orchToast(data.error || t('orch_toast_team_delete_failed') || 'Failed to delete team');
        }
    } catch (e) {
        orchToast(t('orch_toast_network_error') || 'Network error');
    }
}

async function orchDownloadSnapshot() {
    const teamName = orch.teamName.trim();
    if (!teamName) {
        orchToast(t('orch_toast_team_name_required') || 'Please enter team name');
        return;
    }
    try {
        const resp = await fetch('/teams/snapshot/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ team: teamName })
        });
        if (!resp.ok) {
            const data = await resp.json();
            orchToast(data.error || t('orch_toast_snapshot_download_failed') || 'Failed to download snapshot');
            return;
        }
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `team_${teamName}_snapshot.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        orchToast(t('orch_toast_snapshot_downloaded') || 'Snapshot downloaded');
    } catch (e) {
        orchToast(t('orch_toast_network_error') || 'Network error');
    }
}

function orchUploadSnapshotClick() {
    document.getElementById('orch-team-snapshot-input').click();
}

async function orchHandleSnapshotUpload(event) {
    const teamName = orch.teamName.trim();
    if (!teamName) {
        orchToast(t('orch_toast_team_name_required') || 'Please enter team name');
        return;
    }
    const file = event.target.files[0];
    if (!file) return;
    if (!file.name.endsWith('.zip')) {
        orchToast(t('orch_toast_invalid_zip') || 'Please select a .zip file');
        event.target.value = '';
        return;
    }
    const formData = new FormData();
    formData.append('file', file);
    formData.append('team', teamName);
    try {
        const resp = await teamSnapshotUploadZipWithProgress(file, formData);
        const data = await resp.json();
        if (data.success) {
            orchToast(t('orch_toast_snapshot_uploaded') || 'Snapshot uploaded and agents restored');
            // Refresh team list if this is a new team
            if (typeof loadAgentTeams === 'function') {
                await loadAgentTeams();
            }
            orchLoadSessionAgents();
            orchLoadOpenClawSessions();
        } else {
            orchToast(data.error || t('orch_toast_snapshot_upload_failed') || 'Failed to upload snapshot');
        }
    } catch (e) {
        orchToast(t('orch_toast_network_error') || 'Network error');
    }
    event.target.value = '';
}

// ── Zoom / Pan helpers ──
function orchApplyTransform() {
    const inner = document.getElementById('orch-canvas-inner');
    if (inner) inner.style.transform = `translate(${orch.panX}px, ${orch.panY}px) scale(${orch.zoom})`;
    document.getElementById('orch-zoom-label').textContent = Math.round(orch.zoom * 100) + '%';
}
function orchZoom(delta) {
    orch.zoom = Math.min(3, Math.max(0.15, orch.zoom + delta));
    orchApplyTransform();
}
function orchPanBy(dx, dy) {
    orch.panX += dx; orch.panY += dy;
    orchApplyTransform();
}
function orchResetView() {
    orch.zoom = 1; orch.panX = 0; orch.panY = 0;
    orchApplyTransform();
}
/** Convert page-level clientX/Y to canvas-inner coordinates (accounting for zoom+pan) */
function orchClientToCanvas(clientX, clientY) {
    const area = document.getElementById('orch-canvas-area');
    const rect = area.getBoundingClientRect();
    return {
        x: (clientX - rect.left - orch.panX) / orch.zoom,
        y: (clientY - rect.top  - orch.panY) / orch.zoom,
    };
}

/** 判断当前是否为移动端视图 */
function orchIsMobile() { return window.innerWidth <= 768; }

/** 移动端点击专家卡片 → 添加节点 + 收起专家池 + 高亮动画 */
function orchMobileTapAdd(data) {
    const node = orchAddNodeCenter(data);
    // 收起专家池侧边栏
    if (typeof orchCloseMobilePanels === 'function') orchCloseMobilePanels();
    // 高亮动画：新节点闪烁
    const el = document.getElementById('onode-' + node.id);
    if (el) {
        el.classList.add('orch-node-flash');
        setTimeout(() => el.classList.remove('orch-node-flash'), 900);
    }
    orchToast('✅ ' + (data.emoji||'') + ' ' + (data.name||'Node') + ' ' + t('orch_toast_added_mobile'));
}

/** 给专家卡片绑定移动端 click 和桌面端 dblclick */
function orchBindCardEvents(card, data) {
    // 移动端禁用拖拽，改为点击添加
    if (orchIsMobile()) {
        card.draggable = false;
    } else {
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('application/json', JSON.stringify(data));
            e.dataTransfer.effectAllowed = 'copy';
        });
    }
    card.addEventListener('dblclick', () => orchAddNodeCenter(data));
    card.addEventListener('click', e => {
        if (!orchIsMobile()) return;
        if (e.target.closest('.orch-expert-del-btn')) return;
        orchMobileTapAdd(data);
    });
}

/** Conditional card: special drag behavior — drop on blank = new selector node, drop on existing node = toggle selector */
function orchBindCondCardEvents(card, data) {
    if (orchIsMobile()) {
        card.draggable = false;
    } else {
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('application/json', JSON.stringify({...data, _condDrop: true}));
            e.dataTransfer.effectAllowed = 'copy';
        });
    }
    // Double-click: add as standalone selector node at center
    card.addEventListener('dblclick', () => orchAddNodeCenter(data));
    card.addEventListener('click', e => {
        if (!orchIsMobile()) return;
        orchMobileTapAdd(data);
    });
}

function orchBuildControlNodeData(kind) {
    if (kind === 'manual') {
        return {type:'manual', name:t('orch_manual_inject'), tag:'manual', emoji:'📝', temperature:0};
    }
    if (kind === 'start') {
        return {type:'manual', name:t('orch_start_node'), tag:'manual', emoji:'🚀', temperature:0, author:t('orch_start_author'), content:t('orch_start_default_content')};
    }
    if (kind === 'end') {
        return {type:'manual', name:t('orch_end_node'), tag:'manual', emoji:'🏁', temperature:0, author:t('orch_end_author'), content:t('orch_end_default_content')};
    }
    if (kind === 'script') {
        return {
            type:'script',
            name:t('orch_script_node') || 'Script Node',
            tag:'script',
            emoji:'🧪',
            temperature:0,
            script_command:'',
            script_unix_command:'',
            script_windows_command:'',
            script_timeout:'',
            script_cwd:'',
        };
    }
    if (kind === 'human') {
        return {
            type:'human',
            name:t('orch_human_node') || 'Human Node',
            tag:'human',
            emoji:'🙋',
            temperature:0,
            human_author:t('orch_default_author') || '主持人',
            human_prompt:'',
            human_reply_to:'',
        };
    }
    if (kind === 'conditional') {
        return {type:'conditional', name:t('orch_cond_node'), tag:'conditional', emoji:'🎯', temperature:0, isSelector:true};
    }
    return null;
}

function orchBindControlCards() {
    const controlCards = [
        ['orch-manual-card', orchBuildControlNodeData('manual'), orchBindCardEvents],
        ['orch-start-card', orchBuildControlNodeData('start'), orchBindCardEvents],
        ['orch-end-card', orchBuildControlNodeData('end'), orchBindCardEvents],
        ['orch-script-card', orchBuildControlNodeData('script'), orchBindCardEvents],
        ['orch-human-card', orchBuildControlNodeData('human'), orchBindCardEvents],
        ['orch-cond-card', orchBuildControlNodeData('conditional'), orchBindCondCardEvents],
    ];
    controlCards.forEach(([id, data, binder]) => {
        const card = document.getElementById(id);
        if (card && data) binder(card, data);
    });
}

/** Check if a canvas coordinate hits an existing node element, return node or null */
function orchFindNodeAtPoint(clientX, clientY) {
    const els = document.querySelectorAll('.orch-node');
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom) {
            const nodeId = el.id.replace('onode-', '');
            return orch.nodes.find(n => n.id === nodeId) || null;
        }
    }
    return null;
}

function orchCanBeSelector(node) {
    return !!node && !['manual', 'script', 'human'].includes(node.type);
}

function orchNodePreviewText(node) {
    if (!node) return '';
    if (node.type === 'script') {
        return node.script_unix_command || node.script_windows_command || node.script_command || '';
    }
    if (node.type === 'human') {
        return node.human_prompt || '';
    }
    return node.content || '';
}

function orchRerenderNode(nodeId) {
    const node = orch.nodes.find(n => n.id === nodeId);
    if (!node) return;
    document.getElementById('onode-' + nodeId)?.remove();
    orchRenderNode(node);
    orchRenderEdges();
}

/** Toggle a node's selector status on (with visual feedback). */
function orchSetNodeSelector(node) {
    node.isSelector = true;
    const el = document.getElementById('onode-' + node.id);
    if (el) {
        el.classList.add('selector-type');
        if (!el.querySelector('.orch-selector-badge')) {
            const badge = document.createElement('div');
            badge.className = 'orch-selector-badge';
            badge.textContent = '🎯 SELECTOR';
            el.appendChild(badge);
        }
    }
    orchRenderEdges();
    orchUpdateYaml();
    orchToast('🎯 ' + node.name + ' → SELECTOR');
}

function orchInit() {
    orchLoadTeamList();
    orchLoadExperts();
    orchLoadSessionAgents();
    orchLoadOpenClawSessions();
    orchSetupCanvas();
    orchSetupSettings();
    orchSetupFileDrop();
    orchBindControlCards();
}

// ── Load experts (preset + user/team managed) ──
async function orchLoadExperts() {
    try {
        const teamQ = orch.teamName ? '?team=' + encodeURIComponent(orch.teamName) : '';
        const r = await fetch('/proxy_visual/experts' + teamQ);
        orch.experts = await r.json();
    } catch(e) { console.error('Load experts failed:', e); }
    orchRenderExpertSidebar();
}

function orchRenderExpertSidebar() {
    const custList = document.getElementById('orch-expert-list-custom');
    const agencyCats = document.getElementById('orch-agency-categories');
    custList.innerHTML = '';
    if (agencyCats) agencyCats.innerHTML = '';

    // 分类标签映射（公共专家也作为一个分类）
    const catLabels = {
        '_public': {icon: '🌟', zh: '公共专家', en: 'Public Experts'},
        'design': {icon: '🎨', zh: '设计', en: 'Design'},
        'engineering': {icon: '⚙️', zh: '工程', en: 'Engineering'},
        'marketing': {icon: '📢', zh: '营销', en: 'Marketing'},
        'product': {icon: '📦', zh: '产品', en: 'Product'},
        'project-management': {icon: '📋', zh: '项目管理', en: 'Project Mgmt'},
        'spatial-computing': {icon: '🥽', zh: '空间计算', en: 'Spatial Computing'},
        'specialized': {icon: '🔬', zh: '专项', en: 'Specialized'},
        'support': {icon: '🛡️', zh: '支持', en: 'Support'},
        'testing': {icon: '🧪', zh: '测试', en: 'Testing'},
    };
    const isZh = (typeof currentLang !== 'undefined' && currentLang === 'zh-CN');

    // 所有预设专家都按 category 分组（公共专家用 _public 分类）
    const expertsByCategory = {};

    orch.experts.forEach(exp => {
        const isUserManaged = orchCanDeleteExpert(exp);
        if (isUserManaged) {
            const card = _orchCreateExpertCard(exp, true);
            custList.appendChild(card);
        } else {
            const cat = (exp.source === 'agency' && exp.category) ? exp.category : '_public';
            if (!expertsByCategory[cat]) expertsByCategory[cat] = [];
            expertsByCategory[cat].push(exp);
        }
    });

    // 渲染所有分类折叠（公共专家 _public 排在最前面且默认展开）
    if (agencyCats) {
        // 排序: _public 排首位，其余按字母排序
        const sortedCats = Object.keys(expertsByCategory).sort((a, b) => {
            if (a === '_public') return -1;
            if (b === '_public') return 1;
            return a.localeCompare(b);
        });
        sortedCats.forEach(cat => {
            const items = expertsByCategory[cat];
            const info = catLabels[cat] || {icon: '📂', zh: cat, en: cat};
            const catName = isZh ? info.zh : info.en;
            const isPublic = (cat === '_public');
            const wrapper = document.createElement('div');
            wrapper.className = 'orch-agency-cat-group';

            const header = document.createElement('div');
            header.className = 'orch-agency-cat-header' + (isPublic ? ' expanded' : '');
            header.innerHTML = `<span class="cat-icon">${info.icon}</span><span class="cat-name">${catName}</span><span class="cat-count">${items.length}</span><span class="cat-arrow">▶</span>`;

            const list = document.createElement('div');
            list.className = 'orch-agency-cat-list' + (isPublic ? ' expanded' : '');

            items.forEach(exp => {
                const card = _orchCreateExpertCard(exp, false);
                list.appendChild(card);
            });

            header.addEventListener('click', () => {
                const isExpanded = header.classList.toggle('expanded');
                list.classList.toggle('expanded', isExpanded);
            });

            wrapper.appendChild(header);
            wrapper.appendChild(list);
            agencyCats.appendChild(wrapper);
        });
    }

    if (!custList.children.length) {
        custList.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#d1d5db;text-align:center;">' + t('orch_no_custom') + '</div>';
    }
}

// 创建专家卡片的辅助函数
function _orchCreateExpertCard(exp, isCustom) {
    const card = document.createElement('div');
    card.className = 'orch-expert-card';
    card.draggable = true;
    // 根据当前语言选择显示名称
    const _isZh = (typeof currentLang !== 'undefined' && currentLang === 'zh-CN');
    const displayName = _isZh ? (exp.name_zh || exp.name) : (exp.name_en || exp.name);
    card.innerHTML = `<span class="orch-emoji">${exp.emoji}</span><div style="min-width:0;flex:1;"><div class="orch-name" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</div><div class="orch-tag">${escapeHtml(exp.tag)}</div></div><span class="orch-temp">${exp.temperature||''}</span>${isCustom ? '<button class="orch-card-delete-btn orch-expert-del-btn" title="' + t('orch_ctx_delete') + '">×</button>' : ''}`;
    orchBindCardEvents(card, {type:'expert', ...exp});
    if (isCustom) {
        card.querySelector('.orch-expert-del-btn').addEventListener('click', async (ev) => {
            ev.stopPropagation();
            if (!confirm(t('orch_confirm_del_expert', {name: exp.name}))) return;
            try {
                const _delTeamQ = orch.teamName ? '?team=' + encodeURIComponent(orch.teamName) : '';
                const resp = await fetch('/proxy_visual/experts/custom/' + encodeURIComponent(exp.tag) + _delTeamQ, { method: 'DELETE' });
                const res = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    throw new Error(res.detail || res.error || 'Delete failed');
                }
                orchToast(t('orch_toast_expert_deleted', {name: exp.name}));
                orchLoadExperts();
            } catch(e) { orchToast(t('orch_toast_expert_del_fail') + ': ' + e.message); }
        });
    }
    return card;
}

// Helper: load internal agent meta as a map { session_id: meta }
async function _orchLoadAgentMetaMap() {
    try {
        const resp = await fetch('/internal_agents' + _orchTeamQuery());
        const data = await resp.json();
        const map = {};
        if (data.agents) {
            for (const a of data.agents) map[a.session] = a.meta || {};
        }
        return map;
    } catch (e) { return {}; }
}

// Resolve display title: prefer agent meta name, fallback to original title
function _orchResolveTitle(originalTitle, sessionId, agentMap) {
    const meta = agentMap[sessionId];
    if (meta && meta.name) return meta.name;
    return originalTitle;
}

// ── Add Internal Agent Modal ──
function orchShowAddInternalAgentModal() {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-add-ia-overlay';
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:380px;max-width:460px;">
            <h3>${t('orch_add_internal_agent_title')}</h3>
            <div style="display:flex;flex-direction:column;gap:8px;margin:10px 0;">
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_ia_name')}
                    <input id="orch-ia-name" type="text" placeholder="my_agent" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-top:2px;">
                </label>
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_ia_tag')}
                    <input id="orch-ia-tag" type="text" list="orch-ia-tag-list" placeholder="${t('orch_ia_tag_placeholder')}" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-top:2px;">
                    <datalist id="orch-ia-tag-list">
                        ${[...new Set(orch.experts.map(e => e.tag).filter(Boolean))].map(tag => `<option value="${escapeHtml(tag)}"></option>`).join('')}
                    </datalist>
                </label>
                <div id="orch-ia-drop-zone" style="border:2px dashed #d1d5db;border-radius:8px;padding:12px;text-align:center;font-size:11px;color:#9ca3af;cursor:default;transition:all .15s;">
                    📦 ${t('orch_ia_tag_placeholder')}
                </div>
            </div>
            <div class="orch-modal-btns">
                <button id="orch-ia-cancel" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_modal_cancel')}</button>
                <button id="orch-ia-save" style="padding:6px 14px;border-radius:6px;border:none;background:#6366f1;color:white;cursor:pointer;font-size:12px;">${t('orch_modal_save')}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#orch-ia-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    // Drop zone: accept expert drag to set tag
    const dropZone = overlay.querySelector('#orch-ia-drop-zone');
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; dropZone.style.borderColor = '#6366f1'; dropZone.style.background = '#eef2ff'; });
    dropZone.addEventListener('dragleave', () => { dropZone.style.borderColor = '#d1d5db'; dropZone.style.background = ''; });
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.style.borderColor = '#d1d5db'; dropZone.style.background = '';
        try {
            const data = JSON.parse(e.dataTransfer.getData('application/json'));
            if (data.tag && !['manual', 'conditional', 'script', 'human'].includes(data.tag)) {
                document.getElementById('orch-ia-tag').value = data.tag;
                dropZone.innerHTML = '✅ Tag: <b>' + escapeHtml(data.tag) + '</b> (' + escapeHtml(data.name || '') + ')';
                dropZone.style.borderColor = '#6366f1'; dropZone.style.color = '#374151';
            }
        } catch(err) {}
    });
    // Save: create new session + internal agent entry
    overlay.querySelector('#orch-ia-save').addEventListener('click', async () => {
        const name = document.getElementById('orch-ia-name').value.trim();
        const tag = document.getElementById('orch-ia-tag').value.trim();
        if (!name) { orchToast('⚠️ Name is required'); return; }
        // Generate a new session id
        const newSid = Date.now().toString(36) + Math.random().toString(36).substr(2, 4);
        try {
            await fetch('/internal_agents' + _orchTeamQuery(), {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ session: newSid, meta: { name, tag: tag || '' } })
            });
            orchToast('✅ ' + t('orch_ia_created') + ': ' + name);
            overlay.remove();
            orchLoadSessionAgents();
        } catch(e) { orchToast('❌ ' + t('orch_toast_net_error')); }
    });
}

// ── Load Internal Agents ──
async function orchLoadSessionAgents() {
    const list = document.getElementById('orch-expert-list-sessions');
    if (!list) return;
    list.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#9ca3af;text-align:center;">⏳ ' + t('loading') + '</div>';
    try {
        // Load sessions and agent meta in parallel
        const [resp, agentMap] = await Promise.all([fetch('/proxy_sessions'), _orchLoadAgentMetaMap()]);
        const data = await resp.json();
        list.innerHTML = '';

        // Build merged session list: start from proxy_sessions, then add any JSON-only entries
        const allSessions = (data.sessions || []).slice();
        const seenIds = new Set(allSessions.map(s => s.session_id));
        // Add sessions that exist in internal agent JSON but not in proxy_sessions
        for (const [sid, meta] of Object.entries(agentMap)) {
            if (!seenIds.has(sid)) {
                allSessions.push({ session_id: sid, title: meta.name || 'Untitled', message_count: 0 });
                seenIds.add(sid);
            }
        }

        // Only show sessions that have a name in the agent JSON
        const sessions = allSessions.filter(s => {
            const meta = agentMap[s.session_id];
            return meta && meta.name;
        });

        if (sessions.length === 0) {
            list.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#d1d5db;text-align:center;">No named agents yet</div>';
            return;
        }
        // Sort by session_id descending (newest first)
        sessions.sort((a, b) => b.session_id.localeCompare(a.session_id));
        for (const s of sessions) {
            const card = document.createElement('div');
            card.className = 'orch-expert-card';
            card.draggable = true;
            const title = _orchResolveTitle(s.title || 'Untitled', s.session_id, agentMap);
            const shortId = s.session_id.slice(-8);
            const msgCount = s.message_count || 0;
            // Carry agent meta tag (from internal agent JSON) if available
            const meta = agentMap[s.session_id] || {};
            const agentTag = meta.tag || '';
            // Add delete button
            card.innerHTML = `<span class="orch-emoji">🤖</span><div style="min-width:0;flex:1;"><div class="orch-name" title="${escapeHtml(title)}">${escapeHtml(title)}</div><div class="orch-tag" style="color:#6366f1;font-family:monospace;">${agentTag ? '🏷️' + escapeHtml(agentTag) + ' · ' : ''}#${escapeHtml(shortId)}</div></div><button class="orch-card-delete-btn" title="Delete agent" onclick="event.stopPropagation(); orchDeleteInternalAgent('${s.session_id}')">×</button>`;
            const nodeData = {
                type: 'session_agent',
                name: title,
                tag: agentTag || '',
                emoji: '🤖',
                temperature: 0.5,
                session_id: s.session_id,
                agent_name: meta.name || title,
            };
            orchBindCardEvents(card, nodeData);
            list.appendChild(card);
        }
    } catch(e) {
        console.error('Load internal agents failed:', e);
        list.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#dc2626;text-align:center;">❌ ' + t('error') + '</div>';
    }
}

// Delete internal agent from orchestration panel
async function orchDeleteInternalAgent(sessionId) {
    if (!confirm('Delete this internal agent?')) return;
    try {
        // Delete from internal_agents JSON
        const url = (orch.teamEnabled && orch.teamName) 
            ? `/internal_agents/${encodeURIComponent(sessionId)}?team=${encodeURIComponent(orch.teamName)}` 
            : `/internal_agents/${encodeURIComponent(sessionId)}`;
        const resp = await fetch(url, { method: 'DELETE' });
        if (resp.ok) {
            // Reload the list
            await orchLoadSessionAgents();
        } else {
            const data = await resp.json();
            alert('Delete failed: ' + (data.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Delete failed: ' + e.message);
    }
}

/** Non–OpenClaw rows grouped by platform/tool (same idea as mobile contacts). */
function _orchGroupPublicExternalAgents(rows) {
    const acpByTool = {};
    (rows || []).forEach((row) => {
        if (!row || typeof row !== 'object') return;
        const platformRaw = String(row.platform || row.tag || '').trim().toLowerCase();
        if (platformRaw === 'openclaw') return;
        const gn = String(row.global_name || '').trim();
        if (!gn) return;
        const key = platformRaw || 'unknown';
        if (!acpByTool[key]) acpByTool[key] = [];
        acpByTool[key].push(row);
    });
    return acpByTool;
}

function _orchMakeExtCategory(title, count) {
    const det = document.createElement('details');
    det.className = 'orch-ext-cat';
    det.open = true;
    const sum = document.createElement('summary');
    sum.className = 'orch-ext-cat-summary';
    const t1 = document.createElement('span');
    t1.textContent = title;
    const t2 = document.createElement('span');
    t2.className = 'orch-ext-cat-count';
    t2.textContent = String(count);
    sum.appendChild(t1);
    sum.appendChild(t2);
    const body = document.createElement('div');
    body.className = 'orch-ext-cat-body';
    det.appendChild(sum);
    det.appendChild(body);
    return { det, body };
}

function _orchAppendPublicExternalCard(parent, row, yamlName) {
    const card = document.createElement('div');
    card.className = 'orch-expert-card';
    card.draggable = true;
    // name = display name (friendly yamlName, or row.name for team-only agents)
    const name = yamlName || row.name || 'unknown';
    // ext_id = YAML third segment; for ACP agents (global_name like "agent:cc7:clawcrosschat"),
    // extract the middle segment; for team agents use name directly
    let extId = yamlName || row.name || '';
    if (!yamlName && row.global_name) {
        // ACP public agent: global_name = "agent:{session}:{suffix}", extract middle segment
        const parts = row.global_name.split(':');
        extId = parts.length >= 2 ? parts[1] : row.global_name;
    }
    const tag = row.tag || '';
    const meta = row.meta || {};
    const mdl = (meta.model && meta.model !== 'unknown') ? meta.model : '';
    const rawUrl = meta.api_url || '';
    const urlShort = rawUrl ? (rawUrl.length > 36 ? rawUrl.slice(0, 36) + '…' : rawUrl) : '';
    card.innerHTML = `<span class="orch-emoji">🔌</span><div style="min-width:0;flex:1;"><div class="orch-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>${tag ? '<div class="orch-tag" style="color:#6b7280;">🏷️' + escapeHtml(tag) + '</div>' : ''}${mdl ? '<div class="orch-tag" style="color:#059669;font-family:monospace;">' + escapeHtml(mdl) + '</div>' : ''}${urlShort ? '<div class="orch-tag" style="color:#6b7280;font-size:9px;">' + escapeHtml(urlShort) + '</div>' : ''}</div>`;
    const nodeData = {
        type: 'external',
        name: name,
        tag: tag || 'custom',
        platform: row.platform || tag || 'custom',
        emoji: '🔌',
        temperature: 0.7,
        api_url: rawUrl,
        api_key: meta.api_key ? '****' : '',
        model: meta.model || '',
        ext_id: extId,  // YAML third segment = parsed session name (not full ACP key)
    };
    if (meta.headers && typeof meta.headers === 'object' && Object.keys(meta.headers).length) {
        nodeData.headers = meta.headers;
    }
    orchBindCardEvents(card, nodeData);
    parent.appendChild(card);
}

// ── External agent pool (OpenClaw + public HTTP/ACP), layered like mobile contacts ──
async function orchLoadOpenClawSessions() {
    const list = document.getElementById('orch-expert-list-openclaw');
    if (!list) return;
    list.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#9ca3af;text-align:center;">⏳ ' + t('loading') + '</div>';
    try {
        const [ocResp, acpxResp] = await Promise.all([
            fetch('/proxy_openclaw_sessions'),
            fetch('/proxy_acpx_sessions_all'),
        ]);
        const data = await ocResp.json().catch(() => ({}));
        let acpxData = { sessions: [] };
        try {
            if (acpxResp.ok) acpxData = await acpxResp.json();
        } catch (_) { /* ignore */ }

        // When team mode is active, also load external_agents.json to get
        // the authoritative name↔global_name mapping.
        // global_name is the real OpenClaw agent name; name is the short display name.
        // They do NOT necessarily have a prefix relationship.
        let extAgentMap = {};   // global_name (lowercase) → { name, global_name }
        if (orch.teamEnabled && orch.teamName) {
            try {
                const extResp = await fetch('/team_openclaw_snapshot?team=' + encodeURIComponent(orch.teamName));
                const extData = await extResp.json();
                if (extData.ok && extData.agents) {
                    for (const ea of extData.agents) {
                        if (ea.global_name) {
                            extAgentMap[ea.global_name.toLowerCase()] = ea;
                        }
                    }
                }
            } catch(e) { /* ignore, will fall back to prefix stripping */ }
        }

        // ACP agents source: team mode from /teams/${team}/members, non-team from /public_external_agents
        let acpRows = [];
        if (orch.teamEnabled && orch.teamName) {
            try {
                const teamResp = await fetch('/teams/' + encodeURIComponent(orch.teamName) + '/members');
                const teamData = await teamResp.json().catch(() => ({}));
                const members = teamData.members || [];
                // Filter external members and transform to row format
                acpRows = members
                    .filter(m => (m.type || m.member_type || '') === 'ext')
                    .map(m => ({
                        name: m.name || m.global_name || '',
                        global_name: m.global_name || m.name || '',
                        tag: m.tag || '',
                        platform: m.platform || m.tag || '',
                        meta: m.meta || {}
                    }));
            } catch(e) { /* team fetch failed: clear acpRows so only team members are shown */ acpRows = []; }
        } else {
            // In public mode (no team), load from user's external_agents.json via /public_external_agents
            try {
                const pubResp = await fetch('/public_external_agents');
                const pubData = await pubResp.json().catch(() => ({}));
                const agents = pubData.agents || [];
                acpRows = agents.map(a => ({
                    name: a.name || a.global_name || '',
                    global_name: a.global_name || a.name || '',
                    tag: a.tag || '',
                    platform: a.platform || a.tag || '',
                    meta: a.meta || {}
                }));
            } catch(e) { /* use empty list if fetch fails */ }
        }
        const acpByTool = _orchGroupPublicExternalAgents(acpRows);
        let acpTotal = 0;
        Object.values(acpByTool).forEach((arr) => {
            if (Array.isArray(arr)) acpTotal += arr.length;
        });

        let agents = data.available ? (data.agents || []) : [];
        if (orch.teamEnabled && orch.teamName) {
            const prefix = orch.teamName.toLowerCase() + '_';
            agents = agents.filter((a) => {
                const aName = (a.name || '').toLowerCase();
                return extAgentMap[aName] || aName.startsWith(prefix);
            });
        }

        if (agents.length === 0 && acpTotal === 0) {
            list.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#d1d5db;text-align:center;">' + escapeHtml(t('orch_ext_pool_empty')) + '</div>';
            return;
        }

        const openclawUrl = data.openclaw_api_url || '';
        list.innerHTML = '';

        const appendOpenClawAgentCard = (parent, a) => {
            const card = document.createElement('div');
            card.className = 'orch-expert-card';
            card.draggable = true;
            const agentName = a.name || 'unknown';
            const mdl = (a.model && a.model !== 'unknown' && a.model !== 'auto') ? a.model : '';
            const agentWs = a.workspace || '';

            // Build subtitle with tools/skills summary
            const toolProfile = (a.tools && a.tools.profile) ? a.tools.profile : '';
            const skillCount = a.skills_all ? '∞' : (a.skills ? a.skills.length : 0);
            let metaLine = '';
            if (toolProfile) metaLine += '🔧' + toolProfile;
            if (skillCount) metaLine += (metaLine ? ' · ' : '') + '🧩' + skillCount;

            // Resolve display name (yamlName) from external_agents.json.
            // The JSON "name" field is the authoritative short display name used in YAML;
            // "global_name" is the real OpenClaw CLI agent name.
            // They do NOT necessarily have a teamName_ prefix relationship.
            let yamlName = agentName;
            const extEntry = extAgentMap[agentName.toLowerCase()];
            if (orch.teamEnabled && orch.teamName) {
                if (extEntry && extEntry.name) {
                    // Prefer the name from external_agents.json
                    yamlName = extEntry.name;
                } else {
                    // Fallback: strip team prefix (for agents not yet in JSON)
                    const prefix = orch.teamName + '_';
                    if (agentName.startsWith(prefix)) {
                        yamlName = agentName.slice(prefix.length);
                    }
                }
            }
            const title = yamlName + (a.is_default ? ' ⭐' : '');

            card.innerHTML = `<span class="orch-emoji">🦞</span><div style="min-width:0;flex:1;"><div class="orch-name" title="${escapeHtml(agentName)}">${escapeHtml(title)}</div>${mdl ? '<div class="orch-tag" style="color:#10b981;font-family:monospace;">' + escapeHtml(mdl) + '</div>' : ''}${metaLine ? '<div class="orch-tag" style="color:#6b7280;font-size:9px;">' + escapeHtml(metaLine) + '</div>' : ''}</div><div style="display:flex;flex-direction:column;gap:2px;flex-shrink:0;">${(orch.teamEnabled && orch.teamName) ? '<button class="orch-oc-snap-btn" data-agent="' + escapeHtml(agentName) + '" data-short="' + escapeHtml(yamlName) + '" title="Export to team snapshot" style="background:none;border:none;cursor:pointer;font-size:12px;padding:1px 3px;opacity:0.5;line-height:1;" onmouseenter="this.style.opacity=1" onmouseleave="this.style.opacity=0.5">📤</button>' : ''}${agentWs ? '<button class="orch-oc-edit-btn" data-ws="' + escapeHtml(agentWs) + '" data-agent="' + escapeHtml(agentName) + '" title="' + t('orch_oc_edit_files') + '" style="background:none;border:none;cursor:pointer;font-size:12px;padding:1px 3px;opacity:0.5;line-height:1;" onmouseenter="this.style.opacity=1" onmouseleave="this.style.opacity=0.5">📝</button>' : ''}<button class="orch-oc-cfg-btn" data-agent="${escapeHtml(agentName)}" title="${t('orch_oc_config')}" style="background:none;border:none;cursor:pointer;font-size:12px;padding:1px 3px;opacity:0.5;line-height:1;" onmouseenter="this.style.opacity=1" onmouseleave="this.style.opacity=0.5">⚙️</button>${orchCanDeleteOpenClawAgent(agentName) ? '<button class="orch-oc-del-btn" data-agent="' + escapeHtml(agentName) + '" title="' + t('orch_oc_delete') + '" style="background:none;border:none;cursor:pointer;font-size:12px;padding:1px 3px;color:#dc2626;opacity:0.65;line-height:1;" onmouseenter="this.style.opacity=1" onmouseleave="this.style.opacity=0.65">🗑️</button>' : ''}</div>`;
            // model format: agent:<name> (CLI uses --agent <name>, no session-id)
            const modelStr = 'agent:' + yamlName;
            const nodeData = {
                type: 'external', name: yamlName, tag: 'openclaw', platform: 'openclaw', emoji: '🦞', temperature: 0.7,
                api_url: openclawUrl, api_key: '****',
                model: modelStr,
                ext_id: yamlName,  // use agent name as ext_id to distinguish different agents
            };
            orchBindCardEvents(card, nodeData);
            // Bind edit button (stop propagation so it doesn't trigger card add)
            const editBtn = card.querySelector('.orch-oc-edit-btn');
            if (editBtn) {
                editBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    orchShowWorkspaceEditor(editBtn.dataset.agent, editBtn.dataset.ws);
                });
                editBtn.addEventListener('dblclick', (e) => e.stopPropagation());
            }
            // Bind config button
            const cfgBtn = card.querySelector('.orch-oc-cfg-btn');
            if (cfgBtn) {
                cfgBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    orchShowAgentConfigModal(cfgBtn.dataset.agent);
                });
                cfgBtn.addEventListener('dblclick', (e) => e.stopPropagation());
            }
            const delBtn = card.querySelector('.orch-oc-del-btn');
            if (delBtn) {
                delBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    await orchDeleteOpenClawAgent(delBtn.dataset.agent, {
                        teamName: orch.teamEnabled ? orch.teamName : '',
                        displayName: delBtn.dataset.agent,
                        reloadMembers: !!(orch.teamEnabled && orch.teamName),
                    });
                });
                delBtn.addEventListener('dblclick', (e) => e.stopPropagation());
            }
            // Bind snapshot export button (team mode only)
            const snapBtn = card.querySelector('.orch-oc-snap-btn');
            if (snapBtn) {
                snapBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const sBtn = e.currentTarget;
                    const fullName = sBtn.dataset.agent;
                    const shortName = sBtn.dataset.short;
                    sBtn.textContent = '⏳';
                    try {
                        const r = await fetch('/team_openclaw_snapshot/export', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ team: orch.teamName, agent_name: fullName, short_name: shortName }),
                        });
                        const res = await r.json();
                        if (res.ok) {
                            orchToast('📤 ' + res.message);
                        } else {
                            orchToast('❌ ' + (res.error || 'Export failed'));
                        }
                    } catch(err) { orchToast('❌ Network error'); }
                    sBtn.textContent = '📤';
                });
                snapBtn.addEventListener('dblclick', (e) => e.stopPropagation());
            }
            parent.appendChild(card);
        };

        const ocCat = _orchMakeExtCategory(t('orch_ext_cat_openclaw'), agents.length);
        list.appendChild(ocCat.det);
        if (agents.length === 0) {
            const hint = document.createElement('div');
            hint.style.cssText = 'padding:8px 10px;font-size:10px;color:#9ca3af;text-align:center;';
            hint.textContent = t('orch_ext_openclaw_empty');
            ocCat.body.appendChild(hint);
        } else {
            for (const a of agents) appendOpenClawAgentCard(ocCat.body, a);
        }

        const sortedTools = Object.keys(acpByTool).sort((a, b) => {
            if (a === '_') return 1;
            if (b === '_') return -1;
            return a.localeCompare(b);
        });
        for (const tool of sortedTools) {
            const items = acpByTool[tool] || [];
            if (!items.length) continue;
            const label = tool === '_' ? t('group_ext_tag_none') : tool;
            const cat = _orchMakeExtCategory(label, items.length);
            list.appendChild(cat.det);
            for (const row of items) {
                // In team mode, resolve yamlName from team external_agents.json by global_name
                let yamlName;
                if (orch.teamEnabled && orch.teamName) {
                    yamlName = (extAgentMap[(row.global_name || '').toLowerCase()] || {}).name || row.name;
                } else {
                    yamlName = row.name;
                }
                _orchAppendPublicExternalCard(cat.body, row, yamlName);
            }
        }

        // Team mode: add Export All / Restore All buttons
        if (orch.teamEnabled && orch.teamName && data.available) {
            const btnBar = document.createElement('div');
            btnBar.style.cssText = 'display:flex;gap:4px;padding:6px 8px;border-top:1px solid #e5e7eb;';
            btnBar.innerHTML = `
                <button id="orch-oc-export-all" style="flex:1;padding:4px 8px;border-radius:4px;border:1px solid #059669;background:#ecfdf5;color:#059669;cursor:pointer;font-size:10px;font-weight:600;" title="Export all team agents config to team folder">📤 Export</button>
                <button id="orch-oc-restore-all" style="flex:1;padding:4px 8px;border-radius:4px;border:1px solid #7c3aed;background:#f5f3ff;color:#7c3aed;cursor:pointer;font-size:10px;font-weight:600;" title="Restore all agents from team snapshot">📥 Restore</button>
            `;
            list.appendChild(btnBar);

            // Export All: save all team agents' full config to team folder
            btnBar.querySelector('#orch-oc-export-all').addEventListener('click', async (e) => {
                e.stopPropagation();
                const btn = e.currentTarget;
                btn.disabled = true; btn.textContent = '⏳ Exporting...';
                try {
                    const r = await fetch('/team_openclaw_snapshot/export_all', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ team: orch.teamName }),
                    });
                    const res = await r.json();
                    if (res.ok) {
                        orchToast('📤 ' + res.message);
                    } else {
                        orchToast('❌ ' + (res.error || 'Export failed'));
                    }
                } catch(err) { orchToast('❌ Network error'); }
                btn.disabled = false; btn.textContent = '📤 Export';
            });

            // Restore All: restore agents from team snapshot
            btnBar.querySelector('#orch-oc-restore-all').addEventListener('click', async (e) => {
                e.stopPropagation();
                const btn = e.currentTarget;
                if (!confirm('Restore all agents from team snapshot? This will create/update OpenClaw agents with prefix "' + orch.teamName + '_".')) return;
                btn.disabled = true; btn.textContent = '⏳ Restoring...';
                try {
                    const r = await fetch('/team_openclaw_snapshot/restore_all', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ team: orch.teamName }),
                    });
                    const res = await r.json();
                    if (res.ok) {
                        orchToast('📥 ' + res.message);
                        if (res.errors && res.errors.length > 0) {
                            orchToast('⚠️ Errors: ' + res.errors.join('; '));
                        }
                        // Reload the list to show newly created agents
                        setTimeout(() => orchLoadOpenClawSessions(), 1000);
                    } else {
                        orchToast('❌ ' + (res.error || 'Restore failed'));
                    }
                } catch(err) { orchToast('❌ Network error'); }
                btn.disabled = false; btn.textContent = '📥 Restore';
            });
        }
    } catch(e) {
        list.innerHTML = '<div style="padding:6px 10px;font-size:10px;color:#dc2626;text-align:center;">❌ ' + t('error') + '</div>';
    }
}

// ── Add custom expert modal ──
function orchShowAddExpertModal() {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-add-expert-overlay';
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:380px;max-width:460px;">
            <h3>${t('orch_add_expert_title')}</h3>
            <div style="display:flex;flex-direction:column;gap:8px;margin:10px 0;">
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_label_name')} <input id="orch-ce-name" type="text" placeholder="${t('orch_ph_name')}" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-top:2px;"></label>
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_label_tag')}
                    <input id="orch-ce-tag" type="text" list="orch-ce-tag-list" placeholder="${t('orch_ph_tag')}" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-top:2px;">
                    <datalist id="orch-ce-tag-list">
                        ${[...new Set(orch.experts.map(e => e.tag).filter(Boolean))].map(tag => `<option value="${escapeHtml(tag)}"></option>`).join('')}
                    </datalist>
                </label>
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_label_temp')} <input id="orch-ce-temp" type="number" value="0.7" min="0" max="2" step="0.1" style="width:80px;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-top:2px;"></label>
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_label_persona')}
                    <textarea id="orch-ce-persona" rows="4" placeholder="${t('orch_ph_persona')}" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-top:2px;resize:vertical;"></textarea>
                </label>
            </div>
            <div class="orch-modal-btns">
                <button id="orch-ce-cancel" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_modal_cancel')}</button>
                <button id="orch-ce-save" style="padding:6px 14px;border-radius:6px;border:none;background:#2563eb;color:white;cursor:pointer;font-size:12px;">${t('orch_modal_save')}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#orch-ce-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.querySelector('#orch-ce-save').addEventListener('click', async () => {
        const name = document.getElementById('orch-ce-name').value.trim();
        const tag = document.getElementById('orch-ce-tag').value.trim();
        const temperature = parseFloat(document.getElementById('orch-ce-temp').value) || 0.7;
        const persona = document.getElementById('orch-ce-persona').value.trim();
        if (!name || !tag || !persona) { orchToast(t('orch_toast_fill_info')); return; }
        try {
            const teamName = orch.teamName || '';
            const r = await fetch('/proxy_visual/experts/custom' + (teamName ? '?team=' + encodeURIComponent(teamName) : ''), {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({name, tag, temperature, persona}),
            });
            const res = await r.json();
            if (r.ok) {
                orchToast(t('orch_toast_custom_added', {name}));
                overlay.remove();
                orchLoadExperts();
            } else {
                orchToast(t('orch_toast_load_fail') + ': ' + (res.detail || res.error || ''));
            }
        } catch(e) { orchToast(t('orch_toast_net_error')); }
    });
}

// ── OpenClaw Workspace File Editor ──
// Now integrated into the unified config modal — see orchShowAgentConfigModal

// ── OpenClaw Quick Config (entry from chat header) ──
async function orchOpenClawQuickConfig() {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-oc-quick-cfg-overlay';
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:320px;max-width:460px;width:85vw;max-height:70vh;display:flex;flex-direction:column;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
                <h3 style="margin:0;font-size:14px;">🦞 ${t('orch_oc_quick_title')}</h3>
                <button id="orch-qcfg-close" style="background:none;border:none;font-size:18px;cursor:pointer;padding:2px 6px;color:#6b7280;">✕</button>
            </div>
            <div id="orch-qcfg-status" style="font-size:10px;color:#9ca3af;margin-bottom:8px;">⏳ ${t('loading')}</div>
            <div id="orch-qcfg-list" style="flex:1;overflow-y:auto;min-height:0;display:flex;flex-direction:column;gap:6px;"></div>
            <div style="padding-top:8px;border-top:1px solid #e5e7eb;margin-top:8px;">
                <button id="orch-qcfg-add" style="width:100%;padding:8px 12px;border:2px dashed #d1d5db;border-radius:8px;background:#fafafa;cursor:pointer;font-size:12px;color:#2563eb;font-weight:600;transition:all .15s;display:flex;align-items:center;justify-content:center;gap:6px;" onmouseenter="this.style.borderColor='#93c5fd';this.style.background='#eff6ff'" onmouseleave="this.style.borderColor='#d1d5db';this.style.background='#fafafa'">
                    ➕ ${t('orch_oc_quick_add')}
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#orch-qcfg-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.querySelector('#orch-qcfg-add').addEventListener('click', () => {
        overlay.remove();
        orchShowAddOpenClawModal();
    });

    const statusEl = overlay.querySelector('#orch-qcfg-status');
    const listEl = overlay.querySelector('#orch-qcfg-list');

    try {
        const resp = await fetch('/proxy_openclaw_sessions');
        const data = await resp.json();
        if (!data.available) {
            statusEl.textContent = '🚫 ' + t('orch_oc_quick_no_agents');
            statusEl.style.color = '#ef4444';
            return;
        }
        if (!data.agents || data.agents.length === 0) {
            statusEl.textContent = t('orch_oc_quick_empty');
            statusEl.style.color = '#9ca3af';
            return;
        }
        statusEl.textContent = t('orch_oc_quick_select');
        statusEl.style.color = '#6b7280';

        for (const a of data.agents) {
            const name = a.name || 'unknown';
            const profile = (a.tools && a.tools.profile) ? a.tools.profile : '-';
            const skillCount = a.skills_all ? '∞' : (a.skills ? a.skills.length : 0);
            const row = document.createElement('div');
            row.className = 'orch-oc-quick-row';
            row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid #e5e7eb;border-radius:8px;cursor:pointer;transition:all .15s;background:#fff;';
            row.addEventListener('mouseenter', () => { row.style.background = '#eff6ff'; row.style.borderColor = '#93c5fd'; });
            row.addEventListener('mouseleave', () => { row.style.background = '#fff'; row.style.borderColor = '#e5e7eb'; });
            row.innerHTML = `
                <span style="font-size:20px;">🦞</span>
                <div style="flex:1;min-width:0;">
                    <div style="font-size:12px;font-weight:600;color:#1f2937;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(name)}${a.is_default ? ' <span style="color:#f59e0b;">⭐</span>' : ''}</div>
                    <div style="font-size:10px;color:#6b7280;">🔧${escapeHtml(profile)} · 🧩${skillCount}</div>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex-shrink:0;">
                    ${orchCanDeleteOpenClawAgent(name) ? '<button class="orch-oc-quick-del" data-agent="' + escapeHtml(name) + '" title="' + t('orch_oc_delete') + '" style="background:none;border:none;cursor:pointer;font-size:12px;padding:1px 3px;color:#dc2626;opacity:0.65;line-height:1;" onmouseenter="this.style.opacity=1" onmouseleave="this.style.opacity=0.65">🗑️</button>' : ''}
                    <span style="font-size:14px;color:#9ca3af;">→</span>
                </div>
            `;
            const delBtn = row.querySelector('.orch-oc-quick-del');
            if (delBtn) {
                delBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    const deleted = await orchDeleteOpenClawAgent(delBtn.dataset.agent, { displayName: delBtn.dataset.agent });
                    if (!deleted) return;
                    row.remove();
                    if (!listEl.querySelector('.orch-oc-quick-row')) {
                        statusEl.textContent = t('orch_oc_quick_empty');
                        statusEl.style.color = '#9ca3af';
                    }
                });
            }
            row.addEventListener('click', () => {
                overlay.remove();
                orchShowAgentConfigModal(name);
            });
            listEl.appendChild(row);
        }
    } catch(e) {
        statusEl.textContent = '❌ ' + t('orch_toast_net_error');
        statusEl.style.color = '#ef4444';
    }
}

// ── Unified OpenClaw Agent Config Modal (Tabs: Core Files | Skills & Tools | Channels) ──
async function orchShowAgentConfigModal(agentName, initialTab) {
    const canDelete = orchCanDeleteOpenClawAgent(agentName);
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-agent-config-overlay';
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:420px;max-width:750px;width:92vw;max-height:88vh;display:flex;flex-direction:column;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <h3 style="margin:0;font-size:14px;">🦞 ${escapeHtml(agentName)}</h3>
                <div style="display:flex;align-items:center;gap:6px;">
                    ${canDelete ? '<button id="orch-ucfg-delete" style="padding:4px 10px;border-radius:6px;border:1px solid #fecaca;background:#fff1f2;color:#dc2626;cursor:pointer;font-size:11px;font-weight:600;">🗑️ ' + t('orch_oc_delete') + '</button>' : ''}
                    <button id="orch-ucfg-close" style="background:none;border:none;font-size:18px;cursor:pointer;padding:2px 6px;color:#6b7280;">✕</button>
                </div>
            </div>
            <div id="orch-ucfg-tabs" style="display:flex;gap:0;margin-bottom:10px;border-bottom:2px solid #e5e7eb;">
                <button class="orch-ucfg-tab" data-tab="files" style="padding:6px 14px;font-size:11px;font-weight:600;border:none;cursor:pointer;background:none;border-bottom:2px solid transparent;margin-bottom:-2px;color:#6b7280;transition:all .15s;">📝 ${t('orch_oc_tab_files')}</button>
                <button class="orch-ucfg-tab" data-tab="config" style="padding:6px 14px;font-size:11px;font-weight:600;border:none;cursor:pointer;background:none;border-bottom:2px solid transparent;margin-bottom:-2px;color:#6b7280;transition:all .15s;">⚙️ ${t('orch_oc_tab_config')}</button>
                <button class="orch-ucfg-tab" data-tab="channels" style="padding:6px 14px;font-size:11px;font-weight:600;border:none;cursor:pointer;background:none;border-bottom:2px solid transparent;margin-bottom:-2px;color:#6b7280;transition:all .15s;">📡 ${t('orch_oc_tab_channels')}</button>
            </div>
            <div id="orch-ucfg-content" style="flex:1;overflow-y:auto;min-height:0;display:flex;flex-direction:column;">
                <div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ${t('loading')}</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Close handler: sync team openclaw snapshot if on team page
    function closeConfigModal() {
        overlay.remove();
        // If on team page (currentGroupId exists), sync all openclaw agents
        if (typeof currentGroupId !== 'undefined' && currentGroupId) {
            fetch('/team_openclaw_snapshot/sync_all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ team: currentGroupId }),
            }).catch(e => console.warn('sync_all on close:', e));
        }
    }

    overlay.querySelector('#orch-ucfg-close').addEventListener('click', closeConfigModal);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeConfigModal(); });
    if (canDelete) {
        overlay.querySelector('#orch-ucfg-delete').addEventListener('click', async () => {
            const teamName = (orch.teamEnabled && orch.teamName)
                ? orch.teamName
                : ((typeof currentGroupId !== 'undefined' && currentGroupId) ? currentGroupId : '');
            const deleted = await orchDeleteOpenClawAgent(agentName, {
                teamName,
                displayName: agentName,
                reloadMembers: !!teamName,
            });
            if (deleted) closeConfigModal();
        });
    }

    // Tab switching
    let activeTab = initialTab || 'config';
    const tabs = overlay.querySelectorAll('.orch-ucfg-tab');
    const contentEl = overlay.querySelector('#orch-ucfg-content');

    function activateTab(tab) {
        activeTab = tab;
        tabs.forEach(t => {
            const isActive = t.dataset.tab === tab;
            t.style.borderBottomColor = isActive ? '#2563eb' : 'transparent';
            t.style.color = isActive ? '#2563eb' : '#6b7280';
        });
        contentEl.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ' + t('loading') + '</div>';
        if (tab === 'files') loadFilesTab(agentName, contentEl, overlay);
        else if (tab === 'config') loadConfigTab(agentName, contentEl, overlay);
        else if (tab === 'channels') loadChannelsTab(agentName, contentEl, overlay);
    }

    tabs.forEach(tb => tb.addEventListener('click', () => activateTab(tb.dataset.tab)));
    activateTab(activeTab);
}

// Helper: orchShowWorkspaceEditor now opens unified modal on files tab
function orchShowWorkspaceEditor(agentName, workspace) {
    orchShowAgentConfigModal(agentName, 'files');
}

// ── Tab: Core Files ──
async function loadFilesTab(agentName, contentEl, overlay) {
    // First get workspace path from agent detail
    let workspace = '';
    try {
        const dr = await fetch('/proxy_openclaw_agent_detail?name=' + encodeURIComponent(agentName));
        const dd = await dr.json();
        if (dd.ok && dd.agent) workspace = dd.agent.workspace || '';
    } catch(e) {}

    if (!workspace) {
        contentEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ No workspace found</div>';
        return;
    }

    contentEl.innerHTML = `
        <div style="font-size:10px;color:#9ca3af;margin-bottom:8px;font-family:monospace;word-break:break-all;">${escapeHtml(workspace)}</div>
        <div id="orch-ws-file-list" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;color:#9ca3af;">⏳ ${t('loading')}</span>
        </div>
        <div id="orch-ws-editor-area" style="flex:1;min-height:0;display:flex;flex-direction:column;">
            <div style="text-align:center;padding:30px;">
                <div style="color:#d1d5db;font-size:12px;margin-bottom:16px;">${t('orch_oc_select_file')}</div>
                <button id="orch-ws-import-expert-global" style="padding:6px 16px;border-radius:6px;border:1px solid #8b5cf6;background:#f5f3ff;color:#7c3aed;cursor:pointer;font-size:12px;font-weight:500;" title="${t('orch_oc_import_expert_tip')}">
                    📥 ${t('orch_oc_import_expert_to_identity')}
                </button>
            </div>
        </div>
    `;

    try {
        const r = await fetch('/proxy_openclaw_workspace_files?workspace=' + encodeURIComponent(workspace));
        const res = await r.json();
        const listEl = contentEl.querySelector('#orch-ws-file-list');
        if (!res.ok || !res.files) {
            listEl.innerHTML = '<span style="color:#ef4444;font-size:10px;">❌ ' + (res.error || 'Error') + '</span>';
            return;
        }
        listEl.innerHTML = '';
        for (const f of res.files) {
            const btn = document.createElement('button');
            btn.className = 'orch-ws-file-tab';
            btn.dataset.filename = f.name;
            btn.style.cssText = 'padding:3px 8px;border-radius:4px;border:1px solid #d1d5db;background:white;cursor:pointer;font-size:10px;font-family:monospace;color:#374151;white-space:nowrap;';
            const sizeStr = f.exists ? (f.size >= 1024 ? (f.size / 1024).toFixed(1) + ' KB' : f.size + ' B') : t('orch_oc_file_missing');
            btn.textContent = f.name + (f.exists ? '' : ' ⚠️');
            btn.title = f.name + ' — ' + sizeStr;
            if (!f.exists) btn.style.color = '#d1d5db';
            btn.addEventListener('click', () => orchWsOpenFile(agentName, workspace, f.name, contentEl));
            listEl.appendChild(btn);
        }
        // Bind global import expert button (shown when no file is selected)
        const globalImportBtn = contentEl.querySelector('#orch-ws-import-expert-global');
        if (globalImportBtn) {
            globalImportBtn.addEventListener('click', () => {
                orchShowImportExpertModal(null, null, { agentName, workspace, containerEl: contentEl, targetFile: 'IDENTITY.md' });
            });
        }
    } catch(e) {
        contentEl.querySelector('#orch-ws-file-list').innerHTML =
            '<span style="color:#ef4444;font-size:10px;">❌ ' + t('orch_toast_net_error') + '</span>';
    }
}

async function orchWsOpenFile(agentName, workspace, filename, containerEl) {
    const editorArea = containerEl.querySelector('#orch-ws-editor-area');
    containerEl.querySelectorAll('.orch-ws-file-tab').forEach(b => {
        b.style.background = b.dataset.filename === filename ? '#dbeafe' : 'white';
        b.style.borderColor = b.dataset.filename === filename ? '#2563eb' : '#d1d5db';
    });
    editorArea.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ' + t('loading') + '</div>';
    try {
        const r = await fetch('/proxy_openclaw_workspace_file?workspace=' + encodeURIComponent(workspace) + '&filename=' + encodeURIComponent(filename));
        const res = await r.json();
        const content = res.content || '';
        const isIdentityFile = (filename === 'IDENTITY.md' || filename === 'SOUL.md');
        editorArea.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
                <span style="font-size:11px;font-weight:600;color:#374151;font-family:monospace;">${escapeHtml(filename)}</span>
                <div style="display:flex;gap:4px;align-items:center;">
                    <span id="orch-ws-status" style="font-size:10px;color:#9ca3af;"></span>
                    ${isIdentityFile ? '<button id="orch-ws-import-expert" style="padding:3px 10px;border-radius:4px;border:1px solid #8b5cf6;background:#f5f3ff;color:#7c3aed;cursor:pointer;font-size:11px;" title="' + t('orch_oc_import_expert_tip') + '">📥 ' + t('orch_oc_import_expert') + '</button>' : ''}
                    <button id="orch-ws-save" style="padding:3px 10px;border-radius:4px;border:none;background:#2563eb;color:white;cursor:pointer;font-size:11px;">${t('orch_oc_save')}</button>
                </div>
            </div>
            <textarea id="orch-ws-textarea" spellcheck="false"
                style="flex:1;width:100%;min-height:250px;max-height:55vh;border:1px solid #d1d5db;border-radius:6px;padding:8px;font-size:11px;font-family:monospace;line-height:1.5;resize:vertical;color:#1f2937;background:#fafafa;"
            >${escapeHtml(content)}</textarea>
        `;
        const textarea = editorArea.querySelector('#orch-ws-textarea');
        const statusEl = editorArea.querySelector('#orch-ws-status');
        const saveBtn = editorArea.querySelector('#orch-ws-save');

        if (!res.exists) statusEl.textContent = '🆕 ' + t('orch_oc_new_file');
        textarea.addEventListener('input', () => { statusEl.textContent = '● ' + t('orch_oc_unsaved'); statusEl.style.color = '#f59e0b'; });
        textarea.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveBtn.click(); }
        });
        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true; saveBtn.textContent = '⏳';
            try {
                const sr = await fetch('/proxy_openclaw_workspace_file', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ workspace, filename, content: textarea.value }),
                });
                const sres = await sr.json();
                if (sr.ok && sres.ok) {
                    statusEl.textContent = '✅ ' + t('orch_oc_saved'); statusEl.style.color = '#10b981';
                    orchToast('✅ ' + filename + ' ' + t('orch_oc_saved'));
                    const tab = containerEl.querySelector(`.orch-ws-file-tab[data-filename="${filename}"]`);
                    if (tab) { tab.style.color = '#374151'; tab.textContent = filename; }
                } else { statusEl.textContent = '❌ ' + (sres.error || 'Error'); statusEl.style.color = '#ef4444'; }
            } catch(e) { statusEl.textContent = '❌ ' + t('orch_toast_net_error'); statusEl.style.color = '#ef4444'; }
            saveBtn.disabled = false; saveBtn.textContent = t('orch_oc_save');
        });

        // Import expert persona button (only for IDENTITY.md / SOUL.md)
        const importBtn = editorArea.querySelector('#orch-ws-import-expert');
        if (importBtn) {
            importBtn.addEventListener('click', () => orchShowImportExpertModal(textarea, statusEl));
        }
    } catch(e) {
        editorArea.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + t('orch_toast_net_error') + '</div>';
    }
}

// ── Import Expert Persona into IDENTITY/SOUL file ──
async function orchShowImportExpertModal(textarea, statusEl, fileCtx) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-import-expert-overlay';
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:380px;max-width:550px;width:88vw;max-height:80vh;display:flex;flex-direction:column;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                <h3 style="margin:0;font-size:14px;">📥 ${t('orch_oc_import_expert_title')}</h3>
                <button id="orch-ie-close" style="background:none;border:none;font-size:18px;cursor:pointer;padding:2px 6px;color:#6b7280;">✕</button>
            </div>
            <div style="font-size:11px;color:#6b7280;margin-bottom:10px;">${t('orch_oc_import_expert_desc')}</div>
            <div style="display:flex;gap:6px;margin-bottom:10px;">
                <select id="orch-ie-mode" style="padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:11px;">
                    <option value="replace">${t('orch_oc_import_replace')}</option>
                    <option value="append">${t('orch_oc_import_append')}</option>
                </select>
                <input id="orch-ie-search" type="text" placeholder="${t('orch_oc_import_search_ph')}" style="flex:1;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:11px;">
            </div>
            <div id="orch-ie-list" style="flex:1;overflow-y:auto;min-height:0;display:flex;flex-direction:column;gap:4px;">
                <div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ${t('loading')}</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#orch-ie-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    const listEl = overlay.querySelector('#orch-ie-list');
    const searchInput = overlay.querySelector('#orch-ie-search');
    let allExperts = [];

    try {
        const r = await fetch('/proxy_visual/experts');
        allExperts = await r.json();
    } catch(e) {
        listEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + t('orch_toast_net_error') + '</div>';
        return;
    }

    function renderExpertList(filter) {
        listEl.innerHTML = '';
        const keyword = (filter || '').toLowerCase();
        const filtered = keyword ? allExperts.filter(ex =>
            (ex.name||'').toLowerCase().includes(keyword) ||
            (ex.tag||'').toLowerCase().includes(keyword) ||
            (ex.persona||'').toLowerCase().includes(keyword) ||
            (ex.category||'').toLowerCase().includes(keyword)
        ) : allExperts;

        if (filtered.length === 0) {
            listEl.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">' + t('orch_oc_import_no_result') + '</div>';
            return;
        }

        // Group by source
        const groups = { public: [], agency: [], custom: [] };
        filtered.forEach(ex => {
            const src = ex.source || 'public';
            if (groups[src]) groups[src].push(ex);
            else groups.public.push(ex);
        });

        const groupLabels = {
            public: { icon: '🌟', label: t('orch_oc_import_public') },
            agency: { icon: '🏢', label: t('orch_oc_import_agency') },
            custom: { icon: '🛠️', label: t('orch_oc_import_custom') },
        };

        for (const [src, items] of Object.entries(groups)) {
            if (items.length === 0) continue;
            const info = groupLabels[src] || { icon: '📂', label: src };
            const header = document.createElement('div');
            header.style.cssText = 'font-size:10px;font-weight:600;color:#6b7280;padding:4px 0;margin-top:4px;';
            header.textContent = info.icon + ' ' + info.label + ' (' + items.length + ')';
            listEl.appendChild(header);

            for (const ex of items) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:8px;cursor:pointer;transition:all .15s;background:#fff;';
                row.addEventListener('mouseenter', () => { row.style.background = '#f5f3ff'; row.style.borderColor = '#c4b5fd'; });
                row.addEventListener('mouseleave', () => { row.style.background = '#fff'; row.style.borderColor = '#e5e7eb'; });

                const personaPreview = (ex.persona || '').length > 80 ? (ex.persona.slice(0, 80) + '…') : (ex.persona || '-');
                row.innerHTML = `
                    <span style="font-size:18px;flex-shrink:0;">${ex.emoji || '⭐'}</span>
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:12px;font-weight:600;color:#1f2937;">${escapeHtml(ex.name)}</div>
                        <div style="font-size:10px;color:#6b7280;margin-top:1px;">${escapeHtml(ex.tag)}${ex.category ? ' · ' + escapeHtml(ex.category) : ''}</div>
                        <div style="font-size:10px;color:#9ca3af;margin-top:3px;line-height:1.4;white-space:pre-wrap;word-break:break-all;">${escapeHtml(personaPreview)}</div>
                    </div>
                    <span style="font-size:14px;color:#7c3aed;flex-shrink:0;margin-top:2px;">→</span>
                `;
                row.addEventListener('click', async () => {
                    const mode = overlay.querySelector('#orch-ie-mode').value;
                    const persona = ex.persona || '';
                    // Build identity content with expert name and persona
                    const identityContent = '# ' + (ex.name || ex.tag) + '\n\n' + persona;

                    if (textarea) {
                        // Direct mode: textarea is already open
                        if (mode === 'replace') {
                            textarea.value = identityContent;
                        } else {
                            textarea.value = textarea.value + (textarea.value ? '\n\n---\n\n' : '') + identityContent;
                        }
                        if (statusEl) { statusEl.textContent = '● ' + t('orch_oc_unsaved'); statusEl.style.color = '#f59e0b'; }
                    } else if (fileCtx) {
                        // Global mode: no file open yet, open IDENTITY.md then write
                        await orchWsOpenFile(fileCtx.agentName, fileCtx.workspace, fileCtx.targetFile, fileCtx.containerEl);
                        const ta = fileCtx.containerEl.querySelector('#orch-ws-textarea');
                        const st = fileCtx.containerEl.querySelector('#orch-ws-status');
                        if (ta) {
                            if (mode === 'replace') {
                                ta.value = identityContent;
                            } else {
                                ta.value = ta.value + (ta.value ? '\n\n---\n\n' : '') + identityContent;
                            }
                            if (st) { st.textContent = '● ' + t('orch_oc_unsaved'); st.style.color = '#f59e0b'; }
                        }
                    }
                    orchToast('📥 ' + t('orch_oc_import_done', { name: ex.name }));
                    overlay.remove();
                });
                listEl.appendChild(row);
            }
        }
    }

    renderExpertList('');
    searchInput.addEventListener('input', () => renderExpertList(searchInput.value));
}

// ── Expert Picker (for creation dialog, returns expert via callback) ──
async function orchShowImportExpertPicker(onSelect) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-expert-picker-overlay';
    overlay.style.zIndex = '10001'; // above creation modal
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:360px;max-width:520px;width:85vw;max-height:75vh;display:flex;flex-direction:column;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                <h3 style="margin:0;font-size:14px;">📥 ${t('orch_oc_create_pick_expert')}</h3>
                <button id="orch-ep-close" style="background:none;border:none;font-size:18px;cursor:pointer;padding:2px 6px;color:#6b7280;">✕</button>
            </div>
            <input id="orch-ep-search" type="text" placeholder="${t('orch_oc_import_search_ph')}" style="padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:11px;margin-bottom:8px;">
            <div id="orch-ep-list" style="flex:1;overflow-y:auto;min-height:0;display:flex;flex-direction:column;gap:4px;">
                <div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ${t('loading')}</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#orch-ep-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    const listEl = overlay.querySelector('#orch-ep-list');
    const searchInput = overlay.querySelector('#orch-ep-search');
    let allExperts = [];

    try {
        const r = await fetch('/proxy_visual/experts');
        allExperts = await r.json();
    } catch(e) {
        listEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + t('orch_toast_net_error') + '</div>';
        return;
    }

    function renderList(filter) {
        listEl.innerHTML = '';
        const keyword = (filter || '').toLowerCase();
        const filtered = keyword ? allExperts.filter(ex =>
            (ex.name||'').toLowerCase().includes(keyword) ||
            (ex.tag||'').toLowerCase().includes(keyword) ||
            (ex.persona||'').toLowerCase().includes(keyword) ||
            (ex.category||'').toLowerCase().includes(keyword)
        ) : allExperts;

        if (filtered.length === 0) {
            listEl.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">' + t('orch_oc_import_no_result') + '</div>';
            return;
        }

        const groups = { public: [], agency: [], custom: [] };
        filtered.forEach(ex => {
            const src = ex.source || 'public';
            if (groups[src]) groups[src].push(ex); else groups.public.push(ex);
        });
        const groupLabels = {
            public: { icon: '🌟', label: t('orch_oc_import_public') },
            agency: { icon: '🏢', label: t('orch_oc_import_agency') },
            custom: { icon: '🛠️', label: t('orch_oc_import_custom') },
        };

        for (const [src, items] of Object.entries(groups)) {
            if (items.length === 0) continue;
            const info = groupLabels[src] || { icon: '📂', label: src };
            const header = document.createElement('div');
            header.style.cssText = 'font-size:10px;font-weight:600;color:#6b7280;padding:4px 0;margin-top:4px;';
            header.textContent = info.icon + ' ' + info.label + ' (' + items.length + ')';
            listEl.appendChild(header);

            for (const ex of items) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:8px;cursor:pointer;transition:all .15s;background:#fff;';
                row.addEventListener('mouseenter', () => { row.style.background = '#f5f3ff'; row.style.borderColor = '#c4b5fd'; });
                row.addEventListener('mouseleave', () => { row.style.background = '#fff'; row.style.borderColor = '#e5e7eb'; });
                const personaPreview = (ex.persona || '').length > 80 ? (ex.persona.slice(0, 80) + '…') : (ex.persona || '-');
                row.innerHTML = `
                    <span style="font-size:18px;flex-shrink:0;">${ex.emoji || '⭐'}</span>
                    <div style="flex:1;min-width:0;">
                        <div style="font-size:12px;font-weight:600;color:#1f2937;">${escapeHtml(ex.name)}</div>
                        <div style="font-size:10px;color:#6b7280;margin-top:1px;">${escapeHtml(ex.tag)}${ex.category ? ' · ' + escapeHtml(ex.category) : ''}</div>
                        <div style="font-size:10px;color:#9ca3af;margin-top:3px;line-height:1.4;white-space:pre-wrap;word-break:break-all;">${escapeHtml(personaPreview)}</div>
                    </div>
                    <span style="font-size:14px;color:#7c3aed;flex-shrink:0;margin-top:2px;">→</span>
                `;
                row.addEventListener('click', () => {
                    if (typeof onSelect === 'function') onSelect(ex);
                    overlay.remove();
                });
                listEl.appendChild(row);
            }
        }
    }

    renderList('');
    searchInput.addEventListener('input', () => renderList(searchInput.value));
    setTimeout(() => searchInput.focus(), 100);
}

// ── Tab: Skills & Tools Config ──
async function loadConfigTab(agentName, contentEl, overlay) {
    contentEl.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ' + t('loading') + '</div>';
    try {
        const [detailRes, toolsRes] = await Promise.all([
            fetch('/proxy_openclaw_agent_detail?name=' + encodeURIComponent(agentName)).then(r => r.json()),
            fetch('/proxy_openclaw_tool_groups').then(r => r.json()),
        ]);

        if (!detailRes.ok) {
            contentEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + (detailRes.error || 'Error') + '</div>';
            return;
        }

        const agent = detailRes.agent;
        const allSkills = detailRes.skills || [];
        const toolGroups = (toolsRes.ok ? toolsRes.groups : {}) || {};
        const toolProfiles = (toolsRes.ok ? toolsRes.profiles : {}) || {};
        const agentSkills = new Set(agent.skills || []);
        const skillsAll = agent.skills_all;

        const toolProfile = (agent.tools && agent.tools.profile) || '';
        const alsoAllow = (agent.tools && agent.tools.alsoAllow) || [];
        const deny = (agent.tools && agent.tools.deny) || [];

        let toolsHtml = `<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;">
            <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:8px;">🔧 ${t('orch_oc_cfg_tools')}</div>
            <div style="margin-bottom:8px;">
                <label style="font-size:11px;color:#6b7280;">${t('orch_oc_cfg_profile')}</label>
                <select id="orch-cfg-profile" style="width:100%;padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:11px;margin-top:2px;">
                    <option value="">${t('orch_oc_cfg_no_profile')}</option>`;
        for (const [pname, pinfo] of Object.entries(toolProfiles)) {
            toolsHtml += `<option value="${pname}" ${pname === toolProfile ? 'selected' : ''}>${pname} — ${pinfo.description}</option>`;
        }
        toolsHtml += `</select></div>`;
        toolsHtml += `<div style="font-size:10px;color:#6b7280;margin-bottom:6px;">${t('orch_oc_cfg_tool_toggles')}</div>`;
        toolsHtml += `<div style="display:flex;flex-wrap:wrap;gap:4px;" id="orch-cfg-tool-toggles">`;
        for (const [gname, tools] of Object.entries(toolGroups)) {
            toolsHtml += `<div style="width:100%;font-size:10px;font-weight:600;color:#374151;margin-top:4px;">${gname}</div>`;
            for (const tn of tools) {
                const isDenied = deny.includes(tn) || deny.includes(gname);
                const isAllowed = alsoAllow.includes(tn) || alsoAllow.includes(gname);
                let state = 'default';
                if (isDenied) state = 'deny';
                else if (isAllowed) state = 'allow';
                toolsHtml += `<label style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:2px 6px;border:1px solid #e5e7eb;border-radius:4px;cursor:pointer;background:${state === 'deny' ? '#fef2f2' : state === 'allow' ? '#f0fdf4' : '#fff'};" data-tool="${tn}" data-state="${state}">
                    <span class="orch-cfg-tool-icon">${state === 'deny' ? '🚫' : state === 'allow' ? '✅' : '⚪'}</span>
                    <span>${tn}</span>
                </label>`;
            }
        }
        toolsHtml += `</div></div>`;

        let skillsHtml = `<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
                <div style="font-size:12px;font-weight:600;color:#374151;">🧩 ${t('orch_oc_cfg_skills')}</div>
                <label style="font-size:10px;display:flex;align-items:center;gap:4px;color:#6b7280;">
                    <input type="checkbox" id="orch-cfg-skills-all" ${skillsAll ? 'checked' : ''} style="margin:0;">
                    ${t('orch_oc_cfg_skills_all')}
                </label>
            </div>
            <div id="orch-cfg-skills-list" style="display:flex;flex-wrap:wrap;gap:3px;max-height:200px;overflow-y:auto;${skillsAll ? 'opacity:0.4;pointer-events:none;' : ''}">`;
        for (const sk of allSkills) {
            const sname = sk.name || sk;
            const checked = skillsAll || agentSkills.has(sname);
            skillsHtml += `<label style="display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:2px 6px;border:1px solid #e5e7eb;border-radius:4px;cursor:pointer;background:${checked ? '#dbeafe' : '#fff'};">
                <input type="checkbox" class="orch-cfg-skill-cb" value="${escapeHtml(sname)}" ${checked ? 'checked' : ''} style="margin:0;width:12px;height:12px;">
                <span>${escapeHtml(sname)}</span>
            </label>`;
        }
        skillsHtml += `</div></div>`;

        const saveHtml = `<div style="display:flex;justify-content:flex-end;gap:8px;padding-top:8px;border-top:1px solid #e5e7eb;">
            <button id="orch-cfg-save" style="padding:6px 16px;border-radius:6px;border:none;background:#2563eb;color:white;cursor:pointer;font-size:12px;">💾 ${t('orch_oc_save')}</button>
        </div>`;

        contentEl.innerHTML = toolsHtml + skillsHtml + saveHtml;

        // Skills "all" toggle
        contentEl.querySelector('#orch-cfg-skills-all').addEventListener('change', (e) => {
            const listEl = contentEl.querySelector('#orch-cfg-skills-list');
            listEl.style.opacity = e.target.checked ? '0.4' : '1';
            listEl.style.pointerEvents = e.target.checked ? 'none' : '';
        });

        contentEl.querySelectorAll('.orch-cfg-skill-cb').forEach(cb => {
            cb.addEventListener('change', () => { cb.parentElement.style.background = cb.checked ? '#dbeafe' : '#fff'; });
        });

        // Tool toggle (3-state)
        contentEl.querySelectorAll('[data-tool]').forEach(label => {
            label.addEventListener('click', (e) => {
                if (e.target.tagName === 'INPUT') return;
                e.preventDefault();
                const current = label.dataset.state;
                let next = current === 'default' ? 'allow' : current === 'allow' ? 'deny' : 'default';
                label.dataset.state = next;
                label.querySelector('.orch-cfg-tool-icon').textContent = next === 'deny' ? '🚫' : next === 'allow' ? '✅' : '⚪';
                label.style.background = next === 'deny' ? '#fef2f2' : next === 'allow' ? '#f0fdf4' : '#fff';
            });
        });

        // Save
        contentEl.querySelector('#orch-cfg-save').addEventListener('click', async () => {
            const saveBtn = contentEl.querySelector('#orch-cfg-save');
            saveBtn.disabled = true; saveBtn.textContent = '⏳';
            const isSkillsAll = contentEl.querySelector('#orch-cfg-skills-all').checked;
            let skillsValue = null;
            if (!isSkillsAll) {
                skillsValue = [];
                contentEl.querySelectorAll('.orch-cfg-skill-cb:checked').forEach(cb => skillsValue.push(cb.value));
            }
            const profile = contentEl.querySelector('#orch-cfg-profile').value;
            const newAlsoAllow = [], newDeny = [];
            contentEl.querySelectorAll('[data-tool]').forEach(label => {
                const st = label.dataset.state;
                if (st === 'allow') newAlsoAllow.push(label.dataset.tool);
                else if (st === 'deny') newDeny.push(label.dataset.tool);
            });
            try {
                const r = await fetch('/proxy_openclaw_update_config', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ agent_name: agentName, skills: skillsValue, tools: { profile, alsoAllow: newAlsoAllow, deny: newDeny } }),
                });
                const res = await r.json();
                if (r.ok && res.ok) {
                    orchToast('✅ ' + t('orch_oc_cfg_saved', {name: agentName}));
                    orchLoadOpenClawSessions();
                } else { orchToast('❌ ' + (res.error || res.errors?.join(', ') || 'Error')); }
            } catch(e) { orchToast('❌ ' + t('orch_toast_net_error')); }
            saveBtn.disabled = false; saveBtn.textContent = '💾 ' + t('orch_oc_save');
        });
    } catch(e) {
        contentEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + t('orch_toast_net_error') + '</div>';
    }
}

// ── Tab: Channels Binding ──
async function loadChannelsTab(agentName, contentEl, overlay) {
    contentEl.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:20px;font-size:11px;">⏳ ' + t('loading') + '</div>';
    try {
        const [chRes, bindRes] = await Promise.all([
            fetch('/proxy_openclaw_channels').then(r => r.json()),
            fetch('/proxy_openclaw_agent_bindings?agent=' + encodeURIComponent(agentName)).then(r => r.json()),
        ]);

        if (!chRes.ok) {
            contentEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + (chRes.error || 'Error') + '</div>';
            return;
        }

        const channels = chRes.channels || [];
        const currentBindings = new Set(bindRes.ok ? (bindRes.bindings || []) : []);

        if (channels.length === 0) {
            contentEl.innerHTML = `<div style="padding:20px;text-align:center;font-size:12px;color:#9ca3af;">
                <div style="font-size:24px;margin-bottom:8px;">📡</div>
                <div style="margin-bottom:12px;font-weight:600;color:#6b7280;">${t('orch_oc_ch_empty')}</div>
                <div style="text-align:left;font-size:11px;color:#9ca3af;background:#f9fafb;border-radius:8px;padding:12px;margin:0 auto;max-width:360px;line-height:1.7;">
                    <div style="margin-bottom:8px;color:#6b7280;font-weight:600;">${t('orch_oc_ch_guide_title')}</div>
                    <div style="font-family:monospace;font-size:10px;background:#1f2937;color:#d1fae5;padding:8px 10px;border-radius:6px;margin-bottom:8px;overflow-x:auto;white-space:pre-line;">
# Telegram
openclaw channels add --channel telegram --token BOT_TOKEN

# Slack
openclaw channels add --channel slack --token BOT_TOKEN</div>
                    <div style="color:#9ca3af;font-size:10px;">${t('orch_oc_ch_guide_docs')} <a href="https://docs.openclaw.ai/gateway/configuration" target="_blank" style="color:#3b82f6;text-decoration:underline;">docs.openclaw.ai</a></div>
                </div>
            </div>`;
            return;
        }

        // Group channels by channel name
        const grouped = {};
        for (const ch of channels) {
            if (!grouped[ch.channel]) grouped[ch.channel] = [];
            grouped[ch.channel].push(ch);
        }

        let html = `<div style="font-size:11px;color:#6b7280;margin-bottom:8px;">${t('orch_oc_ch_desc')}</div>`;
        html += `<div style="display:flex;flex-direction:column;gap:8px;">`;

        for (const [chName, accounts] of Object.entries(grouped)) {
            html += `<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;">
                <div style="font-size:12px;font-weight:600;color:#374151;margin-bottom:6px;">📡 ${escapeHtml(chName)}</div>
                <div style="display:flex;flex-wrap:wrap;gap:4px;">`;
            for (const acc of accounts) {
                const bindKey = acc.bind_key || (chName + ':' + acc.account);
                const isBound = currentBindings.has(bindKey) || currentBindings.has(chName + ':' + acc.account);
                html += `<button class="orch-ch-bind-btn" data-channel="${escapeHtml(bindKey)}" data-bound="${isBound}"
                    style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:6px;border:1px solid ${isBound ? '#86efac' : '#e5e7eb'};background:${isBound ? '#f0fdf4' : '#fff'};cursor:pointer;font-size:11px;transition:all .15s;color:${isBound ? '#16a34a' : '#6b7280'};"
                    onmouseenter="this.style.boxShadow='0 1px 4px rgba(0,0,0,0.1)'" onmouseleave="this.style.boxShadow='none'">
                    <span class="orch-ch-icon">${isBound ? '🔗' : '⚪'}</span>
                    <span>${escapeHtml(acc.account)}</span>
                </button>`;
            }
            html += `</div></div>`;
        }
        html += `</div>`;

        contentEl.innerHTML = html;

        // Bind/unbind click handlers
        contentEl.querySelectorAll('.orch-ch-bind-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const channel = btn.dataset.channel;
                const wasBound = btn.dataset.bound === 'true';
                const action = wasBound ? 'unbind' : 'bind';
                btn.disabled = true;
                btn.querySelector('.orch-ch-icon').textContent = '⏳';
                try {
                    const r = await fetch('/proxy_openclaw_agent_bind', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ agent: agentName, channel, action }),
                    });
                    const res = await r.json();
                    if (r.ok && res.ok) {
                        const nowBound = !wasBound;
                        btn.dataset.bound = String(nowBound);
                        btn.querySelector('.orch-ch-icon').textContent = nowBound ? '🔗' : '⚪';
                        btn.style.borderColor = nowBound ? '#86efac' : '#e5e7eb';
                        btn.style.background = nowBound ? '#f0fdf4' : '#fff';
                        btn.style.color = nowBound ? '#16a34a' : '#6b7280';
                        orchToast(`${nowBound ? '🔗' : '⛓️‍💥'} ${agentName} ${action} ${channel}`);
                    } else {
                        orchToast('❌ ' + (res.error || 'Error'));
                        btn.querySelector('.orch-ch-icon').textContent = wasBound ? '🔗' : '⚪';
                    }
                } catch(e) {
                    orchToast('❌ ' + t('orch_toast_net_error'));
                    btn.querySelector('.orch-ch-icon').textContent = wasBound ? '🔗' : '⚪';
                }
                btn.disabled = false;
            });
        });
    } catch(e) {
        contentEl.innerHTML = '<div style="color:#ef4444;padding:20px;text-align:center;font-size:11px;">❌ ' + t('orch_toast_net_error') + '</div>';
    }
}

// ── Add OpenClaw Agent modal ──
function orchShowAddOpenClawModal() {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-add-openclaw-overlay';
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:340px;max-width:460px;">
            <h3>🦞 ${t('orch_add_openclaw_title')}</h3>
            <div style="display:flex;flex-direction:column;gap:10px;margin:12px 0;">
                <label style="font-size:11px;font-weight:600;color:#374151;">
                    ${t('orch_openclaw_agent_name')}
                    <div style="display:flex;align-items:center;gap:0;margin-top:2px;">
                        ${(orch.teamEnabled && orch.teamName) ? '<span style="padding:6px 4px 6px 8px;border:1px solid #d1d5db;border-right:none;border-radius:6px 0 0 6px;font-size:12px;background:#f3f4f6;color:#6b7280;white-space:nowrap;">' + escapeHtml(orch.teamName) + '_</span>' : ''}
                        <input id="orch-oc-name" type="text" placeholder="e.g. work, research, coding"
                               style="width:100%;padding:6px 8px;border:1px solid #d1d5db;${(orch.teamEnabled && orch.teamName) ? 'border-radius:0 6px 6px 0;' : 'border-radius:6px;'}font-size:12px;"
                               pattern="[a-zA-Z0-9_-]+" title="Only alphanumeric, dash, underscore">
                    </div>
                </label>
                <label style="font-size:11px;font-weight:600;color:#374151;">
                    Workspace ${t('orch_openclaw_ws_path')}
                    <div style="display:flex;gap:4px;align-items:center;margin-top:2px;">
                        <input id="orch-oc-workspace" type="text" placeholder="${t('orch_openclaw_ws_loading')}"
                               style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:11px;font-family:monospace;color:#374151;">
                        <button id="orch-oc-ws-reset" type="button" title="${t('orch_openclaw_ws_reset')}"
                                style="padding:4px 6px;border:1px solid #d1d5db;border-radius:4px;background:#f9fafb;cursor:pointer;font-size:11px;white-space:nowrap;">↺</button>
                    </div>
                </label>
                <div style="font-size:10px;color:#6b7280;background:#f9fafb;border-radius:6px;padding:8px;">
                    ${t('orch_openclaw_workspace_hint')}
                </div>
                <div style="border:1px dashed #c4b5fd;border-radius:8px;padding:10px;background:#faf5ff;">
                    <div style="display:flex;align-items:center;justify-content:space-between;">
                        <span style="font-size:11px;font-weight:600;color:#7c3aed;">📥 ${t('orch_oc_create_import_expert')}</span>
                        <button id="orch-oc-pick-expert" type="button" style="padding:3px 10px;border-radius:4px;border:1px solid #8b5cf6;background:#f5f3ff;color:#7c3aed;cursor:pointer;font-size:10px;font-weight:500;">${t('orch_oc_create_pick_expert')}</button>
                    </div>
                    <div id="orch-oc-expert-preview" style="display:none;margin-top:8px;padding:6px 8px;background:white;border-radius:6px;border:1px solid #e5e7eb;font-size:11px;color:#374151;"></div>
                </div>
            </div>
            <div class="orch-modal-btns">
                <button id="orch-oc-cancel" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_modal_cancel')}</button>
                <button id="orch-oc-create" style="padding:6px 14px;border-radius:6px;border:none;background:#10b981;color:white;cursor:pointer;font-size:12px;">🦞 ${t('orch_openclaw_create_btn')}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#orch-oc-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    const nameInp = document.getElementById('orch-oc-name');
    const wsInp = document.getElementById('orch-oc-workspace');
    let parentDir = '';       // default workspace parent dir from server
    let wsManualEdit = false; // whether user has manually edited workspace

    // Derive the workspace-friendly agent name (includes team prefix when active)
    function _orchWsAgentName() {
        const n = nameInp.value.trim();
        if (!n) return '';
        return (orch.teamEnabled && orch.teamName) ? (orch.teamName + '_' + n) : n;
    }

    // Fetch default workspace parent dir
    fetch('/proxy_openclaw_default_workspace').then(r => r.json()).then(res => {
        if (res.ok && res.parent_dir) {
            parentDir = res.parent_dir;
            // If name already typed, populate workspace
            const wn = _orchWsAgentName();
            if (wn && !wsManualEdit) {
                wsInp.value = parentDir + '/workspace-' + wn;
            }
            wsInp.placeholder = parentDir + '/workspace-...';
        } else {
            wsInp.placeholder = t('orch_openclaw_ws_fallback');
        }
    }).catch(() => { wsInp.placeholder = t('orch_openclaw_ws_fallback'); });

    // Name changes → auto-update workspace (unless user has manually edited it)
    nameInp.addEventListener('input', () => {
        nameInp.style.borderColor = '#d1d5db';
        nameInp.style.background = '';
        if (!wsManualEdit) {
            const wn = _orchWsAgentName();
            if (wn) {
                wsInp.value = (parentDir || '') + '/workspace-' + wn;
            } else {
                wsInp.value = '';
            }
        }
    });

    // Track manual workspace edits
    wsInp.addEventListener('input', () => { wsManualEdit = true; });

    // Reset button: revert workspace to auto-derived value
    overlay.querySelector('#orch-oc-ws-reset').addEventListener('click', () => {
        wsManualEdit = false;
        const wn = _orchWsAgentName();
        wsInp.value = wn ? ((parentDir || '') + '/workspace-' + wn) : '';
        wsInp.style.borderColor = '#d1d5db';
    });

    setTimeout(() => nameInp.focus(), 100);

    // ── Expert import picker for creation ──
    let selectedExpertContent = null; // stores identity content to write after creation
    const expertPreview = overlay.querySelector('#orch-oc-expert-preview');
    overlay.querySelector('#orch-oc-pick-expert').addEventListener('click', () => {
        orchShowImportExpertPicker((expert) => {
            selectedExpertContent = '# ' + (expert.name || expert.tag) + '\n\n' + (expert.persona || '');
            expertPreview.style.display = 'block';
            expertPreview.innerHTML = '<div style="display:flex;align-items:center;gap:6px;"><span style="font-size:16px;">' + (expert.emoji || '⭐') + '</span><span style="font-weight:600;">' + escapeHtml(expert.name) + '</span><button id="orch-oc-clear-expert" type="button" style="margin-left:auto;padding:1px 6px;border:1px solid #d1d5db;border-radius:4px;background:#f9fafb;cursor:pointer;font-size:10px;color:#6b7280;">✕</button></div>'
                + '<div style="font-size:10px;color:#6b7280;margin-top:4px;max-height:60px;overflow:hidden;white-space:pre-wrap;word-break:break-all;">' + escapeHtml((expert.persona || '').slice(0, 120) + ((expert.persona || '').length > 120 ? '…' : '')) + '</div>';
            expertPreview.querySelector('#orch-oc-clear-expert').addEventListener('click', (ev) => {
                ev.stopPropagation();
                selectedExpertContent = null;
                expertPreview.style.display = 'none';
                expertPreview.innerHTML = '';
            });
        });
    });

    overlay.querySelector('#orch-oc-create').addEventListener('click', async () => {
        const shortName = nameInp.value.trim();
        const workspace = wsInp.value.trim();
        // globalName = the real OpenClaw agent name (with team prefix if team mode)
        let globalName = shortName;
        if (orch.teamEnabled && orch.teamName) {
            globalName = orch.teamName + '_' + shortName;
        }
        if (!globalName) { orchToast(t('orch_openclaw_name_required')); return; }
        if (!/^[a-zA-Z0-9_-]+$/.test(globalName)) { orchToast(t('orch_openclaw_name_invalid')); return; }
        if (!workspace) { orchToast(t('orch_openclaw_ws_required')); return; }
        const btn = overlay.querySelector('#orch-oc-create');
        btn.disabled = true;
        btn.textContent = '⏳ ' + t('orch_openclaw_creating');
        try {
            const r = await fetch('/proxy_openclaw_add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: globalName, workspace }),
            });
            const res = await r.json();
            if (r.ok && res.ok) {
                // If expert was selected, write IDENTITY.md right after creation
                if (selectedExpertContent) {
                    try {
                        await fetch('/proxy_openclaw_workspace_file', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ workspace, filename: 'IDENTITY.md', content: selectedExpertContent }),
                        });
                        orchToast('📥 ' + t('orch_oc_import_done', { name: globalName }));
                    } catch(e) { /* ignore write error, user can edit later */ }
                }
                // Save to external_agents.json so the engine can resolve
                // name (short display name) ↔ global_name (real OpenClaw agent name)
                if (orch.teamEnabled && orch.teamName) {
                    try {
                        await fetch('/teams/' + encodeURIComponent(orch.teamName) + '/members/external', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                name: shortName,
                                tag: 'openclaw',
                                global_name: globalName
                            })
                        });
                    } catch(e) { console.warn('Failed to save to external_agents.json:', e); }
                }
                orchToast('🦞 ' + t('orch_openclaw_created', {name: globalName}));
                overlay.remove();
                orchLoadOpenClawSessions();
                // Auto-open config modal for the newly created agent
                setTimeout(() => orchShowAgentConfigModal(globalName), 500);
            } else {
                if (r.status === 409) {
                    orchToast('⚠️ ' + t('orch_openclaw_exists', {name: globalName}));
                    nameInp.style.borderColor = '#ef4444';
                    nameInp.style.background = '#fef2f2';
                    nameInp.focus();
                    nameInp.select();
                } else {
                    orchToast('❌ ' + (res.error || t('orch_toast_net_error')));
                }
                btn.disabled = false;
                btn.textContent = '🦞 ' + t('orch_openclaw_create_btn');
            }
        } catch(e) {
            orchToast(t('orch_toast_net_error'));
            btn.disabled = false;
            btn.textContent = '🦞 ' + t('orch_openclaw_create_btn');
        }
    });
    nameInp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') overlay.querySelector('#orch-oc-create').click();
    });
}

function orchRenderSidebar() {
    orchRenderExpertSidebar();
    orchBindControlCards();
}

// ── Settings ──
function orchSetupSettings() {
    document.getElementById('orch-threshold').addEventListener('input', e => {
        document.getElementById('orch-threshold-val').textContent = e.target.value;
    });
}
function orchGetSettings() {
    return {
        repeat: document.getElementById('orch-repeat').checked,
        max_rounds: parseInt(document.getElementById('orch-rounds').value) || 5,
        cluster_threshold: parseInt(document.getElementById('orch-threshold').value) || 150,
    };
}

// ── Node Management ──
function orchNextInstance(data) {
    // Compute next instance number for this agent identity
    const key = data.type === 'session_agent' ? ('sa:' + (data.session_id||'')) : data.type === 'external' ? ('ext:' + (data.ext_id || data.tag||'custom')) : ('ex:' + (data.tag||'custom'));
    let maxInst = 0;
    orch.nodes.forEach(n => {
        const nk = n.type === 'session_agent' ? ('sa:' + (n.session_id||'')) : n.type === 'external' ? ('ext:' + (n.ext_id || n.tag||'custom')) : ('ex:' + (n.tag||'custom'));
        if (nk === key && n.instance > maxInst) maxInst = n.instance;
    });
    return maxInst + 1;
}

function orchAddNode(data, x, y) {
    const id = 'on' + orch.nid++;
    const inst = data.instance || orchNextInstance(data);
    // session_agent is always stateful
    const nodeStateful = data.type === 'session_agent' ? true : (data.stateful || false);
    const node = { id, name: data.name, tag: data.tag||'custom', emoji: data.emoji||'⭐', x: Math.round(x), y: Math.round(y), type: data.type||'expert', temperature: data.temperature||0.5, author: data.author||t('orch_default_author'), content: data.content||'', session_id: data.session_id||'', source: data.source||'', instance: inst, stateful: nodeStateful };
    // Preserve bilingual name fields
    if (data.name_zh) node.name_zh = data.name_zh;
    if (data.name_en) node.name_en = data.name_en;
    // Preserve selector node flag
    if (data.isSelector) node.isSelector = true;
    // Preserve session agent name for YAML generation
    if (data.type === 'session_agent' && data.agent_name) node.agent_name = data.agent_name;
    // Preserve external agent extra fields
    if (data.type === 'external') {
        node.api_url = data.api_url || '';
        node.ext_id = data.ext_id || '1';
        node.platform = data.platform || '';
        if (data.headers && typeof data.headers === 'object') node.headers = data.headers;
        if (data.api_key) node.api_key = data.api_key;
        if (data.model) node.model = data.model;
    }
    if (data.type === 'script') {
        node.script_command = data.script_command || '';
        node.script_unix_command = data.script_unix_command || '';
        node.script_windows_command = data.script_windows_command || '';
        node.script_timeout = data.script_timeout ?? '';
        node.script_cwd = data.script_cwd || '';
    }
    if (data.type === 'human') {
        node.human_prompt = data.human_prompt || '';
        node.human_author = data.human_author || t('orch_default_author');
        node.human_reply_to = data.human_reply_to ?? '';
    }
    orch.nodes.push(node);
    orchRenderNode(node);
    orchUpdateYaml();
    orchUpdateStatus();
    document.getElementById('orch-canvas-hint').style.display = 'none';
    return node;
}

function orchAddNodeCenter(data) {
    const area = document.getElementById('orch-canvas-area');
    const cx = (area.offsetWidth / 2 - orch.panX) / orch.zoom - 60;
    const cy = (area.offsetHeight / 2 - orch.panY) / orch.zoom - 20;
    const n = orch.nodes.length;
    const angle = n * 137.5 * Math.PI / 180;
    const radius = 80 * Math.sqrt(n) * 0.5;
    return orchAddNode(data, cx + radius * Math.cos(angle), cy + radius * Math.sin(angle));
}

function orchRenderNode(node) {
    const area = document.getElementById('orch-canvas-inner');
    const el = document.createElement('div');
    const isSession = node.type === 'session_agent';
    const isExternal = node.type === 'external';
    const isScript = node.type === 'script';
    const isHuman = node.type === 'human';
    el.className = 'orch-node'
        + (node.type === 'manual' ? ' manual-type' : '')
        + (isSession ? ' session-type' : '')
        + (isExternal ? ' external-type' : '')
        + (isScript ? ' script-type' : '')
        + (isHuman ? ' human-type' : '')
        + (node.isSelector ? ' selector-type' : '');
    el.id = 'onode-' + node.id;
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    if (isSession) el.style.borderColor = '#6366f1';
    if (isExternal) el.style.borderColor = '#2ecc71';
    if (isScript) el.style.borderColor = '#3b82f6';
    if (isHuman) el.style.borderColor = '#8b5cf6';

    const status = orch.sessionStatuses[node.tag] || orch.sessionStatuses[node.name] || 'idle';
    // Bilingual display name for canvas node
    const _nodeIsZh = (typeof currentLang !== 'undefined' && currentLang === 'zh-CN');
    const nodeDisplayName = _nodeIsZh ? (node.name_zh || node.name) : (node.name_en || node.name);
    const instBadge = `<span style="display:inline-block;background:#2563eb;color:#fff;font-size:9px;font-weight:700;border-radius:50%;min-width:16px;height:16px;line-height:16px;text-align:center;margin-left:3px;flex-shrink:0;">${node.instance||1}</span>`;
    let tagTextPlain = '';
    let tagLineStyle = '';
    if (isSession) {
        const tagLabel = node.tag ? `🏷️${node.tag} · ` : '';
        tagTextPlain = `${tagLabel}#${(node.session_id||'').slice(-8)}`;
        tagLineStyle = 'color:#6366f1;font-family:monospace;';
    } else if (isExternal) {
        let extDesc = '';
        if (node.api_url) {
            extDesc = `🌐 ${node.api_url}`;
            if (node.model) extDesc += '\n📦 ' + node.model;
        } else {
            extDesc = '⚠️ Double-click to set URL';
        }
        if (node.headers && typeof node.headers === 'object') {
            const hdrParts = Object.entries(node.headers).map(([k,v]) => `${k}: ${v}`);
            if (hdrParts.length) extDesc += '\n' + hdrParts.join('\n');
        }
        tagTextPlain = extDesc.replace(/\n+/g, ' · ');
        tagLineStyle = 'color:#2ecc71;word-break:break-all;font-size:9px;';
    } else if (isScript) {
        const timeoutLabel = node.script_timeout ? ` · ⏱ ${node.script_timeout}s` : '';
        const cwdLabel = node.script_cwd ? ` · 📁 ${node.script_cwd}` : '';
        tagTextPlain = `script${timeoutLabel}${cwdLabel}`;
        tagLineStyle = 'color:#3b82f6;';
    } else if (isHuman) {
        const authorLabel = node.human_author || t('orch_default_author');
        const replyLabel = node.human_reply_to !== '' && node.human_reply_to !== null && node.human_reply_to !== undefined
            ? ` · ↩ ${node.human_reply_to}`
            : '';
        tagTextPlain = `${authorLabel}${replyLabel}`;
        tagLineStyle = 'color:#8b5cf6;';
    } else {
        tagTextPlain = String(node.tag || '');
        tagLineStyle = '';
    }
    const previewText = orchNodePreviewText(node);
    const previewIcon = isScript ? '🧪' : (isHuman ? '💬' : '📋');
    const textLayout = orchGetTextLayout();
    const displayName = textLayout && typeof textLayout.fitSingleLine === 'function'
        ? textLayout.fitSingleLine(nodeDisplayName, 148, { font: '600 12px Arial', lineHeight: 14, suffix: '…' })
        : { text: nodeDisplayName, width: Math.min(148, nodeDisplayName.length * 8) };
    const displayTag = textLayout && typeof textLayout.fitSingleLine === 'function'
        ? textLayout.fitSingleLine(tagTextPlain, isExternal ? 164 : 148, { font: '10px Arial', lineHeight: 12, suffix: '…' })
        : { text: tagTextPlain, width: Math.min(isExternal ? 164 : 148, String(tagTextPlain || '').length * 6.5) };
    const displayInstr = (node.type !== 'manual' && previewText && textLayout && typeof textLayout.fitSingleLine === 'function')
        ? textLayout.fitSingleLine(previewIcon + ' ' + previewText, 148, { font: '9px Arial', lineHeight: 12, suffix: '…' })
        : { text: previewIcon + ' ' + (previewText.length > 20 ? previewText.slice(0, 20) + '…' : previewText), width: Math.min(148, previewText.length * 5.8) };
    const infoWidth = Math.max(110, Math.min(isExternal ? 180 : 160, Math.ceil(Math.max(displayName.width || 0, displayTag.width || 0, displayInstr.width || 0)) + 12));
    const nodeWidth = Math.max(126, Math.min(isExternal ? 252 : 224, infoWidth + 54));
    const nodeHeight = previewText && node.type !== 'manual' ? 64 : 54;
    el.style.width = nodeWidth + 'px';
    el.style.minHeight = nodeHeight + 'px';
    const tagLineHtml = `<div class="orch-node-tag" title="${escapeHtml(tagTextPlain)}" style="${tagLineStyle}max-width:${infoWidth}px;">${escapeHtml(displayTag.text || tagTextPlain)}</div>`;
    const instrPreview = (node.type !== 'manual' && previewText) ? `<div class="orch-node-instr" title="${escapeHtml(previewText)}" style="font-size:9px;color:#6b7280;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:${infoWidth}px;margin-top:1px;">${escapeHtml(displayInstr.text || '')}</div>` : '';
    const statefulBadge = (node.stateful && !isExternal && !isScript && !isHuman) ? '<span style="display:inline-block;background:#8b5cf6;color:#fff;font-size:8px;font-weight:600;border-radius:3px;padding:0 3px;margin-left:3px;vertical-align:middle;" title="Stateful">⚡S</span>' : '';
    const selectorBadgeHtml = node.isSelector ? '<div class="orch-selector-badge">🎯 SELECTOR</div>' : '';
    el.innerHTML = `
        <span class="orch-node-emoji">${node.emoji}</span>
        <div style="min-width:0;flex:1;max-width:${infoWidth}px;"><div class="orch-node-name" style="display:flex;align-items:center;max-width:${infoWidth}px;" title="${escapeHtml(nodeDisplayName)}">${escapeHtml(displayName.text || nodeDisplayName)}${instBadge}${statefulBadge}</div>${tagLineHtml}${instrPreview}</div>
        <div class="orch-node-del" title="${t('orch_node_remove')}">×</div>
        <div class="orch-port port-in" data-node="${node.id}" data-dir="in"></div>
        <div class="orch-port port-out" data-node="${node.id}" data-dir="out"></div>
        <div class="orch-node-status ${status}">
        ${selectorBadgeHtml}
    `;
    const delBtn = el.querySelector('.orch-node-del');
    const handleDeleteNode = (e) => {
        e.preventDefault();
        e.stopPropagation();
        orchRemoveNode(node.id);
    };
    delBtn.addEventListener('click', handleDeleteNode);
    delBtn.addEventListener('touchend', handleDeleteNode, { passive: false });

    el.addEventListener('mousedown', e => {
        if (e.target.classList.contains('orch-port') || e.target.classList.contains('orch-node-del')) return;
        e.stopPropagation();
        if (!e.shiftKey && !orch.selectedNodes.has(node.id)) orchClearSelection();
        orchSelectNode(node.id);
        const cp = orchClientToCanvas(e.clientX, e.clientY);
        orch.dragging = { nodeId: node.id, offX: cp.x - node.x, offY: cp.y - node.y, multi: orch.selectedNodes.size > 1, starts: {} };
        if (orch.selectedNodes.size > 1) {
            orch.selectedNodes.forEach(nid => { const n = orch.nodes.find(nn=>nn.id===nid); if(n) orch.dragging.starts[nid]={x:n.x,y:n.y}; });
        }
    });

    el.querySelectorAll('.orch-port').forEach(port => {
        port.addEventListener('mousedown', e => {
            e.stopPropagation();
            if (port.dataset.dir === 'out') {
                const portRect = port.getBoundingClientRect();
                const cp = orchClientToCanvas(portRect.left + 5, portRect.top + 5);
                orch.connecting = { sourceId: node.id, sx: cp.x, sy: cp.y };
            }
        });
        port.addEventListener('mouseup', e => {
            e.stopPropagation();
            if (orch.connecting && port.dataset.dir === 'in' && port.dataset.node !== orch.connecting.sourceId) {
                orchAddEdge(orch.connecting.sourceId, node.id);
            }
            orch.connecting = null;
            orchRemoveTempLine();
        });
    });

    el.addEventListener('contextmenu', e => {
        e.preventDefault(); e.stopPropagation();
        if (!orch.selectedNodes.has(node.id)) { orchClearSelection(); orchSelectNode(node.id); }
        orchShowContextMenu(e.clientX, e.clientY, node);
    });
    el.addEventListener('dblclick', () => {
        if (node.type === 'manual') orchShowManualModal(node);
        else if (node.type === 'external') orchShowExternalModal(node);
        else if (node.type === 'script') orchShowScriptModal(node);
        else if (node.type === 'human') orchShowHumanModal(node);
        else orchShowInstructionModal(node);
    });
    area.appendChild(el);
}

function orchRemoveNode(id) {
    orch.nodes = orch.nodes.filter(n => n.id !== id);
    // Clean up conditional edge elseTarget references before filtering
    orch.edges.forEach(e => {
        if (e.elseTarget === id) { e.elseTarget = ''; }
    });
    orch.edges = orch.edges.filter(e => e.source !== id && e.target !== id);
    orch.selectedNodes.delete(id);
    orch.groups.forEach(g => { g.nodeIds = g.nodeIds.filter(nid => nid !== id); });
    const el = document.getElementById('onode-' + id);
    if (el) el.remove();
    orchRenderEdges();
    orchUpdateNodeBadges();
    orchUpdateYaml();
    orchUpdateStatus();
    if (orch.nodes.length === 0) document.getElementById('orch-canvas-hint').style.display = '';
}

function orchSelectNode(id) { orch.selectedNodes.add(id); const el=document.getElementById('onode-'+id); if(el) el.classList.add('selected'); }
function orchClearSelection() { orch.selectedNodes.forEach(id => { const el=document.getElementById('onode-'+id); if(el) el.classList.remove('selected'); }); orch.selectedNodes.clear(); }

// ── Edge Management ──
function orchAddEdge(src, tgt, edgeType) {
    edgeType = edgeType || 'fixed';
    if (orch.edges.some(e => e.source === src && e.target === tgt)) return;
    orch.edges.push({ id: 'oe' + orch.eid++, source: src, target: tgt, edgeType: edgeType, condition: '', thenTarget: '', elseTarget: '' });
    orchRenderEdges();
    orchUpdateNodeBadges();
    orchUpdateYaml();
}

function orchRenderEdges() {
    const svg = document.getElementById('orch-edge-svg');
    const defs = svg.querySelector('defs');
    svg.innerHTML = '';
    if (defs) svg.appendChild(defs);
    else {
        const nd = document.createElementNS('http://www.w3.org/2000/svg','defs');
        nd.innerHTML = `<marker id="orch-arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#2563eb" /></marker>
            <marker id="orch-arrowhead-green" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#16a34a" /></marker>
            <marker id="orch-arrowhead-orange" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#ea580c" /></marker>
            <marker id="orch-arrowhead-purple" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#8b5cf6" /></marker>`;
        svg.appendChild(nd);
    }
    // Build selector edge choice number map: edge.id → choice number
    const selectorChoiceMap = {};
    orch.nodes.forEach(n => {
        if (!n.isSelector) return;
        const outEdges = orch.edges.filter(e => e.source === n.id && !e._isElseSibling);
        outEdges.forEach((e, idx) => { selectorChoiceMap[e.id] = idx + 1; });
    });

    orch.edges.forEach(edge => {
        const sn = orch.nodes.find(n => n.id === edge.source);
        const tn = orch.nodes.find(n => n.id === edge.target);
        if (!sn || !tn) return;
        const se = document.getElementById('onode-' + edge.source);
        const te = document.getElementById('onode-' + edge.target);
        if (!se || !te) return;
        const x1 = sn.x + se.offsetWidth, y1 = sn.y + se.offsetHeight/2;
        const x2 = tn.x, y2 = tn.y + te.offsetHeight/2;
        const isCond = edge.edgeType === 'conditional';
        // Fix: determine else-branch by data flag, not spatial position
        const isElseBranch = !!edge._isElseSibling;
        // Back-edge detection is only for rendering arc path (spatial)
        const isBackEdge = (tn.x + te.offsetWidth/2) < (sn.x + se.offsetWidth/2);
        // Selector edge detection
        const isSelectorEdge = sn.isSelector && !isCond;
        const choiceNum = selectorChoiceMap[edge.id];

        let pathD;
        if (isBackEdge) {
            const arcY = Math.max(sn.y + se.offsetHeight, tn.y + te.offsetHeight) + 60;
            const bx1 = sn.x + se.offsetWidth/2, by1 = sn.y + se.offsetHeight;
            const bx2 = tn.x + te.offsetWidth/2, by2 = tn.y + te.offsetHeight;
            pathD = `M${bx1},${by1} C${bx1},${arcY} ${bx2},${arcY} ${bx2},${by2}`;
        } else {
            const cpx = (x1+x2)/2;
            pathD = `M${x1},${y1} C${cpx},${y1} ${cpx},${y2} ${x2},${y2}`;
        }

        let strokeColor, markerEnd, dashArr;
        if (isSelectorEdge) {
            // Selector edge: purple
            strokeColor='#8b5cf6'; markerEnd='url(#orch-arrowhead-purple)'; dashArr=null;
        } else if (isCond && isElseBranch) {
            // Else branch: always orange dashed (regardless of position)
            strokeColor='#ea580c'; markerEnd='url(#orch-arrowhead-orange)'; dashArr='6,4';
        } else if (isCond) {
            // Then branch: always green solid
            strokeColor='#16a34a'; markerEnd='url(#orch-arrowhead-green)'; dashArr=null;
        } else {
            strokeColor='#2563eb'; markerEnd='url(#orch-arrowhead)'; dashArr=null;
        }

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', pathD);
        path.setAttribute('stroke', strokeColor);
        path.setAttribute('stroke-width', '2');
        path.setAttribute('fill', 'none');
        path.setAttribute('marker-end', markerEnd);
        if (dashArr) path.setAttribute('stroke-dasharray', dashArr);
        path.style.cursor = 'pointer';
        path.style.pointerEvents = 'all';
        const origColor = strokeColor;
        path.addEventListener('click', e => { e.stopPropagation(); orchDeleteEdge(edge); });
        path.addEventListener('contextmenu', e => { e.preventDefault(); e.stopPropagation(); orchShowEdgeContextMenu(e.clientX, e.clientY, edge); });
        path.addEventListener('mouseenter', () => { path.setAttribute('stroke','#ef4444'); path.setAttribute('stroke-width','3'); });
        path.addEventListener('mouseleave', () => { path.setAttribute('stroke', origColor); path.setAttribute('stroke-width','2'); });
        svg.appendChild(path);

        // Label for conditional edges (use data flag, not position)
        if (isCond && edge.condition) {
            const lbl = document.createElementNS('http://www.w3.org/2000/svg','text');
            const edgeLayout = orchGetTextLayout();
            let lx, ly;
            if (isBackEdge) {
                const bx1=sn.x+se.offsetWidth/2, bx2=tn.x+te.offsetWidth/2;
                const arcY=Math.max(sn.y+se.offsetHeight,tn.y+te.offsetHeight)+60;
                lx=(bx1+bx2)/2; ly=arcY+14;
            } else { lx=(x1+x2)/2; ly=(y1+y2)/2-8; }
            lbl.setAttribute('x',lx); lbl.setAttribute('y',ly);
            lbl.setAttribute('text-anchor','middle');
            lbl.classList.add('orch-edge-label');
            lbl.classList.add(isElseBranch?'orch-else-label':'orch-then-label');
            const conditionText = (isElseBranch ? '❌ ' : '✅ ') + edge.condition;
            lbl.textContent = edgeLayout && typeof edgeLayout.fitSingleLine === 'function'
                ? edgeLayout.fitSingleLine(conditionText, 176, { font: '600 10px Arial', lineHeight: 12, suffix: '…' }).text
                : ((isElseBranch ? '❌ ' : '✅ ') + (edge.condition.length > 25 ? edge.condition.slice(0,22)+'...' : edge.condition));
            svg.appendChild(lbl);
        }

        // Label for selector edges: show choice number
        if (isSelectorEdge && choiceNum) {
            const lbl = document.createElementNS('http://www.w3.org/2000/svg','text');
            const edgeLayout = orchGetTextLayout();
            let lx, ly;
            if (isBackEdge) {
                const bx1=sn.x+se.offsetWidth/2, bx2=tn.x+te.offsetWidth/2;
                const arcY=Math.max(sn.y+se.offsetHeight,tn.y+te.offsetHeight)+60;
                lx=(bx1+bx2)/2; ly=arcY+14;
            } else { lx=(x1+x2)/2; ly=(y1+y2)/2-8; }
            lbl.setAttribute('x',lx); lbl.setAttribute('y',ly);
            lbl.setAttribute('text-anchor','middle');
            lbl.classList.add('orch-edge-label');
            lbl.classList.add('orch-selector-label');
            const selectorText = '🎯 [' + choiceNum + '] → ' + tn.name;
            lbl.textContent = edgeLayout && typeof edgeLayout.fitSingleLine === 'function'
                ? edgeLayout.fitSingleLine(selectorText, 188, { font: '600 10px Arial', lineHeight: 12, suffix: '…' }).text
                : selectorText;
            svg.appendChild(lbl);
        }
    });
}

/** Edge right-click context menu */
function orchShowEdgeContextMenu(x, y, edge) {
    orchHideContextMenu();
    const menu = document.createElement('div');
    menu.className = 'orch-context-menu';
    menu.id = 'orch-ctx-menu';
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    const items = [];
    if (edge.edgeType === 'conditional') {
        items.push({label: t('orch_ctx_edit_cond'), action: () => orchShowCondEdgeModal(edge)});
        items.push({label: t('orch_ctx_remove_cond'), action: () => {
            edge.edgeType='fixed'; edge.condition=''; edge.thenTarget='';
            if(edge.elseTarget){ orch.edges=orch.edges.filter(e=>!(e._isElseSibling===edge.id)); edge.elseTarget=''; }
            orchRenderEdges(); orchUpdateNodeBadges(); orchUpdateYaml();
        }});
    } else {
        items.push({label: t('orch_ctx_set_cond'), action: () => orchShowCondEdgeModal(edge)});
    }
    items.push({divider:true});
    items.push({label: t('orch_ctx_delete'), action: () => { orchDeleteEdge(edge); }});
    items.forEach(item => {
        if(item.divider){const d=document.createElement('div');d.className='orch-menu-divider';menu.appendChild(d);return;}
        const d=document.createElement('div');d.className='orch-menu-item';d.textContent=item.label;
        d.addEventListener('click',()=>{item.action();orchHideContextMenu();});
        menu.appendChild(d);
    });
    document.body.appendChild(menu);
    document.addEventListener('click', orchHideContextMenu, {once:true});
}

/** Conditional edge edit modal */
function orchShowCondEdgeModal(edge) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-cond-edge-modal';
    const otherNodes = orch.nodes.filter(n=>n.id!==edge.source);
    const nodeOpts = otherNodes.map(n=>`<option value="${n.id}">${n.emoji||'🤖'} ${n.name} (${n.id})</option>`).join('');
    const noneOpt = `<option value="">${t('orch_cond_none')}</option>`;
    // Parse existing condition for edit mode
    let _parsedNeg = false, _parsedType = 'always', _parsedVal = '';
    if (edge.condition) {
        let _expr = edge.condition.trim();
        if (_expr.startsWith('!')) { _parsedNeg = true; _expr = _expr.slice(1).trim(); }
        if (_expr === 'always') { _parsedType = 'always'; }
        else if (_expr.startsWith('last_post_contains:')) { _parsedType = 'last_post_contains'; _parsedVal = _expr.split(':',2)[1]||''; }
        else if (_expr.startsWith('last_post_not_contains:')) { _parsedType = 'last_post_not_contains'; _parsedVal = _expr.split(':',2)[1]||''; }
        else if (_expr.startsWith('post_count_gte:')) { _parsedType = 'post_count_gte'; _parsedVal = _expr.split(':',2)[1]||''; }
        else if (_expr.startsWith('post_count_lt:')) { _parsedType = 'post_count_lt'; _parsedVal = _expr.split(':',2)[1]||''; }
        else { _parsedType = 'always'; _parsedVal = ''; }
    }
    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:400px;max-width:500px;">
            <h3>${t('orch_modal_cond_edge')}</h3>
            <div style="margin-bottom:10px;">
                <label style="display:block;font-size:12px;color:#6b7280;margin-bottom:3px;">${t('orch_cond_label_type')}</label>
                <select id="orch-cond-type" style="width:100%;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;">
                    <option value="last_post_contains">${t('orch_cond_opt_contains')}</option>
                    <option value="last_post_not_contains">${t('orch_cond_opt_not_contains')}</option>
                    <option value="post_count_gte">${t('orch_cond_opt_count_gte')}</option>
                    <option value="post_count_lt">${t('orch_cond_opt_count_lt')}</option>
                    <option value="always">${t('orch_cond_opt_always')}</option>
                </select>
            </div>
            <div id="orch-cond-val-row" style="margin-bottom:10px;">
                <label id="orch-cond-val-label" style="display:block;font-size:12px;color:#6b7280;margin-bottom:3px;">${t('orch_cond_label_keyword')}</label>
                <input type="text" id="orch-cond-val" value="" placeholder="" style="width:100%;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;">
            </div>
            <div style="margin-bottom:10px;">
                <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#6b7280;cursor:pointer;">
                    <input type="checkbox" id="orch-cond-negate"> ${t('orch_cond_label_negate')}
                </label>
            </div>
            <div style="margin-bottom:10px;">
                <label style="display:block;font-size:12px;color:#6b7280;margin-bottom:3px;">${t('orch_cond_label_then')}</label>
                <select id="orch-cond-then" style="width:100%;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;">${nodeOpts}</select>
            </div>
            <div style="margin-bottom:10px;">
                <label style="display:block;font-size:12px;color:#6b7280;margin-bottom:3px;">${t('orch_cond_label_else')}</label>
                <select id="orch-cond-else" style="width:100%;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;">${noneOpt}${nodeOpts}</select>
            </div>
            <div class="orch-modal-btns">
                <button id="orch-cond-cancel" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_modal_cancel')}</button>
                ${edge.edgeType==='conditional'?`<button id="orch-cond-revert" style="padding:6px 14px;border-radius:6px;border:1px solid #fca5a5;background:#fef2f2;color:#dc2626;cursor:pointer;font-size:12px;">${t('orch_ctx_remove_cond')}</button>`:''}
                <button id="orch-cond-save" style="padding:6px 14px;border-radius:6px;border:none;background:#2563eb;color:white;cursor:pointer;font-size:12px;">${t('orch_modal_save')}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    const thenSel=overlay.querySelector('#orch-cond-then');
    const elseSel=overlay.querySelector('#orch-cond-else');
    thenSel.value=edge.target; elseSel.value=edge.elseTarget||'';
    // Init condition type selector
    const typeSel=overlay.querySelector('#orch-cond-type');
    const valRow=overlay.querySelector('#orch-cond-val-row');
    const valInput=overlay.querySelector('#orch-cond-val');
    const valLabel=overlay.querySelector('#orch-cond-val-label');
    const negCheck=overlay.querySelector('#orch-cond-negate');
    typeSel.value=_parsedType; valInput.value=_parsedVal; negCheck.checked=_parsedNeg;
    function _updateCondUI(){
        const tp=typeSel.value;
        if(tp==='always'){ valRow.style.display='none'; }
        else { valRow.style.display='block'; }
        if(tp==='post_count_gte'||tp==='post_count_lt'){
            valLabel.textContent=t('orch_cond_label_number');
            valInput.type='number'; valInput.placeholder='3';
        } else {
            valLabel.textContent=t('orch_cond_label_keyword');
            valInput.type='text'; valInput.placeholder='LGTM';
        }
    }
    _updateCondUI();
    typeSel.addEventListener('change', _updateCondUI);
    overlay.querySelector('#orch-cond-cancel').addEventListener('click',()=>overlay.remove());
    overlay.addEventListener('click',e=>{if(e.target===overlay)overlay.remove();});
    const revertBtn=overlay.querySelector('#orch-cond-revert');
    if(revertBtn) revertBtn.addEventListener('click',()=>{
        edge.edgeType='fixed'; edge.condition=''; edge.thenTarget='';
        orch.edges=orch.edges.filter(e=>!(e._isElseSibling===edge.id)); edge.elseTarget='';
        overlay.remove(); orchRenderEdges(); orchUpdateNodeBadges(); orchUpdateYaml();
    });
    overlay.querySelector('#orch-cond-save').addEventListener('click',()=>{
        // Build condition string from UI controls
        const tp=typeSel.value;
        const val=valInput.value.trim();
        const neg=negCheck.checked;
        if(tp!=='always' && !val){ orchToast(t('orch_cond_val_required')); return; }
        let cond='';
        if(tp==='always'){ cond='always'; }
        else { cond=tp+':'+val; }
        if(neg){ cond='!'+cond; }
        const thenTgt=thenSel.value, elseTgt=elseSel.value;
        edge.edgeType='conditional'; edge.condition=cond; edge.target=thenTgt; edge.thenTarget=thenTgt;
        // Remove old else-sibling
        orch.edges=orch.edges.filter(e=>!e._isElseSibling||e._isElseSibling!==edge.id);
        if(elseTgt){
            edge.elseTarget=elseTgt;
            const eid='oe'+orch.eid++;
            orch.edges.push({id:eid,source:edge.source,target:elseTgt,edgeType:'conditional',condition:edge.condition,thenTarget:'',elseTarget:'',_isElseSibling:edge.id});
        } else { edge.elseTarget=''; }
        overlay.remove(); orchRenderEdges(); orchUpdateNodeBadges(); orchUpdateYaml();
    });
}

/** Update START/END badges on nodes */
function orchUpdateNodeBadges() {
    document.querySelectorAll('.orch-node-badge').forEach(b=>b.remove());
    if(orch.nodes.length===0) return;
    const realEdges=orch.edges.filter(e=>!e._isElseSibling);
    if(realEdges.length===0) return;
    const inDeg={}, outDeg={};
    orch.nodes.forEach(n=>{inDeg[n.id]=0;outDeg[n.id]=0;});
    realEdges.forEach(e=>{
        if(inDeg.hasOwnProperty(e.target)) inDeg[e.target]++;
        if(outDeg.hasOwnProperty(e.source)) outDeg[e.source]++;
    });
    orch.nodes.forEach(n=>{
        const el=document.getElementById('onode-'+n.id);
        if(!el) return;
        if(inDeg[n.id]===0){
            const b=document.createElement('div');b.className='orch-node-badge orch-start-badge';b.textContent='▶ START';el.appendChild(b);
        }
        if(outDeg[n.id]===0){
            const b=document.createElement('div');b.className='orch-node-badge orch-end-badge';b.textContent='■ END';el.appendChild(b);
        }
    });
}

/** Delete an edge and its else-sibling if applicable */
function orchDeleteEdge(edge) {
    if (edge._isElseSibling) {
        // Deleting an else-sibling: also clear the parent's elseTarget
        const parent = orch.edges.find(e => e.id === edge._isElseSibling);
        if (parent) parent.elseTarget = '';
        orch.edges = orch.edges.filter(e => e.id !== edge.id);
    } else {
        // Deleting a main edge: also remove its else-sibling
        orch.edges = orch.edges.filter(e => e.id !== edge.id && e._isElseSibling !== edge.id);
    }
    orchRenderEdges(); orchUpdateNodeBadges(); orchUpdateYaml();
}

function orchRemoveTempLine() { const svg=document.getElementById('orch-edge-svg'); const t=svg.querySelector('.temp-line'); if(t)t.remove(); }
function orchDrawTempLine(x1,y1,x2,y2) {
    const svg=document.getElementById('orch-edge-svg'); orchRemoveTempLine();
    const line=document.createElementNS('http://www.w3.org/2000/svg','line');
    line.classList.add('temp-line');
    line.setAttribute('x1',x1); line.setAttribute('y1',y1); line.setAttribute('x2',x2); line.setAttribute('y2',y2);
    line.setAttribute('stroke','#2563eb80'); line.setAttribute('stroke-width','2'); line.setAttribute('stroke-dasharray','5,5');
    line.style.pointerEvents = 'none';
    svg.appendChild(line);
}

// ── Group Management ──
function orchCreateGroup(type) {
    if (orch.selectedNodes.size < 2 && type !== 'all') { orchToast(t('orch_toast_select_2')); return; }
    const members = [...orch.selectedNodes];
    const nodes = members.map(id => orch.nodes.find(n=>n.id===id)).filter(Boolean);
    const pad = 30;
    const x = Math.min(...nodes.map(n=>n.x)) - pad;
    const y = Math.min(...nodes.map(n=>n.y)) - pad;
    const w = Math.max(...nodes.map(n=>n.x+120)) - x + pad;
    const h = Math.max(...nodes.map(n=>n.y+50)) - y + pad;
    const id = 'og' + orch.gid++;
    const labelMap = {parallel: t('orch_group_parallel'), all: t('orch_group_all')};
    const group = { id, name: labelMap[type]||type, type, x, y, w, h, nodeIds: members };
    orch.groups.push(group);
    orchRenderGroup(group);
    orchUpdateYaml();
}

function orchRenderGroup(group) {
    const area = document.getElementById('orch-canvas-inner');
    const el = document.createElement('div');
    el.className = 'orch-group ' + group.type;
    el.id = 'ogroup-' + group.id;
    el.style.cssText = `left:${group.x}px;top:${group.y}px;width:${group.w}px;height:${group.h}px;`;
    el.innerHTML = `<span class="orch-group-label">${group.name}</span><div class="orch-group-del" title="${t('orch_group_dissolve')}">×</div>`;
    el.querySelector('.orch-group-del').addEventListener('click', e => {
        e.stopPropagation();
        orch.groups = orch.groups.filter(g=>g.id!==group.id);
        el.remove();
        orchUpdateYaml();
    });
    area.appendChild(el);
}

function orchUpdateGroupBounds(group) {
    const members = orch.nodes.filter(n => group.nodeIds.includes(n.id));
    if (!members.length) return;
    const pad = 30;
    group.x = Math.min(...members.map(n=>n.x)) - pad;
    group.y = Math.min(...members.map(n=>n.y)) - pad;
    group.w = Math.max(...members.map(n=>n.x+120)) - group.x + pad;
    group.h = Math.max(...members.map(n=>n.y+50)) - group.y + pad;
    const el = document.getElementById('ogroup-' + group.id);
    if (el) { el.style.left=group.x+'px'; el.style.top=group.y+'px'; el.style.width=group.w+'px'; el.style.height=group.h+'px'; }
}

// ── Canvas Events ──
function orchSetupCanvas() {
    const canvas = document.getElementById('orch-canvas-area');

    // ── Drag-and-drop from sidebar ──
    canvas.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
    canvas.addEventListener('drop', e => {
        e.preventDefault();
        try {
            const data = JSON.parse(e.dataTransfer.getData('application/json'));
            // Conditional card drop: if dropped on an existing agent node, make it a selector
            if (data._condDrop) {
                const hitNode = orchFindNodeAtPoint(e.clientX, e.clientY);
                if (orchCanBeSelector(hitNode)) {
                    // Toggle existing node to selector
                    if (!hitNode.isSelector) {
                        orchSetNodeSelector(hitNode);
                    } else {
                        orchToast('ℹ️ ' + hitNode.name + ' ' + t('orch_cond_already_selector'));
                    }
                } else {
                    // Drop on blank area: create a standalone selector expert node
                    const cp = orchClientToCanvas(e.clientX, e.clientY);
                    const cleanData = {...data};
                    delete cleanData._condDrop;
                    // Create as expert type with selector flag
                    cleanData.type = 'expert';
                    cleanData.tag = 'selector';
                    const node = orchAddNode(cleanData, cp.x - 55, cp.y - 20);
                    orchSetNodeSelector(node);
                }
                return;
            }
            // Expert dropped on an internal agent node → set its tag
            if (data.type === 'expert' || (!data.type && data.tag)) {
                const hitNode = orchFindNodeAtPoint(e.clientX, e.clientY);
                if (hitNode && hitNode.type === 'session_agent' && data.tag && !['manual', 'conditional', 'script', 'human'].includes(data.tag)) {
                    const oldTag = hitNode.tag;
                    hitNode.tag = data.tag;
                    // Also update the backend internal agent JSON
                    if (hitNode.session_id) {
                        fetch('/internal_agents/' + encodeURIComponent(hitNode.session_id), {
                            method: 'PUT', headers: {'Content-Type':'application/json'},
                            body: JSON.stringify({ meta: { tag: data.tag } })
                        }).catch(() => {});
                    }
                    // Re-render node to reflect new tag
                    const el = document.getElementById('onode-' + hitNode.id);
                    if (el) el.remove();
                    orchRenderNode(hitNode);
                    orchRenderEdges();
                    orchUpdateYaml();
                    orchToast('🏷️ ' + t('orch_ia_tag_set') + ' ' + data.tag + ' → ' + ((typeof currentLang !== 'undefined' && currentLang === 'zh-CN') ? (hitNode.name_zh || hitNode.name) : (hitNode.name_en || hitNode.name)));
                    return;
                }
            }
            const cp = orchClientToCanvas(e.clientX, e.clientY);
            orchAddNode(data, cp.x - 55, cp.y - 20);
        } catch(err) {}
    });

    // ── Wheel: zoom towards cursor ──
    canvas.addEventListener('wheel', e => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const oldZoom = orch.zoom;
        const delta = e.deltaY > 0 ? -0.08 : 0.08;
        orch.zoom = Math.min(3, Math.max(0.15, oldZoom + delta));
        // 以鼠标位置为中心缩放：调整 panX/panY 使鼠标下方的画布点不变
        orch.panX = mx - (mx - orch.panX) * (orch.zoom / oldZoom);
        orch.panY = my - (my - orch.panY) * (orch.zoom / oldZoom);
        orchApplyTransform();
    }, { passive: false });

    // ── Mousedown: left on blank = pan, Shift+left on blank = select rect ──
    canvas.addEventListener('mousedown', e => {
        const inner = document.getElementById('orch-canvas-inner');
        const isBlank = e.target === canvas || e.target === inner || e.target.id === 'orch-canvas-hint';

        // 中键 → 平移
        if (e.button === 1) {
            e.preventDefault();
            orch.panning = { startX: e.clientX, startY: e.clientY, origPanX: orch.panX, origPanY: orch.panY };
            canvas.style.cursor = 'grabbing';
            return;
        }

        if (isBlank && e.button === 0) {
            // Shift+左键空白区 → 框选
            if (e.shiftKey) {
                orchClearSelection();
                const cp = orchClientToCanvas(e.clientX, e.clientY);
                orch.selecting = { sx: cp.x, sy: cp.y };
            } else {
                // 左键空白区 → 抓住画布平移
                orchClearSelection();
                orch.panning = { startX: e.clientX, startY: e.clientY, origPanX: orch.panX, origPanY: orch.panY };
                canvas.style.cursor = 'grabbing';
            }
        }
    });

    // ── Mousemove: pan / drag nodes / connect / select ──
    canvas.addEventListener('mousemove', e => {
        // 画布平移优先
        if (orch.panning) {
            const p = orch.panning;
            orch.panX = p.origPanX + (e.clientX - p.startX);
            orch.panY = p.origPanY + (e.clientY - p.startY);
            orchApplyTransform();
            return;
        }
        if (orch.dragging) {
            const d = orch.dragging;
            const cp = orchClientToCanvas(e.clientX, e.clientY);
            if (d.multi) {
                const dx = cp.x - d.offX - d.starts[d.nodeId].x;
                const dy = cp.y - d.offY - d.starts[d.nodeId].y;
                orch.selectedNodes.forEach(nid => {
                    const n = orch.nodes.find(nn=>nn.id===nid);
                    if (n && d.starts[nid]) { n.x = d.starts[nid].x + dx; n.y = d.starts[nid].y + dy; const el=document.getElementById('onode-'+nid); if(el){el.style.left=n.x+'px';el.style.top=n.y+'px';} }
                });
            } else {
                const n = orch.nodes.find(nn=>nn.id===d.nodeId);
                if (n) { n.x = cp.x - d.offX; n.y = cp.y - d.offY; const el=document.getElementById('onode-'+n.id); if(el){el.style.left=n.x+'px';el.style.top=n.y+'px';} }
            }
            orchRenderEdges();
            orch.groups.forEach(g => orchUpdateGroupBounds(g));
        } else if (orch.connecting) {
            const cp = orchClientToCanvas(e.clientX, e.clientY);
            orchDrawTempLine(orch.connecting.sx, orch.connecting.sy, cp.x, cp.y);
        } else if (orch.selecting) {
            const s = orch.selecting;
            const cp = orchClientToCanvas(e.clientX, e.clientY);
            let existing = document.querySelector('.orch-sel-rect');
            const inner = document.getElementById('orch-canvas-inner');
            if (!existing) { existing = document.createElement('div'); existing.className='orch-sel-rect'; inner.appendChild(existing); }
            const x = Math.min(s.sx, cp.x), y = Math.min(s.sy, cp.y);
            const w = Math.abs(cp.x - s.sx), h = Math.abs(cp.y - s.sy);
            existing.style.cssText = `left:${x}px;top:${y}px;width:${w}px;height:${h}px;`;
        }
    });

    // ── Mouseup ──
    canvas.addEventListener('mouseup', e => {
        if (orch.panning) {
            orch.panning = null;
            canvas.style.cursor = '';
            return;
        }
        if (orch.dragging) { orch.dragging = null; orchUpdateYaml(); }
        if (orch.connecting) { orch.connecting = null; orchRemoveTempLine(); }
        if (orch.selecting) {
            const s = orch.selecting;
            const cp = orchClientToCanvas(e.clientX, e.clientY);
            const x1 = Math.min(s.sx, cp.x), y1 = Math.min(s.sy, cp.y);
            const x2 = Math.max(s.sx, cp.x), y2 = Math.max(s.sy, cp.y);
            if (Math.abs(x2-x1) > 10 && Math.abs(y2-y1) > 10) {
                orch.nodes.forEach(n => { if (n.x > x1 && n.x < x2 && n.y > y1 && n.y < y2) orchSelectNode(n.id); });
            }
            orch.selecting = null;
            document.querySelectorAll('.orch-sel-rect').forEach(el => el.remove());
        }
    });

    // ── Context menu ──
    canvas.addEventListener('contextmenu', e => {
        e.preventDefault();
        orchShowContextMenu(e.clientX, e.clientY);
    });

    // ── Keyboard shortcuts ──
    document.addEventListener('keydown', e => {
        if (currentPage !== 'orchestrate') return;
        // 空格键：进入画布拖拽模式
        if (e.key === ' ' && !e.repeat) {
            if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') return;
            e.preventDefault();
            orch.spaceDown = true;
            canvas.style.cursor = 'grab';
        }
        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') return;
            orch.selectedNodes.forEach(id => orchRemoveNode(id));
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'g') {
            e.preventDefault();
            if (orch.selectedNodes.size >= 2) orchCreateGroup('parallel');
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'a' && currentPage === 'orchestrate') {
            e.preventDefault();
            orch.nodes.forEach(n => orchSelectNode(n.id));
        }
        if (e.key === 'Escape') { orchClearSelection(); orchHideContextMenu(); }
    });
    document.addEventListener('keyup', e => {
        if (e.key === ' ') {
            orch.spaceDown = false;
            if (!orch.panning) canvas.style.cursor = '';
        }
    });

    // ── Touch events (mobile) ──
    let touchState = null; // { mode:'pan'|'zoom'|'node'|'port', ... }

    canvas.addEventListener('touchstart', e => {
        if (e.touches.length === 2) {
            // 双指 → 缩放
            e.preventDefault();
            const t0 = e.touches[0], t1 = e.touches[1];
            const dist = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
            const mx = (t0.clientX + t1.clientX) / 2;
            const my = (t0.clientY + t1.clientY) / 2;
            touchState = { mode: 'zoom', initDist: dist, initZoom: orch.zoom, mx, my, initPanX: orch.panX, initPanY: orch.panY };
            // 取消进行中的单指操作
            orch.dragging = null; orch.panning = null;
            return;
        }
        if (e.touches.length === 1) {
            const t = e.touches[0];
            const target = document.elementFromPoint(t.clientX, t.clientY);
            if (!target) return;

            // 端口触摸 → 连线
            if (target.classList.contains('orch-port') && target.dataset.dir === 'out') {
                e.preventDefault();
                const nodeId = target.dataset.node;
                const portRect = target.getBoundingClientRect();
                const cp = orchClientToCanvas(portRect.left + 5, portRect.top + 5);
                orch.connecting = { sourceId: nodeId, sx: cp.x, sy: cp.y };
                touchState = { mode: 'port' };
                return;
            }

            // 节点触摸 → 拖拽节点
            const nodeEl = target.closest('.orch-node');
            if (nodeEl && !target.classList.contains('orch-node-del')) {
                e.preventDefault();
                const nodeId = nodeEl.id.replace('onode-', '');
                const node = orch.nodes.find(n => n.id === nodeId);
                if (!node) return;
                if (!orch.selectedNodes.has(nodeId)) orchClearSelection();
                orchSelectNode(nodeId);
                const cp = orchClientToCanvas(t.clientX, t.clientY);
                orch.dragging = { nodeId, offX: cp.x - node.x, offY: cp.y - node.y, multi: orch.selectedNodes.size > 1, starts: {} };
                if (orch.selectedNodes.size > 1) {
                    orch.selectedNodes.forEach(nid => { const n = orch.nodes.find(nn=>nn.id===nid); if(n) orch.dragging.starts[nid]={x:n.x,y:n.y}; });
                }
                touchState = { mode: 'node' };
                return;
            }

            // 空白区触摸 → 画布平移
            const inner = document.getElementById('orch-canvas-inner');
            if (target === canvas || target === inner || target.id === 'orch-canvas-hint' || target.closest('.orch-canvas-inner')) {
                e.preventDefault();
                orch.panning = { startX: t.clientX, startY: t.clientY, origPanX: orch.panX, origPanY: orch.panY };
                touchState = { mode: 'pan' };
            }
        }
    }, { passive: false });

    canvas.addEventListener('touchmove', e => {
        if (!touchState) return;
        e.preventDefault();

        if (touchState.mode === 'zoom' && e.touches.length >= 2) {
            const t0 = e.touches[0], t1 = e.touches[1];
            const dist = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
            const scale = dist / touchState.initDist;
            const newZoom = Math.min(3, Math.max(0.15, touchState.initZoom * scale));
            // 以初始双指中心为基准缩放
            const rect = canvas.getBoundingClientRect();
            const mx = touchState.mx - rect.left;
            const my = touchState.my - rect.top;
            orch.zoom = newZoom;
            orch.panX = mx - (mx - touchState.initPanX) * (newZoom / touchState.initZoom);
            orch.panY = my - (my - touchState.initPanY) * (newZoom / touchState.initZoom);
            orchApplyTransform();
            return;
        }

        const t = e.touches[0];
        if (touchState.mode === 'pan' && orch.panning) {
            const p = orch.panning;
            orch.panX = p.origPanX + (t.clientX - p.startX);
            orch.panY = p.origPanY + (t.clientY - p.startY);
            orchApplyTransform();
        } else if (touchState.mode === 'node' && orch.dragging) {
            const d = orch.dragging;
            const cp = orchClientToCanvas(t.clientX, t.clientY);
            if (d.multi) {
                const dx = cp.x - d.offX - d.starts[d.nodeId].x;
                const dy = cp.y - d.offY - d.starts[d.nodeId].y;
                orch.selectedNodes.forEach(nid => {
                    const n = orch.nodes.find(nn=>nn.id===nid);
                    if (n && d.starts[nid]) { n.x = d.starts[nid].x + dx; n.y = d.starts[nid].y + dy; const el=document.getElementById('onode-'+nid); if(el){el.style.left=n.x+'px';el.style.top=n.y+'px';} }
                });
            } else {
                const n = orch.nodes.find(nn=>nn.id===d.nodeId);
                if (n) { n.x = cp.x - d.offX; n.y = cp.y - d.offY; const el=document.getElementById('onode-'+n.id); if(el){el.style.left=n.x+'px';el.style.top=n.y+'px';} }
            }
            orchRenderEdges();
            orch.groups.forEach(g => orchUpdateGroupBounds(g));
        } else if (touchState.mode === 'port' && orch.connecting) {
            const cp = orchClientToCanvas(t.clientX, t.clientY);
            orchDrawTempLine(orch.connecting.sx, orch.connecting.sy, cp.x, cp.y);
        }
    }, { passive: false });

    canvas.addEventListener('touchend', e => {
        if (!touchState) return;
        // 端口连线：检查手指松开处是否在目标端口上
        if (touchState.mode === 'port' && orch.connecting) {
            const lastTouch = e.changedTouches[0];
            const target = document.elementFromPoint(lastTouch.clientX, lastTouch.clientY);
            if (target && target.classList.contains('orch-port') && target.dataset.dir === 'in') {
                const targetNodeId = target.dataset.node;
                if (targetNodeId !== orch.connecting.sourceId) {
                    orchAddEdge(orch.connecting.sourceId, targetNodeId);
                }
            }
            orch.connecting = null;
            orchRemoveTempLine();
        }
        if (touchState.mode === 'node' && orch.dragging) {
            orch.dragging = null;
            orchUpdateYaml();
        }
        if (touchState.mode === 'pan') {
            orch.panning = null;
        }
        // 双指缩放结束时可能还有一根手指，忽略
        if (e.touches.length === 0) {
            touchState = null;
        }
    }, { passive: false });

    canvas.addEventListener('touchcancel', () => {
        orch.dragging = null; orch.panning = null; orch.connecting = null;
        orchRemoveTempLine(); touchState = null;
    });
}

function orchShowContextMenu(x, y, targetNode) {
    orchHideContextMenu();
    const menu = document.createElement('div');
    menu.className = 'orch-context-menu';
    menu.id = 'orch-ctx-menu';
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';

    const hasSelection = orch.selectedNodes.size > 0;
    const items = [];

    // ── Node-specific: duplicate / set instance / selector ──
    if (targetNode) {
        items.push({label: t('orch_ctx_duplicate'), action: () => {
            orchAddNode({...targetNode, instance: targetNode.instance}, targetNode.x + 40, targetNode.y + 40);
        }});
        items.push({label: t('orch_ctx_new_instance'), action: () => {
            orchAddNode({...targetNode, instance: undefined}, targetNode.x + 40, targetNode.y + 40);
        }});
        // Selector node toggle
        if (orchCanBeSelector(targetNode)) {
            if (targetNode.isSelector) {
                items.push({label: t('orch_ctx_unset_selector'), action: () => {
                    targetNode.isSelector = false;
                    const el = document.getElementById('onode-' + targetNode.id);
                    if (el) { el.classList.remove('selector-type'); el.querySelector('.orch-selector-badge')?.remove(); }
                    orchRenderEdges(); orchUpdateYaml();
                }});
            } else {
                items.push({label: t('orch_ctx_set_selector'), action: () => {
                    targetNode.isSelector = true;
                    const el = document.getElementById('onode-' + targetNode.id);
                    if (el) {
                        el.classList.add('selector-type');
                        if (!el.querySelector('.orch-selector-badge')) {
                            const badge = document.createElement('div');
                            badge.className = 'orch-selector-badge';
                            badge.textContent = '🎯 SELECTOR';
                            el.appendChild(badge);
                        }
                    }
                    orchRenderEdges(); orchUpdateYaml();
                }});
            }
        }
        items.push({divider: true});
    }

    if (hasSelection && orch.selectedNodes.size >= 2) {
        items.push({label: t('orch_ctx_group_parallel'), action: () => orchCreateGroup('parallel')});
        items.push({label: t('orch_ctx_group_all'), action: () => orchCreateGroup('all')});
        items.push({divider: true});
    }
    if (hasSelection) {
        items.push({label: t('orch_ctx_delete'), action: () => { orch.selectedNodes.forEach(id => orchRemoveNode(id)); }});
    }
    items.push({label: t('orch_ctx_refresh_yaml'), action: () => orchUpdateYaml()});
    items.push({label: t('orch_ctx_clear'), action: () => orchClearCanvas()});

    items.forEach(item => {
        if (item.divider) { const d = document.createElement('div'); d.className='orch-menu-divider'; menu.appendChild(d); return; }
        const d = document.createElement('div');
        d.className = 'orch-menu-item';
        d.textContent = item.label;
        d.addEventListener('click', () => { item.action(); orchHideContextMenu(); });
        menu.appendChild(d);
    });

    document.body.appendChild(menu);
    document.addEventListener('click', orchHideContextMenu, {once: true});
}
function orchHideContextMenu() { const m = document.getElementById('orch-ctx-menu'); if(m) m.remove(); }

// ── Manual Edit Modal ──
function orchShowManualModal(node) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-manual-modal';
    overlay.innerHTML = `<div class="orch-modal">
        <h3>${t('orch_modal_edit_manual')}</h3>
        <input type="text" id="orch-man-author" value="${node.author||t('orch_default_author')}" placeholder="${t('orch_modal_author_ph')}">
        <textarea id="orch-man-content" placeholder="${t('orch_modal_content_ph')}">${node.content||''}</textarea>
        <div class="orch-modal-btns">
            <button onclick="document.getElementById('orch-manual-modal').remove()">${t('orch_modal_cancel')}</button>
            <button class="primary" onclick="orchSaveManual('${node.id}')">${t('orch_modal_save')}</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}
function orchSaveManual(nodeId) {
    const node = orch.nodes.find(n=>n.id===nodeId);
    if (node) {
        node.author = document.getElementById('orch-man-author').value;
        node.content = document.getElementById('orch-man-content').value;
    }
    document.getElementById('orch-manual-modal')?.remove();
    orchUpdateYaml();
}

function orchShowScriptModal(node) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-script-modal';
    overlay.innerHTML = `<div class="orch-modal" style="max-width:560px;">
        <h3>🧪 ${escapeHtml(node.name)} — Script</h3>
        <p style="font-size:11px;color:#6b7280;margin-bottom:8px;">Fill either a generic command or platform-specific commands. Unix uses bash, Windows uses PowerShell/CMD according to the engine.</p>
        <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;display:block;">Command</label>
        <textarea id="orch-script-command" placeholder="npm test" style="min-height:56px;">${escapeHtml(node.script_command || '')}</textarea>
        <label style="font-size:11px;color:#9ca3af;margin:8px 0 2px;display:block;">Unix command</label>
        <textarea id="orch-script-unix-command" placeholder="pytest -q" style="min-height:56px;">${escapeHtml(node.script_unix_command || '')}</textarea>
        <label style="font-size:11px;color:#9ca3af;margin:8px 0 2px;display:block;">Windows command</label>
        <textarea id="orch-script-windows-command" placeholder="powershell -Command &quot;pytest -q&quot;" style="min-height:56px;">${escapeHtml(node.script_windows_command || '')}</textarea>
        <div style="display:grid;grid-template-columns:1fr 120px;gap:8px;margin-top:8px;">
            <div>
                <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;display:block;">Working directory</label>
                <input type="text" id="orch-script-cwd" value="${escapeHtml(node.script_cwd || '')}" placeholder="src/ or ./tests">
            </div>
            <div>
                <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;display:block;">Timeout (s)</label>
                <input type="number" id="orch-script-timeout" min="0" step="1" value="${escapeHtml(node.script_timeout === '' || node.script_timeout === null || node.script_timeout === undefined ? '' : String(node.script_timeout))}" placeholder="300">
            </div>
        </div>
        <div class="orch-modal-btns">
            <button onclick="document.getElementById('orch-script-modal').remove()">${t('orch_modal_cancel')}</button>
            <button class="primary" onclick="orchSaveScript('${node.id}')">${t('orch_modal_save')}</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

function orchSaveScript(nodeId) {
    const node = orch.nodes.find(n => n.id === nodeId);
    if (node) {
        const timeoutRaw = document.getElementById('orch-script-timeout').value.trim();
        node.script_command = document.getElementById('orch-script-command').value.trim();
        node.script_unix_command = document.getElementById('orch-script-unix-command').value.trim();
        node.script_windows_command = document.getElementById('orch-script-windows-command').value.trim();
        node.script_cwd = document.getElementById('orch-script-cwd').value.trim();
        node.script_timeout = timeoutRaw === '' ? '' : Number(timeoutRaw);
        orchRerenderNode(nodeId);
    }
    document.getElementById('orch-script-modal')?.remove();
    orchUpdateYaml();
}

function orchShowHumanModal(node) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-human-modal';
    overlay.innerHTML = `<div class="orch-modal" style="max-width:520px;">
        <h3>🙋 ${escapeHtml(node.name)} — Human</h3>
        <p style="font-size:11px;color:#6b7280;margin-bottom:8px;">This node pauses the workflow and waits for a plain-text human reply.</p>
        <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;display:block;">Author</label>
        <input type="text" id="orch-human-author" value="${escapeHtml(node.human_author || t('orch_default_author'))}" placeholder="${escapeHtml(t('orch_default_author'))}">
        <label style="font-size:11px;color:#9ca3af;margin:8px 0 2px;display:block;">Prompt</label>
        <textarea id="orch-human-prompt" placeholder="请补充一句人类要回复的话..." style="min-height:100px;">${escapeHtml(node.human_prompt || '')}</textarea>
        <label style="font-size:11px;color:#9ca3af;margin:8px 0 2px;display:block;">Reply to post id (optional)</label>
        <input type="number" id="orch-human-reply-to" min="1" step="1" value="${escapeHtml(node.human_reply_to === '' || node.human_reply_to === null || node.human_reply_to === undefined ? '' : String(node.human_reply_to))}" placeholder="123">
        <div class="orch-modal-btns">
            <button onclick="document.getElementById('orch-human-modal').remove()">${t('orch_modal_cancel')}</button>
            <button class="primary" onclick="orchSaveHuman('${node.id}')">${t('orch_modal_save')}</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

function orchSaveHuman(nodeId) {
    const node = orch.nodes.find(n => n.id === nodeId);
    if (node) {
        const replyToRaw = document.getElementById('orch-human-reply-to').value.trim();
        node.human_author = document.getElementById('orch-human-author').value.trim() || t('orch_default_author');
        node.human_prompt = document.getElementById('orch-human-prompt').value.trim();
        node.human_reply_to = replyToRaw === '' ? '' : Number(replyToRaw);
        orchRerenderNode(nodeId);
    }
    document.getElementById('orch-human-modal')?.remove();
    orchUpdateYaml();
}

// ── Instruction Edit Modal (for expert/session nodes) ──
function orchShowInstructionModal(node) {
    const isExternalType = node.type === 'external';
    const isTempType = node.type === 'expert';
    // session_agent is always stateful, temp/external never need it — hide switch for all
    const showStateful = false;
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-instruction-modal';
    overlay.innerHTML = `<div class="orch-modal">
        <h3>📋 ${escapeHtml(node.name)} — Instruction</h3>
        <p style="font-size:11px;color:#6b7280;margin-bottom:8px;">Set a specific instruction for this expert in this step. The expert will focus on this instruction when participating.</p>
        <textarea id="orch-instr-content" placeholder="e.g. Please focus on analyzing technical risks..." style="min-height:80px;">${escapeHtml(node.content||'')}</textarea>
        ${showStateful ? `<label style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:11px;color:#374151;cursor:pointer;"><input type="checkbox" id="orch-instr-stateful" ${node.stateful ? 'checked' : ''} style="accent-color:#8b5cf6;"> <span>⚡ ${t('orch_node_stateful')}</span></label><p style="font-size:10px;color:#9ca3af;margin:2px 0 0 22px;">${t('orch_node_stateful_hint')}</p>` : ''}
        <div class="orch-modal-btns">
            <button onclick="document.getElementById('orch-instruction-modal').remove()">${t('orch_modal_cancel')}</button>
            <button class="primary" onclick="orchSaveInstruction('${node.id}')">${t('orch_modal_save')}</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    setTimeout(() => { const ta = document.getElementById('orch-instr-content'); if (ta) ta.focus(); }, 100);
}
function orchSaveInstruction(nodeId) {
    const node = orch.nodes.find(n=>n.id===nodeId);
    if (node) {
        node.content = document.getElementById('orch-instr-content').value;
        const sfCb = document.getElementById('orch-instr-stateful');
        if (sfCb) node.stateful = sfCb.checked;
        // Re-render node to update instruction preview
        const el = document.getElementById('onode-' + nodeId);
        if (el) el.remove();
        orchRenderNode(node);
        orchRenderEdges();
    }
    document.getElementById('orch-instruction-modal')?.remove();
    orchUpdateYaml();
}

// ── External Agent Edit Modal ──
function orchShowExternalModal(node) {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-external-modal';
    const hdrs = (node.headers && typeof node.headers === 'object') ? JSON.stringify(node.headers, null, 2) : '';
    overlay.innerHTML = `<div class="orch-modal" style="max-width:480px;">
        <h3>🌐 ${escapeHtml((typeof currentLang !== 'undefined' && currentLang === 'zh-CN') ? (node.name_zh || node.name) : (node.name_en || node.name))} — External Agent</h3>
        <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;display:block;">API URL *</label>
        <input type="text" id="orch-ext-url" value="${escapeHtml(node.api_url||'')}" placeholder="https://api.example.com/v1" style="font-family:monospace;font-size:12px;">
        <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;margin-top:8px;display:block;">API Key</label>
        <input type="text" id="orch-ext-key" value="${escapeHtml(node.api_key||'')}" placeholder="sk-xxx (optional)" style="font-family:monospace;font-size:12px;">
        <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;margin-top:8px;display:block;">Model</label>
        <input type="text" id="orch-ext-model" value="${escapeHtml(node.model||'')}" placeholder="gpt-4 / deepseek-chat (optional)" style="font-family:monospace;font-size:12px;">
        <label style="font-size:11px;color:#9ca3af;margin-bottom:2px;margin-top:8px;display:block;">Headers (JSON)</label>
        <textarea id="orch-ext-headers" placeholder='{"X-Custom": "value"}' style="font-family:monospace;font-size:11px;min-height:60px;">${escapeHtml(hdrs)}</textarea>
        <div class="orch-modal-btns">
            <button onclick="document.getElementById('orch-external-modal').remove()">${t('orch_modal_cancel')}</button>
            <button class="primary" onclick="orchSaveExternal('${node.id}')">${t('orch_modal_save')}</button>
        </div>
    </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}
function orchSaveExternal(nodeId) {
    const node = orch.nodes.find(n=>n.id===nodeId);
    if (node) {
        node.api_url = document.getElementById('orch-ext-url').value.trim();
        node.api_key = document.getElementById('orch-ext-key').value.trim();
        node.model = document.getElementById('orch-ext-model').value.trim();
        const hdrsStr = document.getElementById('orch-ext-headers').value.trim();
        if (hdrsStr) {
            try { node.headers = JSON.parse(hdrsStr); } catch(e) { alert('Headers JSON parse error: ' + e.message); return; }
        } else {
            node.headers = {};
        }
        // Re-render node to update display
        const el = document.getElementById('onode-' + nodeId);
        if (el) el.remove();
        orchRenderNode(node);
        orchRenderEdges();
    }
    document.getElementById('orch-external-modal')?.remove();
    orchUpdateYaml();
}

// ── Layout Data ──
function orchGetLayoutData() {
    const fixedEdges = [];
    const conditionalEdges = [];
    const selectorEdges = [];
    orch.edges.forEach(e => {
        if (e._isElseSibling) return;
        if (e.edgeType === 'conditional' && e.condition) {
            conditionalEdges.push({ source: e.source, condition: e.condition, then: e.target, else: e.elseTarget || '' });
        } else {
            fixedEdges.push({ id: e.id, source: e.source, target: e.target });
        }
    });
    // Build selector edges from selector nodes
    orch.nodes.forEach(n => {
        if (!n.isSelector) return;
        const outEdges = orch.edges.filter(e => e.source === n.id && !e._isElseSibling && e.edgeType !== 'conditional');
        if (outEdges.length === 0) return;
        const choices = {};
        outEdges.forEach((e, idx) => { choices[idx + 1] = e.target; });
        selectorEdges.push({ source: n.id, choices: choices });
    });
    return {
        nodes: orch.nodes.map(n => ({...n})),
        edges: fixedEdges,
        conditionalEdges: conditionalEdges,
        selectorEdges: selectorEdges,
        groups: orch.groups.map(g => ({...g})),
        settings: orchGetSettings(),
        view: { zoom: orch.zoom, panX: orch.panX, panY: orch.panY },
        hasConditional: conditionalEdges.length > 0,
        hasSelector: selectorEdges.length > 0,
    };
}

// ── YAML Generation (Rule-based) ──
async function orchUpdateYaml() {
    orchUpdateStatus();
    const data = orchGetLayoutData();
    if (orch.nodes.length === 0) {
        document.getElementById('orch-yaml-content').textContent = t('orch_rule_yaml_hint');
        return;
    }
    try {
        const r = await fetch('/proxy_visual/generate-yaml', {
            method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data),
        });
        const res = await r.json();
        document.getElementById('orch-yaml-content').textContent = res.yaml || '# Error: ' + (res.error || 'Unknown');
    } catch(e) {
        document.getElementById('orch-yaml-content').textContent = '# Error: ' + e.message;
    }
}

// ── AI Generate YAML (with session selection) ──
let orchTargetSessionId = null;

async function orchGenerateAgentYaml() {
    if (orch.nodes.length === 0) { orchToast(t('orch_toast_add_first')); return; }
    orchShowSessionSelectModal();
}

async function orchShowSessionSelectModal() {
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-session-select-overlay';

    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:400px;max-width:500px;">
            <h3>${t('orch_modal_select_session')}</h3>
            <p style="font-size:12px;color:#6b7280;margin-bottom:10px;">${t('orch_modal_select_desc')}</p>
            <div class="orch-session-list" id="orch-session-select-list">
                <div style="text-align:center;padding:20px;color:#9ca3af;font-size:12px;">${t('orch_modal_loading')}</div>
            </div>
            <div class="orch-modal-btns">
                <button id="orch-session-cancel-btn" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_modal_cancel')}</button>
                <button id="orch-session-confirm-btn" disabled style="padding:6px 14px;border-radius:6px;border:none;background:#2563eb;color:white;cursor:pointer;font-size:12px;opacity:0.5;">${t('orch_modal_confirm_gen')}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    let selectedSid = null;

    overlay.querySelector('#orch-session-cancel-btn').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    const listEl = overlay.querySelector('#orch-session-select-list');
    try {
        // Load sessions and agent meta in parallel
        const [resp, agentMap] = await Promise.all([fetch('/proxy_sessions'), _orchLoadAgentMetaMap()]);
        const data = await resp.json();
        listEl.innerHTML = '';

        const newSessionId = Date.now().toString(36) + Math.random().toString(36).substr(2, 4);
        const newItem = document.createElement('div');
        newItem.className = 'orch-session-new';
        newItem.innerHTML = `<span style="font-size:18px;">🆕</span><div style="flex:1;"><div style="font-size:13px;font-weight:500;color:#2563eb;">${t('orch_modal_new_session')}</div><div style="font-size:10px;color:#9ca3af;font-family:monospace;">#${newSessionId.slice(-6)}</div></div>`;
        newItem.addEventListener('click', () => {
            listEl.querySelectorAll('.orch-session-item,.orch-session-new').forEach(el => el.classList.remove('selected'));
            newItem.classList.add('selected');
            selectedSid = newSessionId;
            const btn = overlay.querySelector('#orch-session-confirm-btn');
            btn.disabled = false; btn.style.opacity = '1';
        });
        listEl.appendChild(newItem);

        if (data.sessions && data.sessions.length > 0) {
            data.sessions.sort((a, b) => b.session_id.localeCompare(a.session_id));
            for (const s of data.sessions) {
                const item = document.createElement('div');
                item.className = 'orch-session-item';
                const resolvedTitle = _orchResolveTitle(s.title || 'Untitled', s.session_id, agentMap);
                item.innerHTML = `<span class="orch-session-icon">💬</span><div style="flex:1;min-width:0;"><div class="orch-session-title">${escapeHtml(resolvedTitle)}</div><div class="orch-session-id">#${s.session_id.slice(-6)} · ${t('orch_msg_count', {count: s.message_count||0})}</div></div>`;
                item.addEventListener('click', () => {
                    listEl.querySelectorAll('.orch-session-item,.orch-session-new').forEach(el => el.classList.remove('selected'));
                    item.classList.add('selected');
                    selectedSid = s.session_id;
                    const btn = overlay.querySelector('#orch-session-confirm-btn');
                    btn.disabled = false; btn.style.opacity = '1';
                });
                listEl.appendChild(item);
            }
        }
    } catch(e) {
        listEl.innerHTML = '<div style="text-align:center;padding:20px;color:#dc2626;font-size:12px;">' + t('orch_load_session_fail') + '</div>';
    }

    overlay.querySelector('#orch-session-confirm-btn').addEventListener('click', () => {
        if (!selectedSid) return;
        orchTargetSessionId = selectedSid;
        overlay.remove();
        orchDoGenerateAgentYaml();
    });
}

async function orchDoGenerateAgentYaml() {
    const data = orchGetLayoutData();
    // Attach the user-selected target session_id
    data.target_session_id = orchTargetSessionId || null;
    // Attach team for team-scoped workflow saving
    data.team = orch.teamName || '';

    const statusEl = document.getElementById('orch-agent-status');
    const promptEl = document.getElementById('orch-prompt-content');
    const yamlEl = document.getElementById('orch-agent-yaml');
    statusEl.textContent = t('orch_status_communicating', {id: (orchTargetSessionId||'').slice(-6)});
    statusEl.style.cssText = 'color:#2563eb;background:#eff6ff;border-color:#bfdbfe;';
    promptEl.textContent = t('orch_status_generating');
    yamlEl.textContent = t('orch_status_waiting');

    const oldBtn = document.getElementById('orch-goto-chat-container');
    if (oldBtn) oldBtn.remove();

    try {
        const r = await fetch('/proxy_visual/agent-generate-yaml', {
            method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data),
        });
        const res = await r.json();
        if (res.prompt) promptEl.textContent = res.prompt;
        if (res.error) {
            yamlEl.textContent = '# ⚠️ ' + res.error;
            statusEl.textContent = '⚠️ ' + (res.error.includes('401') ? t('orch_status_auth_fail') : t('orch_status_agent_unavail'));
            statusEl.style.cssText = 'color:#dc2626;background:#fef2f2;border-color:#fca5a5;';
            orchToast(t('orch_toast_agent_unavail'));
            return;
        }
        if (res.agent_yaml) {
            yamlEl.textContent = res.agent_yaml;
            if (res.validation?.valid) {
                let statusMsg = t('orch_yaml_valid', {steps: res.validation.steps, types: res.validation.step_types.join(', ')});
                if (res.saved_file && !res.saved_file.startsWith('save_error')) {
                    statusMsg += t('orch_yaml_saved_suffix', {file: res.saved_file});
                }
                try {
                    const applied = await orchAutoReplaceCanvasFromYaml(res.agent_yaml, res.saved_file || '');
                    if (applied) statusMsg += ' · 画布已自动替换';
                } catch (applyErr) {
                    statusMsg += ` · 自动替换失败: ${applyErr.message}`;
                }
                statusEl.textContent = statusMsg;
                statusEl.style.cssText = 'color:#16a34a;background:#f0fdf4;border-color:#86efac;';
                orchToast(res.saved_file ? t('orch_toast_yaml_generated') : t('orch_toast_agent_valid'));
            } else {
                statusEl.textContent = t('orch_yaml_warn', {error: res.validation?.error||''});
                statusEl.style.cssText = 'color:#d97706;background:#fffbeb;border-color:#fbbf24;';
            }
            orchShowGotoChatButton();
        }
    } catch(e) {
        promptEl.textContent = t('orch_comm_fail', {msg: e.message});
        statusEl.textContent = t('orch_status_conn_error');
        statusEl.style.cssText = 'color:#dc2626;background:#fef2f2;border-color:#fca5a5;';
    }
}

function orchShowGotoChatButton() {
    const old = document.getElementById('orch-goto-chat-container');
    if (old) old.remove();

    if (!orchTargetSessionId) return;

    const container = document.createElement('div');
    container.id = 'orch-goto-chat-container';
    container.style.cssText = 'padding: 8px 12px; text-align: center;';

    const sessionLabel = '#' + orchTargetSessionId.slice(-6);
    container.innerHTML = `
        <button class="orch-goto-chat-btn" onclick="orchGotoChat()">
            ${t('orch_goto_chat', {session: escapeHtml(sessionLabel)})}
        </button>
    `;

    const statusEl = document.getElementById('orch-agent-status');
    if (statusEl && statusEl.parentNode) {
        statusEl.parentNode.insertBefore(container, statusEl.nextSibling);
    }
}

async function orchGotoChat() {
    if (!orchTargetSessionId) { orchToast(t('orch_toast_no_session')); return; }

    const prevSessionId = currentSessionId;
    if (currentSessionId === orchTargetSessionId) {
        currentSessionId = '__temp_orch__';
    }

    switchPage('chat');
    await switchToSession(orchTargetSessionId);

    orchToast(t('orch_toast_jumped', {id: orchTargetSessionId.slice(-6)}));
}

// ── Session Status ──
async function orchRefreshSessions() {
    try {
        const r = await fetch('/proxy_visual/sessions-status');
        const sessions = await r.json();
        orch.sessionStatuses = {};
        if (Array.isArray(sessions)) {
            sessions.forEach(s => {
                const sid = s.session_id || s.id || '';
                const isRunning = s.is_running || s.status === 'running' || false;
                orch.sessionStatuses[sid] = isRunning ? 'running' : 'idle';
            });
        }
        orch.nodes.forEach(n => {
            const el = document.getElementById('onode-' + n.id);
            if (!el) return;
            const dot = el.querySelector('.orch-node-status');
            if (!dot) return;
            const isRunning = Object.entries(orch.sessionStatuses).some(([sid, st]) =>
                st === 'running' && (sid.includes(n.name) || sid.includes(n.tag))
            );
            dot.className = 'orch-node-status ' + (isRunning ? 'running' : 'idle');
        });
        orchToast(t('orch_toast_session_updated'));
    } catch(e) {
        orchToast(t('orch_toast_session_fail'));
    }
}

// ── Actions ──
function orchClearCanvas() {
    orch.nodes = []; orch.edges = []; orch.groups = []; orch.selectedNodes.clear();
    orch.zoom = 1; orch.panX = 0; orch.panY = 0; orchApplyTransform();
    const area = document.getElementById('orch-canvas-inner');
    area.querySelectorAll('.orch-node,.orch-group').forEach(el => el.remove());
    orchRenderEdges();
    orchUpdateYaml();
    document.getElementById('orch-canvas-hint').style.display = '';
}

function orchAutoArrange() {
    const n = orch.nodes.length;
    if (n === 0) return;
    orch.zoom = 1; orch.panX = 0; orch.panY = 0; orchApplyTransform();
    const area = document.getElementById('orch-canvas-area');
    const cw = area.offsetWidth, ch = area.offsetHeight;
    const cols = Math.ceil(Math.sqrt(n));
    const gapX = Math.min(180, (cw - 60) / cols);
    const gapY = Math.min(90, (ch - 60) / Math.ceil(n / cols));
    orch.nodes.forEach((node, i) => {
        const col = i % cols, row = Math.floor(i / cols);
        node.x = 40 + col * gapX;
        node.y = 40 + row * gapY;
        const el = document.getElementById('onode-' + node.id);
        if (el) { el.style.left = node.x + 'px'; el.style.top = node.y + 'px'; }
    });
    orchRenderEdges();
    orch.groups.forEach(g => orchUpdateGroupBounds(g));
    orchUpdateNodeBadges();
    orchUpdateYaml();
    orchToast(t('orch_toast_arranged'));
}

async function orchSaveLayout() {
    const name = prompt(t('orch_prompt_layout_name'), 'my-layout');
    if (!name) return;
    const data = orchGetLayoutData();
    data.name = name;
    data.team = orch.teamName || '';
    try {
        await fetch('/proxy_visual/save-layout', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data) });
        orchToast(t('orch_toast_saved', {name}));
    } catch(e) { orchToast(t('orch_toast_save_fail')); }
}

async function orchLoadLayout() {
    try {
        const r = await fetch('/proxy_visual/load-layouts' + _orchTeamQuery());
        const layouts = await r.json();
        if (!layouts.length) { orchToast(t('orch_toast_no_layouts')); return; }

        // Build visual selection modal
        const overlay = document.createElement('div');
        overlay.className = 'orch-modal-overlay';
        overlay.id = 'orch-load-layout-overlay';
        overlay.innerHTML = `
            <div class="orch-modal" style="min-width:360px;max-width:460px;">
                <h3>${t('orch_modal_select_layout')}</h3>
                <div class="orch-session-list" id="orch-layout-select-list" style="max-height:300px;overflow-y:auto;"></div>
                <div class="orch-modal-btns">
                    <button id="orch-layout-cancel-btn" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_modal_cancel')}</button>
                    <button id="orch-layout-del-btn" style="padding:6px 14px;border-radius:6px;border:1px solid #fca5a5;background:#fef2f2;color:#dc2626;cursor:pointer;font-size:12px;display:none;">${t('orch_modal_delete')}</button>
                    <button id="orch-layout-confirm-btn" disabled style="padding:6px 14px;border-radius:6px;border:none;background:#2563eb;color:white;cursor:pointer;font-size:12px;opacity:0.5;">${t('orch_modal_load')}</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        let selectedName = null;
        overlay.querySelector('#orch-layout-cancel-btn').addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

        const listEl = overlay.querySelector('#orch-layout-select-list');
        for (const name of layouts) {
            const item = document.createElement('div');
            item.className = 'orch-session-item';
            item.innerHTML = `<span class="orch-session-icon">📋</span><div style="flex:1;min-width:0;"><div class="orch-session-title">${escapeHtml(name)}</div></div>`;
            item.addEventListener('click', () => {
                listEl.querySelectorAll('.orch-session-item').forEach(el => el.classList.remove('selected'));
                item.classList.add('selected');
                selectedName = name;
                const btn = overlay.querySelector('#orch-layout-confirm-btn');
                btn.disabled = false; btn.style.opacity = '1';
                overlay.querySelector('#orch-layout-del-btn').style.display = '';
            });
            listEl.appendChild(item);
        }

        overlay.querySelector('#orch-layout-del-btn').addEventListener('click', async () => {
            if (!selectedName || !confirm(t('orch_confirm_del_layout', {name: selectedName}))) return;
            try {
                await fetch('/proxy_visual/delete-layout/' + encodeURIComponent(selectedName) + _orchTeamQuery(), { method: 'DELETE' });
                orchToast(t('orch_toast_deleted', {name: selectedName}));
                overlay.remove();
                orchLoadLayout();
            } catch(e) { orchToast(t('orch_toast_del_fail')); }
        });

        overlay.querySelector('#orch-layout-confirm-btn').addEventListener('click', async () => {
            if (!selectedName) return;
            overlay.remove();
            await orchDoLoadLayout(selectedName);
        });
    } catch(e) { orchToast(t('orch_toast_load_fail')); }
}

async function orchDoLoadLayout(name) {
    try {
        const r2 = await fetch('/proxy_visual/load-layout/' + encodeURIComponent(name) + _orchTeamQuery());
        const data = await r2.json();
        if (data.error) { orchToast(data.error); return; }
        orchClearCanvas();

        // Restore settings
        if (data.settings) {
            document.getElementById('orch-repeat').checked = data.settings.repeat === true;
            document.getElementById('orch-rounds').value = data.settings.max_rounds || 5;
            if (data.settings.cluster_threshold) {
                document.getElementById('orch-threshold').value = data.settings.cluster_threshold;
                document.getElementById('orch-threshold-val').textContent = data.settings.cluster_threshold;
            }
        }

        // Restore view (zoom/pan)
        if (data.view) {
            orch.zoom = data.view.zoom || 1;
            orch.panX = data.view.panX || 0;
            orch.panY = data.view.panY || 0;
            orchApplyTransform();
        }

        // Build id mapping: restore nodes with ORIGINAL ids preserved
        const idMap = {};
        (data.nodes||[]).forEach(n => {
            const origId = n.id;
            const newNode = orchAddNode(n, n.x, n.y);
            idMap[origId] = newNode.id;
        });

        // Restore fixed edges using mapped ids
        (data.edges||[]).forEach(e => {
            const src = idMap[e.source];
            const tgt = idMap[e.target];
            if (src && tgt) orchAddEdge(src, tgt);
        });

        // Restore conditional edges
        (data.conditionalEdges||[]).forEach(ce => {
            const src = idMap[ce.source] || ce.source;
            const thenTgt = idMap[ce.then] || ce.then;
            const elseTgt = ce.else ? (idMap[ce.else] || ce.else) : '';
            const mainId = 'oe' + orch.eid++;
            const mainEdge = { id: mainId, source: src, target: thenTgt, edgeType: 'conditional', condition: ce.condition||'', thenTarget: thenTgt, elseTarget: elseTgt };
            orch.edges.push(mainEdge);
            if (elseTgt) {
                const elseId = 'oe' + orch.eid++;
                orch.edges.push({ id: elseId, source: src, target: elseTgt, edgeType: 'conditional', condition: ce.condition||'', thenTarget: '', elseTarget: '', _isElseSibling: mainId });
            }
        });

        // Restore selector edges: add fixed edges from selector node → each choice target
        (data.selectorEdges||[]).forEach(se => {
            const src = idMap[se.source] || se.source;
            const choices = se.choices || {};
            Object.keys(choices).sort((a,b) => Number(a)-Number(b)).forEach(num => {
                const tgt = idMap[choices[num]] || choices[num];
                if (src && tgt) {
                    // Only add if not already connected
                    const exists = orch.edges.some(e => e.source === src && e.target === tgt);
                    if (!exists) orchAddEdge(src, tgt);
                }
            });
        });

        // Restore groups with mapped node ids
        (data.groups||[]).forEach(g => {
            const mappedGroup = {...g, nodeIds: (g.nodeIds||[]).map(nid => idMap[nid]).filter(Boolean)};
            if (mappedGroup.nodeIds.length > 0) {
                orch.groups.push(mappedGroup);
                orchRenderGroup(mappedGroup);
            }
        });

        orchRenderEdges();
        orchUpdateNodeBadges();
        orchUpdateYaml();
        orchToast(t('orch_toast_loaded', {name}));
    } catch(e) { orchToast(t('orch_toast_load_fail') + ': ' + e.message); }
}

function orchApplyLayoutDataFromYamlImport(data) {
    orchClearCanvas();
    if (data.settings) {
        document.getElementById('orch-repeat').checked = data.settings.repeat === true;
        document.getElementById('orch-rounds').value = data.settings.max_rounds || 5;
        if (data.settings.cluster_threshold) {
            document.getElementById('orch-threshold').value = data.settings.cluster_threshold;
            document.getElementById('orch-threshold-val').textContent = data.settings.cluster_threshold;
        }
    }
    if (data.view) {
        orch.zoom = data.view.zoom || 1;
        orch.panX = data.view.panX || 0;
        orch.panY = data.view.panY || 0;
        orchApplyTransform();
    }
    const idMap = {};
    (data.nodes || []).forEach(n => {
        const newNode = orchAddNode(n, n.x, n.y);
        idMap[n.id] = newNode.id;
    });
    (data.edges || []).forEach(e => {
        const src = idMap[e.source], tgt = idMap[e.target];
        if (src && tgt) orchAddEdge(src, tgt);
    });
    (data.conditionalEdges || []).forEach(ce => {
        const src = idMap[ce.source] || ce.source;
        const thenTgt = idMap[ce.then] || ce.then;
        const elseTgt = ce.else ? (idMap[ce.else] || ce.else) : '';
        const mainId = 'oe' + orch.eid++;
        orch.edges.push({ id: mainId, source: src, target: thenTgt, edgeType: 'conditional', condition: ce.condition || '', thenTarget: thenTgt, elseTarget: elseTgt });
        if (elseTgt) {
            const elseId = 'oe' + orch.eid++;
            orch.edges.push({ id: elseId, source: src, target: elseTgt, edgeType: 'conditional', condition: ce.condition || '', thenTarget: '', elseTarget: '', _isElseSibling: mainId });
        }
    });
    (data.selectorEdges || []).forEach(se => {
        const src = idMap[se.source] || se.source;
        const choices = se.choices || {};
        Object.keys(choices).sort((a, b) => Number(a) - Number(b)).forEach(num => {
            const tgt = idMap[choices[num]] || choices[num];
            if (src && tgt) {
                const exists = orch.edges.some(e => e.source === src && e.target === tgt);
                if (!exists) orchAddEdge(src, tgt);
            }
        });
    });
    (data.groups || []).forEach(g => {
        const mapped = { ...g, nodeIds: (g.nodeIds || []).map(nid => idMap[nid]).filter(Boolean) };
        if (mapped.nodeIds.length > 0) { orch.groups.push(mapped); orchRenderGroup(mapped); }
    });
    orchRenderEdges();
    orchUpdateNodeBadges();
    orchUpdateYaml();
}

async function orchAutoReplaceCanvasFromYaml(yamlText, suggestedName) {
    if (!yamlText || !yamlText.trim()) return false;
    const base = (suggestedName && !String(suggestedName).startsWith('save_error'))
        ? String(suggestedName)
        : `ai_generated_${Date.now()}.yaml`;
    const filename = (base.endsWith('.yaml') || base.endsWith('.yml')) ? base : `${base}.yaml`;

    const r = await fetch('/proxy_visual/upload-yaml', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename, content: yamlText, team: orch.teamName || '' }),
    });
    const res = await r.json();
    if (res.error) throw new Error(res.error);
    if (!res.layout) return false;
    orchApplyLayoutDataFromYamlImport(res.layout);
    return true;
}

function orchExportYaml() {
    const yaml = document.getElementById('orch-yaml-content').textContent;
    if (!yaml || yaml.startsWith(t('orch_rule_yaml_hint').substring(0,2))) { orchToast(t('orch_toast_gen_yaml')); return; }
    navigator.clipboard.writeText(yaml).then(() => orchToast(t('orch_toast_yaml_copied'))).catch(() => {
        const ta = document.createElement('textarea'); ta.value = yaml; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); orchToast(t('orch_toast_yaml_copied'));
    });
}

// ── Download YAML as file ──
function orchDownloadYaml() {
    const yaml = document.getElementById('orch-yaml-content').textContent;
    if (!yaml || yaml.startsWith(t('orch_rule_yaml_hint').substring(0,2))) { orchToast(t('orch_toast_gen_yaml')); return; }
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const fname = `oasis_${ts}.yaml`;
    const blob = new Blob([yaml], { type: 'application/x-yaml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = fname; a.style.display = 'none';
    document.body.appendChild(a); a.click();
    setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
    orchToast(t('orch_toast_yaml_downloaded'));
}

// ── Upload YAML (button click) ──
function orchUploadYamlClick() {
    document.getElementById('orch-yaml-upload-input').click();
}

function orchHandleYamlUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    event.target.value = ''; // reset so re-selecting same file works
    orchImportYamlFile(file);
}

// ── Import a YAML file → upload to server → load as layout ──
async function orchImportYamlFile(file) {
    const fname = file.name || 'upload.yaml';
    if (!fname.endsWith('.yaml') && !fname.endsWith('.yml')) {
        orchToast(t('orch_toast_not_yaml'));
        return;
    }
    try {
        const text = await file.text();
        // Send YAML text to backend for saving and conversion
        const r = await fetch('/proxy_visual/upload-yaml', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: fname, content: text, team: orch.teamName || '' }),
        });
        const res = await r.json();
        if (res.error) { orchToast(t('orch_toast_yaml_upload_fail') + ': ' + res.error); return; }
        // Load the returned layout data
        if (res.layout) {
            orchClearCanvas();
            const data = res.layout;
            // Restore settings
            if (data.settings) {
                document.getElementById('orch-repeat').checked = data.settings.repeat === true;
                document.getElementById('orch-rounds').value = data.settings.max_rounds || 5;
                if (data.settings.cluster_threshold) {
                    document.getElementById('orch-threshold').value = data.settings.cluster_threshold;
                    document.getElementById('orch-threshold-val').textContent = data.settings.cluster_threshold;
                }
            }
            const idMap = {};
            (data.nodes || []).forEach(n => {
                const newNode = orchAddNode(n, n.x, n.y);
                idMap[n.id] = newNode.id;
            });
            (data.edges || []).forEach(e => {
                const src = idMap[e.source], tgt = idMap[e.target];
                if (src && tgt) orchAddEdge(src, tgt);
            });
            // Restore conditional edges from uploaded data
            (data.conditionalEdges || []).forEach(ce => {
                const src = idMap[ce.source] || ce.source;
                const thenTgt = idMap[ce.then] || ce.then;
                const elseTgt = ce.else ? (idMap[ce.else] || ce.else) : '';
                const mainId = 'oe' + orch.eid++;
                orch.edges.push({ id: mainId, source: src, target: thenTgt, edgeType: 'conditional', condition: ce.condition||'', thenTarget: thenTgt, elseTarget: elseTgt });
                if (elseTgt) {
                    const elseId = 'oe' + orch.eid++;
                    orch.edges.push({ id: elseId, source: src, target: elseTgt, edgeType: 'conditional', condition: ce.condition||'', thenTarget: '', elseTarget: '', _isElseSibling: mainId });
                }
            });
            // Restore selector edges
            (data.selectorEdges || []).forEach(se => {
                const src = idMap[se.source] || se.source;
                const choices = se.choices || {};
                Object.keys(choices).sort((a,b) => Number(a)-Number(b)).forEach(num => {
                    const tgt = idMap[choices[num]] || choices[num];
                    if (src && tgt) {
                        const exists = orch.edges.some(e => e.source === src && e.target === tgt);
                        if (!exists) orchAddEdge(src, tgt);
                    }
                });
            });
            (data.groups || []).forEach(g => {
                const mapped = { ...g, nodeIds: (g.nodeIds || []).map(nid => idMap[nid]).filter(Boolean) };
                if (mapped.nodeIds.length > 0) { orch.groups.push(mapped); orchRenderGroup(mapped); }
            });
            orchRenderEdges();
            orchUpdateNodeBadges();
            orchUpdateYaml();
            orchToast(t('orch_toast_yaml_uploaded', { name: fname }));
        } else {
            // Fallback: just show the YAML text
            document.getElementById('orch-yaml-content').textContent = text;
            orchToast(t('orch_toast_yaml_uploaded', { name: fname }));
        }
    } catch (e) {
        orchToast(t('orch_toast_yaml_upload_fail') + ': ' + e.message);
    }
}

// ── Drag & Drop YAML file onto canvas ──
function orchSetupFileDrop() {
    const canvas = document.getElementById('orch-canvas-area');
    const dropOverlay = document.createElement('div');
    dropOverlay.id = 'orch-drop-overlay';
    dropOverlay.className = 'orch-drop-overlay';
    dropOverlay.innerHTML = '<div class="orch-drop-content"><div style="font-size:48px;">📄</div><div>' + t('orch_drop_hint') + '</div></div>';
    canvas.style.position = 'relative';
    canvas.appendChild(dropOverlay);

    let dragCounter = 0;

    canvas.addEventListener('dragenter', e => {
        // Only show overlay for file drags (not sidebar card drags)
        if (e.dataTransfer.types.includes('Files')) {
            e.preventDefault();
            dragCounter++;
            dropOverlay.classList.add('visible');
        }
    });
    canvas.addEventListener('dragover', e => {
        if (e.dataTransfer.types.includes('Files')) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
        }
    });
    canvas.addEventListener('dragleave', e => {
        if (e.dataTransfer.types.includes('Files')) {
            dragCounter--;
            if (dragCounter <= 0) {
                dragCounter = 0;
                dropOverlay.classList.remove('visible');
            }
        }
    });
    canvas.addEventListener('drop', e => {
        dragCounter = 0;
        dropOverlay.classList.remove('visible');
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            const file = e.dataTransfer.files[0];
            if (file.name.endsWith('.yaml') || file.name.endsWith('.yml')) {
                e.preventDefault();
                e.stopPropagation();
                orchImportYamlFile(file);
                return;
            }
        }
        // Let the original drop handler process non-file drags (sidebar cards)
    }, true);
}

function orchCopyPrompt() {
    const text = document.getElementById('orch-prompt-content').textContent;
    navigator.clipboard.writeText(text).catch(() => {}); orchToast(t('orch_toast_prompt_copied'));
}
function orchCopyAgentYaml() {
    const text = document.getElementById('orch-agent-yaml').textContent;
    navigator.clipboard.writeText(text).catch(() => {}); orchToast(t('orch_toast_agent_yaml_copied'));
}

function orchUpdateStatus() {
    document.getElementById('orch-status-bar').textContent = t('orch_status_bar', {nodes: orch.nodes.length, edges: orch.edges.length, groups: orch.groups.length});
}

function orchToast(msg) {
    const existing = document.querySelector('.orch-toast');
    if (existing) existing.remove();
    const t = document.createElement('div');
    t.className = 'orch-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2500);
}

// ── Generate Team from Workflow ──

async function orchGenerateTeam() {
    // 1. Collect eligible nodes (expert, session_agent, external); dedupe by tag
    const eligible = ['expert', 'session_agent', 'external'];
    const seen = {};
    for (const node of orch.nodes) {
        if (eligible.includes(node.type) && node.tag) {
            seen[node.tag] = node;
        }
    }
    const agentNodes = Object.values(seen);

    // 2. Build overlay
    const overlay = document.createElement('div');
    overlay.className = 'orch-modal-overlay';
    overlay.id = 'orch-gt-overlay';

    // Fetch current team list for the select
    let teamList = [];
    try {
        const tlResp = await fetch('/teams');
        if (tlResp.ok) {
            const tlData = await tlResp.json();
            teamList = tlData.teams || [];
        }
    } catch (e) { /* silently use empty list */ }

    const teamOptions = teamList.map(
        tm => `<option value="${escapeHtml(tm)}">${escapeHtml(tm)}</option>`
    ).join('');

    overlay.innerHTML = `
        <div class="orch-modal" style="min-width:420px;max-width:520px;max-height:80vh;overflow-y:auto;">
            <h3 style="margin:0 0 12px;font-size:14px;font-weight:700;">${t('orch_gt_title')}</h3>

            <div style="margin-bottom:10px;">
                <label style="font-size:11px;font-weight:600;color:#374151;">${t('orch_gt_label_team')}</label>
                <div style="display:flex;gap:6px;margin-top:4px;">
                    <select id="orch-gt-team-select" style="flex:1;padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;">
                        <option value="">— ${t('orch_gt_new_team')} —</option>
                        ${teamOptions}
                    </select>
                </div>
                <input id="orch-gt-new-name" type="text" placeholder="${t('orch_gt_new_team_placeholder')}"
                    style="width:100%;margin-top:6px;padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;box-sizing:border-box;">
            </div>

            <div style="margin-bottom:6px;font-size:11px;font-weight:600;color:#374151;">${t('orch_gt_agents_title')} (${agentNodes.length})</div>
            <div id="orch-gt-agent-list" style="display:flex;flex-direction:column;gap:4px;margin-bottom:12px;">
                ${agentNodes.length === 0
                    ? `<div style="font-size:11px;color:#9ca3af;padding:6px 0;">${t('orch_gt_no_agents')}</div>`
                    : agentNodes.map(n => `
                        <div class="orch-gt-row" data-tag="${escapeHtml(n.tag)}" style="display:flex;align-items:center;gap:6px;padding:5px 8px;border:1px solid #e5e7eb;border-radius:6px;font-size:12px;">
                            <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                                ${escapeHtml(n.name || n.tag)}
                                <span style="color:#9ca3af;margin-left:4px;">[${escapeHtml(n.tag)}]</span>
                            </span>
                            <span class="orch-gt-conflict-badge" style="display:none;font-size:10px;color:#f59e0b;white-space:nowrap;">${t('orch_gt_conflict_label')}</span>
                            <button class="orch-gt-skip-btn" data-tag="${escapeHtml(n.tag)}" style="display:none;padding:2px 8px;font-size:10px;border-radius:4px;border:1px solid #d1d5db;background:#f9fafb;cursor:pointer;">${t('orch_gt_conflict_skip')}</button>
                            <button class="orch-gt-overwrite-btn" data-tag="${escapeHtml(n.tag)}" style="display:none;padding:2px 8px;font-size:10px;border-radius:4px;border:none;background:#6366f1;color:white;cursor:pointer;">${t('orch_gt_conflict_overwrite')}</button>
                        </div>`).join('')
                }
            </div>

            <div class="orch-modal-btns">
                <button id="orch-gt-cancel" style="padding:6px 14px;border-radius:6px;border:1px solid #d1d5db;background:white;color:#374151;cursor:pointer;font-size:12px;">${t('orch_gt_cancel')}</button>
                <button id="orch-gt-confirm" style="padding:6px 14px;border-radius:6px;border:none;background:#6366f1;color:white;cursor:pointer;font-size:12px;">${t('orch_gt_confirm')}</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Per-row resolution state: tag → "skip"|"overwrite"|null
    const resolutions = {};

    // Wire conflict toggle buttons
    overlay.querySelectorAll('.orch-gt-skip-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tag = btn.dataset.tag;
            resolutions[tag] = 'skip';
            _orchGtHighlightRow(overlay, tag, 'skip');
        });
    });
    overlay.querySelectorAll('.orch-gt-overwrite-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tag = btn.dataset.tag;
            resolutions[tag] = 'overwrite';
            _orchGtHighlightRow(overlay, tag, 'overwrite');
        });
    });

    // Show/hide new-name input based on team select
    const teamSelect = overlay.querySelector('#orch-gt-team-select');
    const newNameInput = overlay.querySelector('#orch-gt-new-name');
    function _syncNewNameVisibility() {
        newNameInput.style.display = teamSelect.value === '' ? '' : 'none';
    }
    _syncNewNameVisibility();
    teamSelect.addEventListener('change', async () => {
        _syncNewNameVisibility();
        if (teamSelect.value) {
            await _orchGtUpdateConflicts(overlay, teamSelect.value, resolutions);
        } else {
            // New team — no conflicts
            overlay.querySelectorAll('.orch-gt-row').forEach(row => {
                row.querySelector('.orch-gt-conflict-badge').style.display = 'none';
                row.querySelector('.orch-gt-skip-btn').style.display = 'none';
                row.querySelector('.orch-gt-overwrite-btn').style.display = 'none';
                row.style.borderColor = '#e5e7eb';
            });
        }
    });

    // Dismiss
    overlay.querySelector('#orch-gt-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    // Confirm
    const confirmBtn = overlay.querySelector('#orch-gt-confirm');
    confirmBtn.addEventListener('click', async () => {
        const selectedTeam = teamSelect.value;
        const newName = newNameInput.value.trim();
        const teamName = selectedTeam || newName;
        if (!teamName) { orchToast(t('orch_gt_toast_no_team')); return; }
        if (confirmBtn.disabled) return;
        confirmBtn.disabled = true;

        const finalResolutions = Object.assign({}, resolutions);

        // Build nodes payload from canvas nodes
        const payload = {
            nodes: agentNodes.map(n => ({
                type: n.type,
                name: n.name || '',
                tag: n.tag || '',
                persona: n.persona || '',
                temperature: n.temperature != null ? n.temperature : 0.7,
                session: n.session || '',
                global_name: n.global_name || '',
                meta: n.meta || {}
            })),
            create_if_missing: !selectedTeam,
            resolutions: finalResolutions
        };

        try {
            const resp = await fetch(`/teams/${encodeURIComponent(teamName)}/generate-from-workflow`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                orchToast(t('orch_gt_toast_err') + ': ' + (data.error || resp.status));
                return;
            }
            orchToast(t('orch_gt_toast_ok', { added: (data.added || []).length, skipped: (data.skipped || []).length }));
            overlay.remove();
            // Refresh team list so the new team appears in the selector
            if (!selectedTeam) orchLoadTeamList();
        } catch (e) {
            orchToast(t('orch_gt_toast_err') + ': ' + e.message);
        } finally {
            confirmBtn.disabled = false;
        }
    });
}

async function _orchGtUpdateConflicts(overlay, teamName, resolutions) {
    if (!teamName) return;
    let existingTags = new Set();
    try {
        // Fetch members (oasis + external agents) and experts in parallel
        const [mResp, eResp] = await Promise.all([
            fetch(`/teams/${encodeURIComponent(teamName)}/members`),
            fetch(`/teams/${encodeURIComponent(teamName)}/experts`)
        ]);
        if (mResp.ok) {
            const mData = await mResp.json();
            (mData.members || []).forEach(m => { if (m.tag) existingTags.add(m.tag); });
        }
        if (eResp.ok) {
            const eData = await eResp.json();
            (eData.experts || []).forEach(e => { if (e.tag) existingTags.add(e.tag); });
        }
    } catch (e) { /* silently ignore — show no conflicts */ }

    overlay.querySelectorAll('.orch-gt-row').forEach(row => {
        const tag = row.dataset.tag;
        if (existingTags.has(tag)) {
            row.querySelector('.orch-gt-conflict-badge').style.display = '';
            row.querySelector('.orch-gt-skip-btn').style.display = '';
            row.querySelector('.orch-gt-overwrite-btn').style.display = '';
            // Default to skip unless already resolved
            if (!resolutions[tag]) {
                resolutions[tag] = 'skip';
                _orchGtHighlightRow(overlay, tag, 'skip');
            } else {
                _orchGtHighlightRow(overlay, tag, resolutions[tag]);
            }
        } else {
            row.querySelector('.orch-gt-conflict-badge').style.display = 'none';
            row.querySelector('.orch-gt-skip-btn').style.display = 'none';
            row.querySelector('.orch-gt-overwrite-btn').style.display = 'none';
            row.style.borderColor = '#e5e7eb';
            delete resolutions[tag];
        }
    });
}

function _orchGtHighlightRow(overlay, tag, resolution) {
    const row = overlay.querySelector(`.orch-gt-row[data-tag="${CSS.escape(tag)}"]`);
    if (!row) return;
    row.style.borderColor = resolution === 'overwrite' ? '#6366f1' : '#f59e0b';
    const skipBtn = row.querySelector('.orch-gt-skip-btn');
    const owBtn = row.querySelector('.orch-gt-overwrite-btn');
    if (skipBtn) skipBtn.style.background = resolution === 'skip' ? '#fef3c7' : '#f9fafb';
    if (owBtn) owBtn.style.background = resolution === 'overwrite' ? '#4f46e5' : '#6366f1';
}
