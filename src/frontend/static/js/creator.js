(function () {
    // ─── Shared Helpers ───────────────────────────────
    function $(id) { return document.getElementById(id); }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function compactText(value) {
        return String(value || '').replace(/\s+/g, ' ').trim();
    }

    function getPersonaPreview(expert) {
        var explicitPreview = compactText((expert && (expert.persona_preview || expert.preview)) || '');
        if (explicitPreview) return explicitPreview;

        var description = compactText(expert && expert.description);
        if (description) return description;

        var persona = String((expert && expert.persona) || '').trim();
        if (!persona) return '';

        var frontMatterMatch = persona.match(/^---\s*[\r\n]+([\s\S]*?)[\r\n]+---\s*/);
        if (frontMatterMatch) {
            var frontMatter = frontMatterMatch[1];
            var descMatch = frontMatter.match(/(?:^|\n)description:\s*([\s\S]*?)(?:\n[A-Za-z_][A-Za-z0-9_ -]*:|$)/i);
            if (descMatch && descMatch[1]) {
                var frontMatterDescription = compactText(descMatch[1].replace(/^["']|["']$/g, ''));
                if (frontMatterDescription) return frontMatterDescription;
            }
            persona = persona.slice(frontMatterMatch[0].length).trim();
        }

        return compactText(
            persona
                .replace(/^#{1,6}\s+/gm, '')
                .replace(/^\s*[-*]\s+/gm, '')
                .replace(/`{1,3}/g, '')
                .replace(/\[(.*?)\]\((.*?)\)/g, '$1')
        );
    }

    function getExpertFullPersona(expert) {
        return String((expert && (expert.persona_full || expert.persona)) || '').trim();
    }

    function getDownloadFilename(resp, fallbackName) {
        var disposition = '';
        if (resp && resp.headers && resp.headers.get) {
            disposition = resp.headers.get('content-disposition') || '';
        }
        if (disposition) {
            var utf8Match = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
            if (utf8Match && utf8Match[1]) {
                try {
                    return decodeURIComponent(utf8Match[1].trim().replace(/^"|"$/g, ''));
                } catch (e) { /* fall through */ }
            }
            var asciiMatch = disposition.match(/filename\s*=\s*"?([^";]+)"?/i);
            if (asciiMatch && asciiMatch[1]) {
                return asciiMatch[1].trim();
            }
        }
        return fallbackName;
    }

    var CREATOR_I18N = {
        'zh-CN': {
            creator_page_title: 'ClawCross Creator',
            creator_nav_msgcenter: '← 消息中心',
            creator_lang_label: '语言',
            creator_nav_studio: 'ClawCross Studio',
            creator_badge: 'ClawCross Creator · AI + TinyFish Powered',
            creator_hero_text: '描述你的业务场景或任务目标，ClawCross Creator 通过 AI Agent 发现相关 SOP/组织架构 URL，由 TinyFish 深度提取角色数据，再由 AI 智能构建 DAG 工作流，自动生成包含 Persona、工作流和依赖图的多 Agent 协作团队。',
            creator_status_ready: '就绪',
            creator_status_ready_hint: '输入任务描述开始构建团队',
            creator_step1_title: '描述你的团队目标',
            creator_team_name_label: '团队名称',
            creator_team_name_placeholder: '例如: SaaS增长团队',
            creator_task_desc_label: '任务/业务描述',
            creator_task_desc_placeholder: '例如: 负责一个 B2B SaaS 产品从用户增长到续费的全链路运营',
            creator_example_btn: '💡 使用示例指令',
            creator_mode_direct: '✏️ 直接定义角色',
            creator_mode_discover: '🔍 AI 智能发现',
            creator_mode_import_colleague: '👥 从同事导入',
            creator_mode_import_mentor: '🎓 从导师导入',
            creator_import_colleague_title: '导入同事 Skill',
            creator_import_colleague_hint: '可以直接从飞书采集并生成同事，或者导入 colleague-skill 已生成的文件。',
            creator_generate_colleague_kicker: '🪶 直接生成',
            creator_generate_colleague_hint: '填写飞书应用信息和同事基本资料，ClawCross Creator 会采集消息、自动蒸馏 persona/work，并直接导入为团队角色。',
            creator_feishu_app_id_label: '飞书 App ID',
            creator_feishu_app_id_placeholder: '例如: cli_xxx',
            creator_feishu_app_secret_label: '飞书 App Secret',
            creator_feishu_app_secret_placeholder: '例如: xxxxx',
            creator_feishu_target_name_label: '同事姓名',
            creator_feishu_target_name_placeholder: '例如: 张三',
            creator_feishu_role_label: '岗位',
            creator_feishu_role_placeholder: '例如: 后端工程师',
            creator_feishu_company_label: '公司 / 团队',
            creator_feishu_company_placeholder: '例如: Clawcross',
            creator_feishu_level_label: '层级',
            creator_feishu_level_placeholder: '例如: L4 / P6',
            creator_feishu_personality_tags_label: '性格标签（逗号分隔）',
            creator_feishu_personality_tags_placeholder: '例如: 直接, 数据驱动',
            creator_feishu_msg_limit_label: '消息上限',
            creator_generate_colleague_btn: '🪶 采集并生成同事',
            creator_generate_colleague_loading: '正在从飞书采集并蒸馏同事...',
            creator_generate_colleague_success: '同事生成成功：{name}',
            creator_import_colleague_repo: '📦 仓库地址',
            creator_import_colleague_repo_url: 'https://github.com/titanwings/colleague-skill',
            creator_import_colleague_steps: '生成步骤：① 克隆仓库 → ② 在 Claude Code / Cursor 中运行 /create-colleague → ③ 按提示填写同事信息并提供原材料 → ④ 生成的文件在 colleagues/{slug}/ 目录下',
            creator_import_colleague_files: '上传以下文件（位于 colleagues/{slug}/ 目录中）：',
            creator_import_mentor_title: '导入导师 Skill',
            creator_import_mentor_hint: '可以直接从 ArXiv 搜索生成导师，或者导入 supervisor 已生成的文件。',
            creator_generate_mentor_kicker: '📚 直接生成',
            creator_generate_mentor_hint: '输入导师姓名后，ClawCross Creator 会搜索 ArXiv、生成导师档案，并直接导入为团队角色。',
            creator_arxiv_author_name_label: '导师姓名',
            creator_arxiv_author_name_placeholder: '例如: Geoffrey Hinton',
            creator_arxiv_affiliation_label: '机构（可选）',
            creator_arxiv_affiliation_placeholder: '例如: University of Toronto',
            creator_arxiv_max_results_label: '论文上限',
            creator_generate_mentor_btn: '📚 搜索 ArXiv 并生成导师',
            creator_generate_mentor_loading: '正在搜索 ArXiv 并生成导师...',
            creator_generate_mentor_success: '导师生成成功：{name}（{count} 篇论文）',
            creator_import_mentor_repo: '📦 仓库地址',
            creator_import_mentor_repo_url: 'https://github.com/ybq22/supervisor',
            creator_import_mentor_steps: '生成步骤：① 克隆仓库 → ② 在 Claude Code / Cursor 中运行 /distill-mentor <导师名> → ③ 自动搜索 ArXiv 论文并分析风格 → ④ 生成的文件在 ~/.claude/mentors/ 和 ~/.claude/skills/ 下',
            creator_import_mentor_files: '上传以下文件：',
            creator_import_meta_json: 'meta.json — 同事基础信息（必需）',
            creator_import_persona_md: 'persona.md — 5 层性格画像（必需）',
            creator_import_work_md: 'work.md — 工作能力（可选，推荐上传）',
            creator_import_or_local_path: '或者填写本机路径：',
            creator_import_colleague_dir: 'colleagues/{slug} 目录路径（自动读取 meta.json / persona.md / work.md）',
            creator_import_colleague_dir_placeholder: '例如: ~/.claude/skills/create-colleague/colleagues/zhangsan',
            creator_import_mentor_json: '{name}.json — 导师档案 JSON（必需）',
            creator_import_skill_md: 'SKILL.md — 导师完整 Skill（可选，推荐上传）',
            creator_import_mentor_json_path: '{name}.json 路径（未上传时必需）',
            creator_import_mentor_json_path_placeholder: '例如: ~/.claude/mentors/Geoffrey_Hinton.json',
            creator_import_skill_md_path: 'SKILL.md 路径（可选）',
            creator_import_skill_md_path_placeholder: '例如: ~/.claude/skills/geoffrey-hinton/SKILL.md',
            creator_import_btn: '📥 导入到 ClawCross Creator',
            creator_import_success: '导入成功！',
            creator_import_error: '导入失败',
            creator_step2_manual_kicker: 'Step 2 · 手动定义',
            creator_step2_manual_title: '定义团队角色',
            creator_roles_hint: '添加你的团队角色。每个角色包含名称、职责和依赖关系。',
            creator_add_role_btn: '+ 添加角色',
            creator_paste_json_btn: '粘贴 JSON',
            creator_step2_discovery_kicker: 'Step 2 · AI + TinyFish Discovery',
            creator_step2_discovery_title: '🔍 AI 智能发现',
            creator_discovery_hint: 'AI Agent 首先搜索发现与你任务相关的 SOP 和组织架构 URL，然后 TinyFish 深度爬取这些页面提取角色数据。你可以在提取阶段观看浏览器操作。',
            creator_discover_btn: '🚀 开始智能发现',
            creator_discover_stop_btn: '⏹ 停止',
            creator_phase_discovery: 'AI 发现 URL',
            creator_phase_extraction: '🐟 TinyFish 提取角色',
            creator_phase_confirm: '确认角色',
            creator_discovery_log_title: '🔍 AI URL 发现日志',
            creator_discovery_log_idle: '等待启动发现流程...',
            creator_extraction_tabs_title: '🐟 TinyFish 并行提取',
            creator_extraction_summary_title: '📊 提取汇总',
            creator_discovered_roles_title: '✅ 发现的角色',
            creator_roles_count_zero: '0 个角色',
            creator_discovery_mode_llm: '🤖 LLM 智能选择',
            creator_discovery_mode_manual: '✋ 手动选择',
            creator_max_roles_label: '最大角色数',
            creator_discovery_smart_select_btn: '🤖 开始智能筛选',
            creator_max_roles_hint: 'LLM 将选出最重要的 N 个角色，并自动匹配预设专家池',
            creator_discovery_manual_hint: '💡 点击角色卡片的复选框来选择你需要的角色，不受数量限制',
            creator_selected_count_zero: '已选 0 个',
            creator_select_all: '全选',
            creator_deselect_all: '全不选',
            creator_use_selected_roles: '✅ 使用已选角色',
            creator_add_discovered_role: '+ 补充角色',
            creator_step3_title: '团队预览',
            creator_build_btn: '🔨 构建团队',
            creator_download_btn: '📦 下载 ZIP',
            creator_import_btn: '📥 导入到 Clawcross',
            creator_workflow_dag_title: 'OASIS Workflow DAG',
            creator_zoom_out: '缩小',
            creator_zoom_in: '放大',
            creator_reset_view: '重置视图',
            creator_view_yaml: '查看 YAML 源码',
            creator_tinyfish_status_title: '爬取引擎状态',
            creator_tinyfish_status_loading: '正在检查 TinyFish 配置...',
            creator_expert_pool_title: '预设专家池',
            creator_expert_pool_hint: '点击专家可快速添加为团队角色（复用消息中心通讯录同一专家源）',
            creator_expert_pool_search_placeholder: '🔍 搜索专家名称 / 分类...',
            creator_expert_pool_loading: '正在加载专家池...',
            creator_build_jobs_kicker: '构建记录',
            creator_build_jobs_title: '构建记录',
            creator_jobs_empty: '暂无构建记录',
            creator_cat_public: '公共专家',
            creator_cat_design: '设计',
            creator_cat_engineering: '工程',
            creator_cat_marketing: '市场营销',
            creator_cat_product: '产品',
            creator_cat_project_management: '项目管理',
            creator_cat_spatial_computing: '空间计算',
            creator_cat_specialized: '专业领域',
            creator_cat_support: '技术支持',
            creator_cat_testing: '测试',
            creator_cat_custom: '自定义专家',
            creator_no_roles_hint: '还没有角色。从右侧专家池添加预设专家，或点击 "+ 添加角色" 手动创建。',
            creator_role_preset_title: '预设专家，构建时使用完整 Persona',
            creator_role_persona_label: '📄 Persona',
            creator_role_persona_note: '(构建时使用此完整版本)',
            creator_role_traits_label: '性格特质 (逗号分隔)',
            creator_role_responsibilities_label: '主要职责 (逗号分隔)',
            creator_role_depends_on_label: '依赖角色 (逗号分隔)',
            creator_role_tools_label: '工具 (逗号分隔)',
            creator_role_name_placeholder: '角色名称',
            creator_role_traits_placeholder: '例如: 数据驱动, 全局视野',
            creator_role_responsibilities_placeholder: '例如: 制定策略, 监控指标',
            creator_role_depends_on_placeholder: '例如: 增长负责人, 产品经理',
            creator_role_depends_on_single_placeholder: '例如: 增长负责人',
            creator_role_tools_placeholder: '例如: Python, SQL, Notion',
            creator_role_remove_title: '删除',
            creator_no_discovered_roles: '未发现角色',
            creator_preset_match_title: '与预设专家语义匹配，构建时将使用预设 Persona',
            creator_preset_match_label: '🌟 匹配',
            creator_role_count: '{count} 个角色',
            creator_selected_count: '已选 {count} / {total} 个',
            creator_json_modal_title: '粘贴角色 JSON',
            creator_json_modal_hint: '粘贴一个 JSON 数组，每个对象包含 role_name, personality_traits, primary_responsibilities, depends_on, tools_used 字段。',
            creator_json_modal_placeholder: '[{"role_name": "产品经理", "personality_traits": ["逻辑"], "primary_responsibilities": ["需求分析"], "depends_on": [], "tools_used": ["Figma"]}]',
            creator_json_modal_cancel: '取消',
            creator_json_modal_import: '导入',
            creator_open_live_watch: '🔗 Open Live Watch',
            creator_page_fallback: '页面 {index}',
            creator_session_waiting: '等待中',
            creator_session_waiting_tinyfish: '等待 TinyFish 启动...',
            creator_session_preparing: '准备提取',
            creator_discovered_pages_title: '📄 发现的页面 ({count})',
            creator_build_summary_title: '构建摘要',
            creator_build_summary_total_roles: '角色数',
            creator_build_summary_workflow_nodes: '工作流节点',
            creator_build_summary_selectors: '选择器节点',
            creator_build_summary_review_loops: '复核回环',
            creator_build_summary_preset_matched: '命中预设',
            creator_build_summary_custom_generated: 'AI 生成',
            creator_build_summary_custom_persona: '自定义 Persona',
            creator_build_summary_dag_enhanced: 'DAG 增强',
            creator_build_summary_team_name: 'Team 名称',
            creator_build_summary_workflow_mode: '工作流模式',
            creator_persona_missing_preview: '未提取到简介，将直接使用下方 Persona。',
            creator_persona_origin_preset_hint: '当前来源是预设 Persona；你可以在这里直接覆盖成更贴合团队的版本。',
            creator_persona_origin_generated_hint: '当前来源是 AI 生成 Persona；建议在这里补充语气、边界和交付标准。',
            creator_persona_origin_preset_label: '预设 Persona · 可手动覆盖',
            creator_persona_origin_generated_label: '生成 Persona · 建议手动精修',
            creator_chars_label: '{count} 字符',
            creator_expert_pool_empty: '暂无可用专家',
            creator_expert_pool_load_failed: '加载失败',
            creator_expert_pool_no_match: '没有匹配的专家',
            creator_expert_pool_add_title: '添加为角色',
            creator_tinyfish_state_ready: '状态: ✅ 就绪',
            creator_tinyfish_state_missing: '状态: ⚠️ 未配置',
            creator_tinyfish_base_url_label: 'Base URL',
            creator_tinyfish_recent_runs_label: '最近运行',
            creator_tinyfish_mode_disabled: 'TinyFish API Key 未配置',
            creator_tinyfish_status_load_failed: 'TinyFish 状态加载失败: {error}',
            creator_default_team_name_prefix: '新团队_',
            creator_job_status_complete: '构建完成',
            creator_job_status_failed: '构建失败',
            creator_job_status_running: '构建中',
            creator_job_status_pending: '等待开始',
            creator_job_meta_roles: '{count} 个角色 · {time}',
            creator_job_meta_empty: '暂无结果 · {time}',
            creator_jobs_load_failed: '构建记录加载失败: {error}',
            creator_jobs_roles_suffix: 'roles',
            creator_example_team_name: 'SaaS增长团队',
            creator_example_task_desc: '负责一个 B2B SaaS 产品从用户增长到续费的全链路运营，包括获客、激活、留存、变现、推荐五个阶段，需要数据分析驱动决策，结合内容营销和产品内增长手段。'
        },
        en: {
            creator_page_title: 'ClawCross Creator',
            creator_nav_msgcenter: '← Message Center',
            creator_lang_label: 'Language',
            creator_nav_studio: 'ClawCross Studio',
            creator_badge: 'ClawCross Creator · AI + TinyFish Powered',
            creator_hero_text: 'Describe your business scenario or target workflow. ClawCross Creator uses AI Agents to discover relevant SOP and org-structure URLs, uses TinyFish to extract role data, and then builds a DAG workflow to generate a multi-agent team with personas, workflow, and dependency graph.',
            creator_status_ready: 'Ready',
            creator_status_ready_hint: 'Describe the task to start building the team',
            creator_step1_title: 'Describe Your Team Goal',
            creator_team_name_label: 'Team Name',
            creator_team_name_placeholder: 'e.g. SaaS Growth Team',
            creator_task_desc_label: 'Task / Business Description',
            creator_task_desc_placeholder: 'e.g. Own the full B2B SaaS lifecycle from acquisition to renewal',
            creator_example_btn: '💡 Use Example Prompt',
            creator_mode_direct: '✏️ Define Roles Directly',
            creator_mode_discover: '🔍 AI Discovery',
            creator_mode_import_colleague: '👥 Import Colleague',
            creator_mode_import_mentor: '🎓 Import Mentor',
            creator_import_colleague_title: 'Import Colleague Skill',
            creator_import_colleague_hint: 'Generate a colleague directly from Feishu or import artifacts from colleague-skill.',
            creator_generate_colleague_kicker: '🪶 Direct Generate',
            creator_generate_colleague_hint: 'Provide Feishu app credentials and basic colleague info. ClawCross Creator will collect messages, distill persona/work, and import the role directly.',
            creator_feishu_app_id_label: 'Feishu App ID',
            creator_feishu_app_id_placeholder: 'e.g. cli_xxx',
            creator_feishu_app_secret_label: 'Feishu App Secret',
            creator_feishu_app_secret_placeholder: 'e.g. xxxxx',
            creator_feishu_target_name_label: 'Colleague Name',
            creator_feishu_target_name_placeholder: 'e.g. Alice',
            creator_feishu_role_label: 'Role',
            creator_feishu_role_placeholder: 'e.g. Backend Engineer',
            creator_feishu_company_label: 'Company / Team',
            creator_feishu_company_placeholder: 'e.g. Clawcross',
            creator_feishu_level_label: 'Level',
            creator_feishu_level_placeholder: 'e.g. L4 / P6',
            creator_feishu_personality_tags_label: 'Personality Tags (comma separated)',
            creator_feishu_personality_tags_placeholder: 'e.g. direct, data-driven',
            creator_feishu_msg_limit_label: 'Message Limit',
            creator_generate_colleague_btn: '🪶 Collect & Generate Colleague',
            creator_generate_colleague_loading: 'Collecting Feishu messages and distilling colleague artifacts...',
            creator_generate_colleague_success: 'Colleague generated: {name}',
            creator_import_colleague_repo: '📦 Repository',
            creator_import_colleague_repo_url: 'https://github.com/titanwings/colleague-skill',
            creator_import_colleague_steps: 'Steps: ① Clone the repo → ② Run /create-colleague in Claude Code / Cursor → ③ Fill in colleague info & provide source materials → ④ Generated files are in colleagues/{slug}/',
            creator_import_colleague_files: 'Upload the following files (from colleagues/{slug}/ directory):',
            creator_import_mentor_title: 'Import Mentor Skill',
            creator_import_mentor_hint: 'Generate a mentor directly from ArXiv or import artifacts from supervisor.',
            creator_generate_mentor_kicker: '📚 Direct Generate',
            creator_generate_mentor_hint: 'Enter a mentor name and ClawCross Creator will search ArXiv, build the mentor profile, and import it directly.',
            creator_arxiv_author_name_label: 'Mentor Name',
            creator_arxiv_author_name_placeholder: 'e.g. Geoffrey Hinton',
            creator_arxiv_affiliation_label: 'Affiliation (optional)',
            creator_arxiv_affiliation_placeholder: 'e.g. University of Toronto',
            creator_arxiv_max_results_label: 'Paper Limit',
            creator_generate_mentor_btn: '📚 Search ArXiv & Generate Mentor',
            creator_generate_mentor_loading: 'Searching ArXiv and generating mentor...',
            creator_generate_mentor_success: 'Mentor generated: {name} ({count} papers)',
            creator_import_mentor_repo: '📦 Repository',
            creator_import_mentor_repo_url: 'https://github.com/ybq22/supervisor',
            creator_import_mentor_steps: 'Steps: ① Clone the repo → ② Run /distill-mentor <name> in Claude Code / Cursor → ③ Auto-searches ArXiv papers & analyzes style → ④ Generated files are in ~/.claude/mentors/ and ~/.claude/skills/',
            creator_import_mentor_files: 'Upload the following files:',
            creator_import_meta_json: 'meta.json — colleague profile (required)',
            creator_import_persona_md: 'persona.md — 5-layer personality (required)',
            creator_import_work_md: 'work.md — work capabilities (optional, recommended)',
            creator_import_or_local_path: 'Or provide a local path:',
            creator_import_colleague_dir: 'colleagues/{slug} directory (auto-reads meta.json / persona.md / work.md)',
            creator_import_colleague_dir_placeholder: 'e.g. ~/.claude/skills/create-colleague/colleagues/zhangsan',
            creator_import_mentor_json: '{name}.json — mentor profile JSON (required)',
            creator_import_skill_md: 'SKILL.md — full mentor skill (optional, recommended)',
            creator_import_mentor_json_path: '{name}.json path (required if not uploaded)',
            creator_import_mentor_json_path_placeholder: 'e.g. ~/.claude/mentors/Geoffrey_Hinton.json',
            creator_import_skill_md_path: 'SKILL.md path (optional)',
            creator_import_skill_md_path_placeholder: 'e.g. ~/.claude/skills/geoffrey-hinton/SKILL.md',
            creator_import_btn: '📥 Import to ClawCross Creator',
            creator_import_success: 'Import successful!',
            creator_import_error: 'Import failed',
            creator_step2_manual_kicker: 'Step 2 · Manual Setup',
            creator_step2_manual_title: 'Define Team Roles',
            creator_roles_hint: 'Add the roles in your team. Each role includes a name, responsibilities, and dependencies.',
            creator_add_role_btn: '+ Add Role',
            creator_paste_json_btn: 'Paste JSON',
            creator_step2_discovery_kicker: 'Step 2 · AI + TinyFish Discovery',
            creator_step2_discovery_title: '🔍 AI Discovery',
            creator_discovery_hint: 'The AI Agent first finds SOP and org-structure URLs relevant to your task, then TinyFish crawls those pages to extract role data. You can watch the browser actions during extraction.',
            creator_discover_btn: '🚀 Start Discovery',
            creator_discover_stop_btn: '⏹ Stop',
            creator_phase_discovery: 'AI Finds URLs',
            creator_phase_extraction: '🐟 TinyFish Extracts Roles',
            creator_phase_confirm: 'Confirm Roles',
            creator_discovery_log_title: '🔍 AI URL Discovery Log',
            creator_discovery_log_idle: 'Waiting to start discovery...',
            creator_extraction_tabs_title: '🐟 TinyFish Parallel Extraction',
            creator_extraction_summary_title: '📊 Extraction Summary',
            creator_discovered_roles_title: '✅ Discovered Roles',
            creator_roles_count_zero: '0 roles',
            creator_discovery_mode_llm: '🤖 LLM Smart Select',
            creator_discovery_mode_manual: '✋ Manual Select',
            creator_max_roles_label: 'Max Roles',
            creator_discovery_smart_select_btn: '🤖 Run Smart Select',
            creator_max_roles_hint: 'The LLM will choose the most important N roles and auto-match preset experts.',
            creator_discovery_manual_hint: '💡 Use the checkboxes on role cards to select what you need. No quantity limit.',
            creator_selected_count_zero: 'Selected 0',
            creator_select_all: 'Select All',
            creator_deselect_all: 'Clear All',
            creator_use_selected_roles: '✅ Use Selected Roles',
            creator_add_discovered_role: '+ Add Role',
            creator_step3_title: 'Team Preview',
            creator_build_btn: '🔨 Build Team',
            creator_download_btn: '📦 Download ZIP',
            creator_import_btn: '📥 Import to Clawcross',
            creator_workflow_dag_title: 'OASIS Workflow DAG',
            creator_zoom_out: 'Zoom out',
            creator_zoom_in: 'Zoom in',
            creator_reset_view: 'Reset view',
            creator_view_yaml: 'View YAML Source',
            creator_tinyfish_status_title: 'Crawler Status',
            creator_tinyfish_status_loading: 'Checking TinyFish config...',
            creator_expert_pool_title: 'Preset Expert Pool',
            creator_expert_pool_hint: 'Click an expert to add it as a team role quickly. This reuses the same expert source as the Message Center contacts.',
            creator_expert_pool_search_placeholder: '🔍 Search expert name / category...',
            creator_expert_pool_loading: 'Loading expert pool...',
            creator_build_jobs_kicker: 'Build Jobs',
            creator_build_jobs_title: 'Build History',
            creator_jobs_empty: 'No build history yet',
            creator_cat_public: 'Public Experts',
            creator_cat_design: 'Design',
            creator_cat_engineering: 'Engineering',
            creator_cat_marketing: 'Marketing',
            creator_cat_product: 'Product',
            creator_cat_project_management: 'Project Management',
            creator_cat_spatial_computing: 'Spatial Computing',
            creator_cat_specialized: 'Specialized',
            creator_cat_support: 'Support',
            creator_cat_testing: 'Testing',
            creator_cat_custom: 'Custom Experts',
            creator_no_roles_hint: 'No roles yet. Add preset experts from the pool on the right, or click "+ Add Role" to create one manually.',
            creator_role_preset_title: 'Preset expert. The full Persona will be used during build.',
            creator_role_persona_label: '📄 Persona',
            creator_role_persona_note: '(the full version used in build)',
            creator_role_traits_label: 'Traits (comma separated)',
            creator_role_responsibilities_label: 'Responsibilities (comma separated)',
            creator_role_depends_on_label: 'Dependencies (comma separated)',
            creator_role_tools_label: 'Tools (comma separated)',
            creator_role_name_placeholder: 'Role name',
            creator_role_traits_placeholder: 'e.g. data-driven, strategic',
            creator_role_responsibilities_placeholder: 'e.g. define strategy, monitor metrics',
            creator_role_depends_on_placeholder: 'e.g. Growth Lead, Product Manager',
            creator_role_depends_on_single_placeholder: 'e.g. Growth Lead',
            creator_role_tools_placeholder: 'e.g. Python, SQL, Notion',
            creator_role_remove_title: 'Remove',
            creator_no_discovered_roles: 'No roles discovered',
            creator_preset_match_title: 'Semantically matched to a preset expert. The preset Persona will be used during build.',
            creator_preset_match_label: '🌟 Match',
            creator_role_count: '{count} roles',
            creator_selected_count: 'Selected {count} / {total}',
            creator_json_modal_title: 'Paste Role JSON',
            creator_json_modal_hint: 'Paste a JSON array. Each object should contain role_name, personality_traits, primary_responsibilities, depends_on, and tools_used.',
            creator_json_modal_placeholder: '[{"role_name": "Product Manager", "personality_traits": ["logical"], "primary_responsibilities": ["requirements analysis"], "depends_on": [], "tools_used": ["Figma"]}]',
            creator_json_modal_cancel: 'Cancel',
            creator_json_modal_import: 'Import',
            creator_open_live_watch: '🔗 Open Live Watch',
            creator_page_fallback: 'Page {index}',
            creator_session_waiting: 'Waiting',
            creator_session_waiting_tinyfish: 'Waiting for TinyFish to start...',
            creator_session_preparing: 'Preparing extraction',
            creator_discovered_pages_title: '📄 Discovered Pages ({count})',
            creator_build_summary_title: 'BUILD SUMMARY',
            creator_build_summary_total_roles: 'Total Roles',
            creator_build_summary_workflow_nodes: 'Workflow Nodes',
            creator_build_summary_selectors: 'Selectors',
            creator_build_summary_review_loops: 'Review Loops',
            creator_build_summary_preset_matched: 'Preset Matched',
            creator_build_summary_custom_generated: 'Custom Generated',
            creator_build_summary_custom_persona: 'Custom Persona',
            creator_build_summary_dag_enhanced: 'DAG Enhanced',
            creator_build_summary_team_name: 'Team Name',
            creator_build_summary_workflow_mode: 'Workflow Mode',
            creator_persona_missing_preview: 'No short description was extracted. The Persona below will be used directly.',
            creator_persona_origin_preset_hint: 'This currently comes from a preset Persona. You can override it here to better fit the team.',
            creator_persona_origin_generated_hint: 'This currently comes from an AI-generated Persona. It is a good place to refine tone, scope, and delivery standards.',
            creator_persona_origin_preset_label: 'Preset Persona · Editable override',
            creator_persona_origin_generated_label: 'Generated Persona · Manual refinement recommended',
            creator_chars_label: '{count} chars',
            creator_expert_pool_empty: 'No experts available',
            creator_expert_pool_load_failed: 'Load failed',
            creator_expert_pool_no_match: 'No matching experts',
            creator_expert_pool_add_title: 'Add as role',
            creator_tinyfish_state_ready: 'Status: ✅ Ready',
            creator_tinyfish_state_missing: 'Status: ⚠️ Not Configured',
            creator_tinyfish_base_url_label: 'Base URL',
            creator_tinyfish_recent_runs_label: 'Recent Runs',
            creator_tinyfish_mode_disabled: 'TinyFish API Key not configured',
            creator_tinyfish_status_load_failed: 'Failed to load TinyFish status: {error}',
            creator_default_team_name_prefix: 'new_team_',
            creator_job_status_complete: 'Build Complete',
            creator_job_status_failed: 'Build Failed',
            creator_job_status_running: 'Building',
            creator_job_status_pending: 'Queued',
            creator_job_meta_roles: '{count} roles · {time}',
            creator_job_meta_empty: 'No result yet · {time}',
            creator_jobs_load_failed: 'Failed to load build history: {error}',
            creator_jobs_roles_suffix: 'roles',
            creator_example_team_name: 'SaaS Growth Team',
            creator_example_task_desc: 'Own the full B2B SaaS lifecycle from user acquisition to renewal across acquisition, activation, retention, monetization, and referral, with data-driven decisions plus content marketing and product-led growth.'
        }
    };

    function normalizeLang(rawLang) {
        var lang = String(rawLang || '').trim().toLowerCase();
        if (!lang) return 'zh-CN';
        if (lang === 'en' || lang.indexOf('en-') === 0) return 'en';
        return 'zh-CN';
    }

    function readCurrentLang() {
        var primary = normalizeLang(window.localStorage.getItem('lang'));
        if (primary && CREATOR_I18N[primary]) return primary;
        var secondary = normalizeLang(window.localStorage.getItem('clawcross_lang'));
        if (secondary && CREATOR_I18N[secondary]) return secondary;
        var browserLang = normalizeLang((navigator.language || navigator.userLanguage || '').trim());
        return CREATOR_I18N[browserLang] ? browserLang : 'zh-CN';
    }

    var currentLang = readCurrentLang();

    function persistLang(lang) {
        var normalized = normalizeLang(lang);
        window.localStorage.setItem('lang', normalized);
        window.localStorage.setItem('clawcross_lang', normalized === 'en' ? 'en' : 'zh');
    }

    function t(key, vars) {
        var dict = CREATOR_I18N[currentLang] || CREATOR_I18N['zh-CN'];
        var fallback = CREATOR_I18N['zh-CN'];
        var text = dict[key];
        if (text == null) text = fallback[key];
        if (text == null) text = key;
        if (vars) {
            Object.keys(vars).forEach(function (name) {
                text = text.replace(new RegExp('\\{' + name + '\\}', 'g'), vars[name]);
            });
        }
        return text;
    }

    function isZhLang() {
        return currentLang === 'zh-CN';
    }

    function getLocalizedExpertName(expert) {
        if (!expert) return '';
        return (isZhLang() ? (expert.name_zh || expert.name) : (expert.name_en || expert.name)) || expert.tag || '';
    }

    function getLocalizedExpertSummary(expert) {
        if (!expert) return '';
        if (isZhLang()) {
            return compactText(expert.description_zh || expert.description || getPersonaPreview(expert));
        }
        return compactText(expert.description_en || expert.description || getPersonaPreview(expert));
    }

    var dynamicTranslationCache = {};
    var dynamicTranslationPending = {};

    function dynamicTranslationKey(text, context, lang) {
        return (lang || currentLang) + '::' + (context || 'general') + '::' + String(text || '');
    }

    function shouldTranslateDynamicText(text, context, lang) {
        var source = compactText(text);
        if (!source) return false;
        if (/^https?:\/\//i.test(source)) return false;
        if (/^[\d\s.,:;!?()[\]{}%/+*=#@&_\\-]+$/.test(source)) return false;
        if (/^[A-Za-z0-9_.-]{1,32}$/.test(source) && (context === 'tag' || context === 'id')) return false;
        var hasHan = /[\u4e00-\u9fff]/.test(source);
        var hasLatin = /[A-Za-z]/.test(source);
        var target = normalizeLang(lang || currentLang);
        if (target === 'en') return hasHan;
        if (hasHan) return false;
        return hasLatin;
    }

    async function requestDynamicTranslation(text, context, lang) {
        var source = String(text == null ? '' : text);
        var normalizedLang = normalizeLang(lang || currentLang);
        if (!shouldTranslateDynamicText(source, context, normalizedLang)) return source;
        var cacheKey = dynamicTranslationKey(source, context, normalizedLang);
        if (dynamicTranslationCache[cacheKey] != null) return dynamicTranslationCache[cacheKey];
        if (dynamicTranslationPending[cacheKey]) return dynamicTranslationPending[cacheKey];

        dynamicTranslationPending[cacheKey] = fetch('/api/team-creator/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                texts: [source],
                target_lang: normalizedLang === 'en' ? 'en' : 'zh',
                context: context || 'general',
            }),
        }).then(function (resp) {
            return resp.json().then(function (data) {
                if (!resp.ok || !data.ok || !Array.isArray(data.translations)) {
                    throw new Error((data && data.error) || 'translation failed');
                }
                var translated = String(data.translations[0] == null ? source : data.translations[0]);
                dynamicTranslationCache[cacheKey] = translated || source;
                return dynamicTranslationCache[cacheKey];
            });
        }).catch(function () {
            dynamicTranslationCache[cacheKey] = source;
            return source;
        }).finally(function () {
            delete dynamicTranslationPending[cacheKey];
        });

        return dynamicTranslationPending[cacheKey];
    }

    async function prefetchDynamicTranslations(texts, options) {
        var context = options && options.context || 'general';
        var normalizedLang = normalizeLang(options && options.lang || currentLang);
        var grouped = [];
        var charCount = 0;
        var seen = {};
        var changed = false;

        (texts || []).forEach(function (item) {
            var source = String(item == null ? '' : item);
            var cacheKey = dynamicTranslationKey(source, context, normalizedLang);
            if (!shouldTranslateDynamicText(source, context, normalizedLang)) return;
            if (dynamicTranslationCache[cacheKey] != null || dynamicTranslationPending[cacheKey] || seen[cacheKey]) return;
            seen[cacheKey] = true;
            if (!grouped.length || charCount + source.length > 2800 || grouped[grouped.length - 1].length >= 12) {
                grouped.push([]);
                charCount = 0;
            }
            grouped[grouped.length - 1].push(source);
            charCount += source.length;
        });

        for (var i = 0; i < grouped.length; i++) {
            var chunk = grouped[i];
            if (!chunk.length) continue;
            chunk.forEach(function (source) {
                dynamicTranslationPending[dynamicTranslationKey(source, context, normalizedLang)] = true;
            });
            try {
                var resp = await fetch('/api/team-creator/translate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        texts: chunk,
                        target_lang: normalizedLang === 'en' ? 'en' : 'zh',
                        context: context || 'general',
                    }),
                });
                var data = await resp.json().catch(function () { return {}; });
                var translations = (resp.ok && data.ok && Array.isArray(data.translations)) ? data.translations : chunk;
                for (var j = 0; j < chunk.length; j++) {
                    var key = dynamicTranslationKey(chunk[j], context, normalizedLang);
                    var translated = String(translations[j] == null ? chunk[j] : translations[j]) || chunk[j];
                    if (dynamicTranslationCache[key] !== translated) changed = true;
                    dynamicTranslationCache[key] = translated;
                    delete dynamicTranslationPending[key];
                }
            } catch (err) {
                chunk.forEach(function (source) {
                    var key = dynamicTranslationKey(source, context, normalizedLang);
                    dynamicTranslationCache[key] = source;
                    delete dynamicTranslationPending[key];
                });
            }
        }

        return changed;
    }

    function getDynamicDisplayText(text, context, lang) {
        var source = String(text == null ? '' : text);
        var normalizedLang = normalizeLang(lang || currentLang);
        if (!shouldTranslateDynamicText(source, context, normalizedLang)) return source;
        return dynamicTranslationCache[dynamicTranslationKey(source, context, normalizedLang)] || source;
    }

    function setDynamicText(el, text, options) {
        if (!el) return;
        el.dataset.tcDynamicSource = String(text == null ? '' : text);
        el.dataset.tcDynamicContext = options && options.context || 'general';
        el.dataset.tcDynamicAttr = options && options.attr || 'text';
        renderDynamicTextNode(el);
    }

    async function renderDynamicTextNode(el) {
        if (!el) return;
        var source = el.dataset.tcDynamicSource || '';
        var context = el.dataset.tcDynamicContext || 'general';
        var attr = el.dataset.tcDynamicAttr || 'text';
        var targetSource = source;
        var translated = await requestDynamicTranslation(source, context, currentLang);
        if ((el.dataset.tcDynamicSource || '') !== targetSource) return;
        if (attr === 'placeholder') el.placeholder = translated;
        else if (attr === 'title') el.title = translated;
        else el.textContent = translated;
    }

    function refreshDynamicTextNodes() {
        document.querySelectorAll('[data-tc-dynamic-source]').forEach(function (el) {
            renderDynamicTextNode(el);
        });
    }

    function applyTranslations() {
        document.documentElement.lang = currentLang;
        document.title = t('creator_page_title');

        var toggleText = $('creator-lang-toggle-text');
        if (toggleText) toggleText.textContent = currentLang === 'zh-CN' ? 'EN' : '中文';

        document.querySelectorAll('[data-i18n]').forEach(function (el) {
            var key = el.getAttribute('data-i18n');
            el.textContent = t(key);
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
            el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
        });
        document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
            el.title = t(el.getAttribute('data-i18n-title'));
        });

        refreshDynamicTextNodes();
        if (window.__ClawcrossCreatorBuilder && typeof window.__ClawcrossCreatorBuilder.refreshI18n === 'function') {
            window.__ClawcrossCreatorBuilder.refreshI18n();
        }
    }

    function toggleLanguage() {
        currentLang = currentLang === 'zh-CN' ? 'en' : 'zh-CN';
        persistLang(currentLang);
        applyTranslations();
    }

    window.ClawcrossCreatorI18n = {
        t: t,
        getLang: function () { return currentLang; },
        toggleLanguage: toggleLanguage,
        applyTranslations: applyTranslations,
    };

    // ═════════════════════════════════════════════════════
    //  UNIFIED BUILDER MODULE
    //  Three-stage pipeline: Input → Discovery/Roles → Build
    // ═════════════════════════════════════════════════════
    var BuilderModule = (function () {
        var state = {
            roles: [],
            teamConfig: null,
            selectedJobId: '',
            mode: 'direct', // 'direct' | 'discover'
            building: false,
            jobPollTimer: null,
            // Discovery state
            discoverController: null,
            discoverPhase: 'idle', // idle | discovery | extraction | confirm
            discoverStopRequested: false,
            discoveredPages: [],
            discoveredRoles: [],
            tinyfishReady: false,
            ui: {
                builderPillTone: 'muted',
                builderStatusTone: 'muted',
                previewStatusTone: 'muted',
            },
        };
        var CREATOR_SESSION_KEY = 'clawcross_creator_session_v1';
        var persistTimer = null;
        var suppressPersistence = false;
        var extractionTabBarBound = false;

        // ── Avatar colors ──
        var AVATAR_COLORS = [
            '#2563eb', '#7c3aed', '#db2777', '#ea580c',
            '#059669', '#0891b2', '#4f46e5', '#b91c1c',
            '#65a30d', '#0d9488', '#c026d3', '#d97706',
        ];

        function avatarColor(index) {
            return AVATAR_COLORS[index % AVATAR_COLORS.length];
        }

        function avatarLetter(name) {
            return (name || '?').charAt(0).toUpperCase();
        }

        // ── Max Roles Limit ──
        var DEFAULT_MAX_ROLES = 8;

        function getMaxRoles() {
            var el = $('builder-max-roles');
            if (!el) return DEFAULT_MAX_ROLES;
            var val = parseInt(el.value, 10);
            if (isNaN(val) || val < 1) return DEFAULT_MAX_ROLES;
            if (val > 30) return 30;
            return val;
        }

        function clipPersistedString(value, maxLen) {
            var text = String(value || '');
            if (!maxLen || text.length <= maxLen) return text;
            return text.slice(text.length - maxLen);
        }

        function readSessionSnapshot() {
            try {
                var raw = window.sessionStorage.getItem(CREATOR_SESSION_KEY);
                return raw ? JSON.parse(raw) : null;
            } catch (err) {
                return null;
            }
        }

        function writeSessionSnapshot(snapshot) {
            try {
                if (!snapshot) {
                    window.sessionStorage.removeItem(CREATOR_SESSION_KEY);
                    return;
                }
                window.sessionStorage.setItem(CREATOR_SESSION_KEY, JSON.stringify(snapshot));
            } catch (err) {
                console.warn('[ClawcrossCreator] session persistence failed:', err);
            }
        }

        function snapshotLogElement(el, maxLen) {
            if (!el) return { html: '', empty: true };
            return {
                html: clipPersistedString(el.innerHTML || '', maxLen || 12000),
                empty: el.dataset.empty !== '0',
            };
        }

        function restoreLogElement(el, snapshot) {
            if (!el) return;
            var snap = snapshot || {};
            el.innerHTML = snap.html || '';
            el.dataset.empty = snap.empty ? '1' : '0';
        }

        function hasRestorableData(snapshot) {
            if (!snapshot || typeof snapshot !== 'object') return false;
            return !!(
                snapshot.teamName ||
                snapshot.taskDescription ||
                (snapshot.roles && snapshot.roles.length) ||
                (snapshot.discoveredPages && snapshot.discoveredPages.length) ||
                (snapshot.discoveredRoles && snapshot.discoveredRoles.length) ||
                snapshot.teamConfig ||
                (snapshot.logs && (
                    (snapshot.logs.discovery && snapshot.logs.discovery.html) ||
                    (snapshot.logs.extraction && snapshot.logs.extraction.html)
                ))
            );
        }

        // ── Expert Pool (loaded from /proxy_visual/experts — same source as 消息中心通讯录) ──
        var expertPoolCache = [];  // Full expert list from server
        var expertPoolByCat = {};  // Grouped by category

        // Category label mapping — mirrors 消息中心通讯录的 _catLabels
        var CAT_LABELS = {
            '_public':            { icon: '🌟', labelKey: 'creator_cat_public' },
            'design':             { icon: '🎨', labelKey: 'creator_cat_design' },
            'engineering':        { icon: '⚙️', labelKey: 'creator_cat_engineering' },
            'marketing':          { icon: '📢', labelKey: 'creator_cat_marketing' },
            'product':            { icon: '📦', labelKey: 'creator_cat_product' },
            'project-management': { icon: '📋', labelKey: 'creator_cat_project_management' },
            'spatial-computing':  { icon: '🥽', labelKey: 'creator_cat_spatial_computing' },
            'specialized':        { icon: '🔬', labelKey: 'creator_cat_specialized' },
            'support':            { icon: '🛡️', labelKey: 'creator_cat_support' },
            'testing':            { icon: '🧪', labelKey: 'creator_cat_testing' },
            '_custom':            { icon: '🛠️', labelKey: 'creator_cat_custom' },
        };

        function getExpertPoolFilterValue() {
            var input = $('expert-pool-search');
            return input ? String(input.value || '') : '';
        }

        function matchesExpertPoolFilter(expert, filterLower) {
            if (!filterLower) return true;
            var searchable = [
                expert.name || '', expert.name_zh || '', expert.name_en || '',
                expert.tag || '', expert.category || '',
                expert.description || '', expert.description_zh || '', expert.description_en || '',
                getPersonaPreview(expert) || '',
            ].join(' ').toLowerCase();
            return searchable.indexOf(filterLower) !== -1;
        }

        // ── Status helpers ──
        function setBuilderPill(text, tone) {
            var el = $('builder-status-pill');
            if (!el) return;
            var presets = {
                muted: ['rgba(37, 99, 235, 0.10)', '#1d4ed8'],
                success: ['rgba(16, 185, 129, 0.14)', '#047857'],
                warning: ['rgba(245, 158, 11, 0.16)', '#b45309'],
                error: ['rgba(239, 68, 68, 0.16)', '#b91c1c'],
                running: ['rgba(59, 130, 246, 0.16)', '#1d4ed8'],
            };
            var p = presets[tone] || presets.muted;
            state.ui.builderPillTone = tone || 'muted';
            setDynamicText(el, text, { context: 'status' });
            el.style.background = p[0];
            el.style.color = p[1];
            schedulePersistBuilderState();
        }

        function setBuilderStatus(text, tone) {
            var el = $('builder-status-text');
            if (!el) return;
            var colors = {
                muted: 'rgba(226, 232, 240, 0.84)',
                success: '#86efac',
                warning: '#fcd34d',
                error: '#fca5a5',
                running: '#93c5fd',
            };
            state.ui.builderStatusTone = tone || 'muted';
            setDynamicText(el, text, { context: 'status' });
            el.style.color = colors[tone] || colors.muted;
            schedulePersistBuilderState();
        }

        function setPreviewStatus(text, tone) {
            var el = $('builder-preview-status');
            if (!el) return;
            var colors = { muted: '#516176', success: '#047857', warning: '#b45309', error: '#b91c1c', running: '#2563eb' };
            state.ui.previewStatusTone = tone || 'muted';
            setDynamicText(el, text, { context: 'status' });
            el.style.color = colors[tone] || colors.muted;
            schedulePersistBuilderState();
        }

        function localizeDynamicList(items, context) {
            return (items || []).map(function (item) {
                return getDynamicDisplayText(item, context);
            });
        }

        async function prefetchRoleTranslations(roles) {
            var texts = [];
            (roles || []).forEach(function (role) {
                if (!role) return;
                if (role.role_name) texts.push(role.role_name);
                (role.personality_traits || []).forEach(function (item) { texts.push(item); });
                (role.primary_responsibilities || []).forEach(function (item) { texts.push(item); });
                (role.depends_on || []).forEach(function (item) { texts.push(item); });
                (role.tools_used || []).forEach(function (item) { texts.push(item); });
                if (role._full_persona) texts.push(role._full_persona);
                else if (role._persona_preview) texts.push(role._persona_preview);
            });
            return prefetchDynamicTranslations(texts, { context: 'role' });
        }

        // ── Role Editor ──
        function renderRoles() {
            var container = $('builder-roles-list');
            if (!container) return;

            state.roles = hydratePresetRoles(state.roles);

            prefetchRoleTranslations(state.roles).then(function (changed) {
                if (changed) renderRoles();
            });

            if (!state.roles.length) {
                container.innerHTML = '<div class="builder-hint" style="text-align:center;padding:24px 0;">' + escapeHtml(t('creator_no_roles_hint')) + '</div>';
                schedulePersistBuilderState();
                return;
            }

            container.innerHTML = state.roles.map(function (role, i) {
                var isPreset = role._expert_tag || role._expert_source;
                var presetBadge = isPreset
                    ? '<span class="builder-role-preset-badge" title="' + escapeHtml(t('creator_role_preset_title')) + '">🌟 ' + escapeHtml(role._expert_tag || '') + '</span>'
                    : '';

                // Preset experts: show a single persona textarea (read-only by default)
                // Manual roles: show the four editable field inputs
                var bodyHtml;
                if (isPreset) {
                    var fullPersona = getDynamicDisplayText(role._full_persona || role._persona_preview || '', 'role');
                    bodyHtml =
                        '<div class="builder-role-persona-full">' +
                            '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_persona_label')) + ' <span style="font-weight:400;color:var(--creator-muted);">' + escapeHtml(t('creator_role_persona_note')) + '</span></div>' +
                            '<textarea class="builder-role-persona-textarea" data-field="persona" readonly rows="5">' + escapeHtml(fullPersona) + '</textarea>' +
                        '</div>';
                } else {
                    bodyHtml =
                        '<div class="builder-role-fields">' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_traits_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.personality_traits, 'role').join(', ')) + '" data-field="personality_traits" placeholder="' + escapeHtml(t('creator_role_traits_placeholder')) + '">' +
                            '</div>' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_responsibilities_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.primary_responsibilities, 'role').join(', ')) + '" data-field="primary_responsibilities" placeholder="' + escapeHtml(t('creator_role_responsibilities_placeholder')) + '">' +
                            '</div>' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_depends_on_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.depends_on, 'role').join(', ')) + '" data-field="depends_on" placeholder="' + escapeHtml(t('creator_role_depends_on_placeholder')) + '">' +
                            '</div>' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_tools_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.tools_used, 'role').join(', ')) + '" data-field="tools_used" placeholder="' + escapeHtml(t('creator_role_tools_placeholder')) + '">' +
                            '</div>' +
                        '</div>';
                }

                return (
                    '<div class="builder-role-card' + (isPreset ? ' builder-role-card-preset' : '') + '" data-role-idx="' + i + '" data-expert-tag="' + escapeHtml(role._expert_tag || '') + '" data-expert-source="' + escapeHtml(role._expert_source || '') + '">' +
                        '<div class="builder-role-header">' +
                            '<div class="builder-role-num">' + (i + 1) + '</div>' +
                            '<input type="text" class="builder-role-name" value="' + escapeHtml(getDynamicDisplayText(role.role_name, 'role')) + '" placeholder="' + escapeHtml(t('creator_role_name_placeholder')) + '" data-field="role_name">' +
                            presetBadge +
                            '<button class="builder-role-remove" type="button" title="' + escapeHtml(t('creator_role_remove_title')) + '">×</button>' +
                        '</div>' +
                        bodyHtml +
                    '</div>'
                );
            }).join('');
            schedulePersistBuilderState();
        }

        // ── Discovery Role Selection State ──
        var discoverySelectMode = 'llm'; // 'llm' | 'manual'
        var discoverySelected = {};      // { index: true/false }
        var discoveryPresetMatches = {}; // { index: { matched_tag, matched_name, confidence } }

        // Render roles in discovery review panel with checkbox selection
        function renderDiscoveryRoles() {
            var container = $('discovery-roles-list');
            if (!container) return;
            var roles = state.discoveredRoles;

            prefetchRoleTranslations(roles).then(function (changed) {
                if (changed) renderDiscoveryRoles();
            });

            if (!roles.length) {
                container.innerHTML = '<div class="builder-hint" style="text-align:center;padding:24px 0;">' + escapeHtml(t('creator_no_discovered_roles')) + '</div>';
                updateDiscoverySelectSummary();
                schedulePersistBuilderState();
                return;
            }

            container.innerHTML = roles.map(function (role, i) {
                var isSelected = discoverySelected[i] === true;
                var presetMatch = discoveryPresetMatches[i];
                var presetBadge = '';
                if (presetMatch) {
                    var matchedName = getDynamicDisplayText(presetMatch.matched_name || presetMatch.matched_tag, 'role');
                    presetBadge =
                        '<span class="discovery-preset-match-badge" title="' + escapeHtml(t('creator_preset_match_title')) + '">' +
                            escapeHtml(t('creator_preset_match_label')) + ': ' + escapeHtml(matchedName) +
                            (presetMatch.confidence ? ' (' + Math.round(presetMatch.confidence * 100) + '%)' : '') +
                        '</span>';
                }

                return (
                    '<div class="builder-role-card discovery-role-selectable' + (isSelected ? ' discovery-role-selected' : '') + '" data-role-idx="' + i + '">' +
                        '<div class="builder-role-header">' +
                            '<label class="discovery-role-checkbox-wrap">' +
                                '<input type="checkbox" class="discovery-role-checkbox" data-role-idx="' + i + '"' + (isSelected ? ' checked' : '') + '>' +
                                '<span class="discovery-role-checkmark"></span>' +
                            '</label>' +
                            '<div class="builder-role-num">' + (i + 1) + '</div>' +
                            '<input type="text" class="builder-role-name" value="' + escapeHtml(getDynamicDisplayText(role.role_name, 'role')) + '" placeholder="' + escapeHtml(t('creator_role_name_placeholder')) + '" data-field="role_name">' +
                            presetBadge +
                            '<button class="builder-role-remove" type="button" title="' + escapeHtml(t('creator_role_remove_title')) + '">×</button>' +
                        '</div>' +
                        '<div class="builder-role-fields">' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_traits_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.personality_traits, 'role').join(', ')) + '" data-field="personality_traits" placeholder="' + escapeHtml(t('creator_role_traits_placeholder')) + '">' +
                            '</div>' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_responsibilities_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.primary_responsibilities, 'role').join(', ')) + '" data-field="primary_responsibilities" placeholder="' + escapeHtml(t('creator_role_responsibilities_placeholder')) + '">' +
                            '</div>' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_depends_on_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.depends_on, 'role').join(', ')) + '" data-field="depends_on" placeholder="' + escapeHtml(t('creator_role_depends_on_single_placeholder')) + '">' +
                            '</div>' +
                            '<div>' +
                                '<div class="builder-role-field-label">' + escapeHtml(t('creator_role_tools_label')) + '</div>' +
                                '<input class="builder-role-field-input" type="text" value="' + escapeHtml(localizeDynamicList(role.tools_used, 'role').join(', ')) + '" data-field="tools_used" placeholder="' + escapeHtml(t('creator_role_tools_placeholder')) + '">' +
                            '</div>' +
                        '</div>' +
                    '</div>'
                );
            }).join('');

            var countEl = $('discovery-roles-count');
            if (countEl) countEl.textContent = t('creator_role_count', { count: roles.length });
            updateDiscoverySelectSummary();
            schedulePersistBuilderState();
        }

        function updateDiscoverySelectSummary() {
            var summaryEl = $('discovery-select-summary');
            var countEl = $('discovery-selected-count');
            if (!summaryEl || !countEl) return;
            var total = state.discoveredRoles.length;
            if (!total) { summaryEl.style.display = 'none'; return; }
            summaryEl.style.display = '';
            var count = 0;
            for (var k in discoverySelected) {
                if (discoverySelected[k]) count++;
            }
            countEl.textContent = t('creator_selected_count', { count: count, total: total });
        }

        function toggleDiscoveryRoleSelect(idx) {
            discoverySelected[idx] = !discoverySelected[idx];
            var card = document.querySelector('#discovery-roles-list .builder-role-card[data-role-idx="' + idx + '"]');
            if (card) {
                card.classList.toggle('discovery-role-selected', discoverySelected[idx]);
                var cb = card.querySelector('.discovery-role-checkbox');
                if (cb) cb.checked = discoverySelected[idx];
            }
            updateDiscoverySelectSummary();
            schedulePersistBuilderState();
        }

        function selectAllDiscoveryRoles() {
            for (var i = 0; i < state.discoveredRoles.length; i++) {
                discoverySelected[i] = true;
            }
            renderDiscoveryRoles();
        }

        function deselectAllDiscoveryRoles() {
            discoverySelected = {};
            renderDiscoveryRoles();
        }

        function setDiscoverySelectMode(mode) {
            discoverySelectMode = mode;
            var llmBtn = $('discovery-mode-llm');
            var manualBtn = $('discovery-mode-manual');
            var llmControls = $('discovery-llm-controls');
            var manualHint = $('discovery-manual-hint');
            if (llmBtn) llmBtn.classList.toggle('discovery-mode-active', mode === 'llm');
            if (manualBtn) manualBtn.classList.toggle('discovery-mode-active', mode === 'manual');
            if (llmControls) llmControls.style.display = mode === 'llm' ? '' : 'none';
            if (manualHint) manualHint.style.display = mode === 'manual' ? '' : 'none';
            schedulePersistBuilderState();
        }

        function addEmptyRole() {
            state.roles = collectRolesFromUI('builder-roles-list');
            state.roles.push({
                role_name: '',
                personality_traits: [],
                primary_responsibilities: [],
                depends_on: [],
                tools_used: [],
            });
            renderRoles();
            var cards = document.querySelectorAll('#builder-roles-list .builder-role-card');
            if (cards.length) {
                var last = cards[cards.length - 1];
                var nameInput = last.querySelector('.builder-role-name');
                if (nameInput) nameInput.focus();
            }
        }

        function findExpertByTag(tag) {
            var key = String(tag || '').trim();
            if (!key) return null;

            if (state.teamConfig && Array.isArray(state.teamConfig.oasis_experts)) {
                for (var i = 0; i < state.teamConfig.oasis_experts.length; i++) {
                    var teamExpert = state.teamConfig.oasis_experts[i];
                    if (teamExpert && String(teamExpert.tag || '').trim() === key) return teamExpert;
                }
            }

            for (var j = 0; j < expertPoolCache.length; j++) {
                var poolExpert = expertPoolCache[j];
                if (poolExpert && String(poolExpert.tag || '').trim() === key) return poolExpert;
            }

            return null;
        }

        function hydratePresetRole(role) {
            if (!role || !role._expert_tag) return role;
            var expert = findExpertByTag(role._expert_tag);
            if (!expert) return role;

            if (!role._expert_source && expert.source) {
                role._expert_source = expert.source;
            }
            if (!role._full_persona) {
                role._full_persona = getExpertFullPersona(expert) || '';
            }
            if (!role._persona_preview) {
                role._persona_preview = getPersonaPreview(expert) || (role._full_persona ? role._full_persona.slice(0, 200) : '');
            }
            return role;
        }

        function hydratePresetRoles(roles) {
            return (roles || []).map(function (role) {
                return hydratePresetRole(role);
            });
        }

        function refreshPresetRolesFromExpertPool() {
            var currentRoles = collectRolesFromUI('builder-roles-list');
            var currentDiscoveredRoles = collectRolesFromUI('discovery-roles-list');
            var builderRoles = currentRoles.length ? currentRoles : (state.roles || []);
            var discoveryRoles = currentDiscoveredRoles.length ? currentDiscoveredRoles : (state.discoveredRoles || []);
            var builderNeedsRefresh = builderRoles.some(function (role) {
                return !!(role && role._expert_tag);
            });
            var discoveryNeedsRefresh = discoveryRoles.some(function (role) {
                return !!(role && role._expert_tag);
            });

            if (builderNeedsRefresh) {
                state.roles = hydratePresetRoles(builderRoles);
                renderRoles();
            }
            if (discoveryNeedsRefresh) {
                state.discoveredRoles = hydratePresetRoles(discoveryRoles);
                renderDiscoveryRoles();
            }
        }

        function collectRolesFromUI(containerId) {
            var container = $(containerId || 'builder-roles-list');
            if (!container) return [];
            var sourceRoles = containerId === 'discovery-roles-list' ? state.discoveredRoles : state.roles;
            var cards = container.querySelectorAll('.builder-role-card');
            var roles = [];
            cards.forEach(function (card) {
                var nameInput = card.querySelector('[data-field="role_name"]');
                var name = nameInput ? nameInput.value.trim() : '';
                if (!name) return;

                function getList(field) {
                    var input = card.querySelector('[data-field="' + field + '"]');
                    if (!input) return [];
                    return input.value.split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
                }

                var role = {
                    role_name: name,
                    personality_traits: getList('personality_traits'),
                    primary_responsibilities: getList('primary_responsibilities'),
                    depends_on: getList('depends_on'),
                    tools_used: getList('tools_used'),
                };

                // Preserve preset expert metadata from data attributes
                var expertTag = card.dataset.expertTag;
                var expertSource = card.dataset.expertSource;
                if (expertTag) role._expert_tag = expertTag;
                if (expertSource) role._expert_source = expertSource;

                // Preserve full persona from state (for preset experts)
                var idx = parseInt(card.dataset.roleIdx, 10);
                if (!isNaN(idx) && sourceRoles[idx]) {
                    if (sourceRoles[idx]._full_persona) {
                        role._full_persona = sourceRoles[idx]._full_persona;
                    }
                    if (sourceRoles[idx]._persona_preview) {
                        role._persona_preview = sourceRoles[idx]._persona_preview;
                    }
                    if (sourceRoles[idx]._expert_source && !role._expert_source) {
                        role._expert_source = sourceRoles[idx]._expert_source;
                    }
                }

                roles.push(hydratePresetRole(role));
            });
            return roles;
        }

        function serializeBuilderState() {
            var currentRoles = collectRolesFromUI('builder-roles-list');
            var currentDiscoveredRoles = collectRolesFromUI('discovery-roles-list');
            var summaryEl = $('builder-summary');
            var workflowEl = $('builder-workflow');
            var llmStatusEl = $('discovery-llm-status');
            return {
                version: 1,
                savedAt: Date.now(),
                selectedJobId: state.selectedJobId || '',
                teamName: (($('builder-team-name') || {}).value || '').trim(),
                taskDescription: (($('builder-task-desc') || {}).value || '').trim(),
                maxRoles: getMaxRoles(),
                mode: state.mode,
                discoverPhase: state.discoverPhase,
                discoveryMode: discoverySelectMode,
                hadInFlightWork: !!state.discoverController || state.building || extractionSessions.some(function (session) {
                    return session.status === 'running' || !!session.abortController;
                }),
                roles: currentRoles.length ? currentRoles : (state.roles || []),
                discoveredPages: state.discoveredPages || [],
                discoveredRoles: currentDiscoveredRoles.length ? currentDiscoveredRoles : (state.discoveredRoles || []),
                discoverySelected: discoverySelected,
                discoveryPresetMatches: discoveryPresetMatches,
                teamConfig: state.teamConfig || null,
                statuses: {
                    builderPillText: (($('builder-status-pill') || {}).textContent || '').trim(),
                    builderPillTone: state.ui.builderPillTone || 'muted',
                    builderStatusText: (($('builder-status-text') || {}).textContent || '').trim(),
                    builderStatusTone: state.ui.builderStatusTone || 'muted',
                    previewStatusText: (($('builder-preview-status') || {}).textContent || '').trim(),
                    previewStatusTone: state.ui.previewStatusTone || 'muted',
                },
                sections: {
                    discoveryLogVisible: !!($('discovery-log-section') && $('discovery-log-section').style.display !== 'none'),
                    liveVisible: !!($('discovery-live-section') && $('discovery-live-section').style.display !== 'none'),
                    extractionVisible: !!($('discovery-extraction-progress') && $('discovery-extraction-progress').style.display !== 'none'),
                    reviewVisible: !!($('discovery-roles-review') && $('discovery-roles-review').style.display !== 'none'),
                    summaryVisible: !!(summaryEl && !summaryEl.classList.contains('builder-hidden')),
                    workflowVisible: !!(workflowEl && !workflowEl.classList.contains('builder-hidden')),
                },
                logs: {
                    discovery: snapshotLogElement($('discovery-live-log'), 20000),
                    extraction: snapshotLogElement($('discovery-extraction-log'), 16000),
                    llmStatus: llmStatusEl ? {
                        display: llmStatusEl.style.display || '',
                        text: llmStatusEl.textContent || '',
                        className: llmStatusEl.className || '',
                    } : null,
                },
                extractionSessions: extractionSessions.map(function (session) {
                    return {
                        idx: session.idx,
                        url: session.url,
                        title: session.title,
                        status: session.status,
                        statusLabel: session.statusEl ? session.statusEl.textContent : session.status,
                        roles: session.roles || [],
                        watchHtml: clipPersistedString((session.watchEl && session.watchEl.innerHTML) || '', 2000),
                        previewUrl: session.iframeEl ? (session.iframeEl.getAttribute('src') || '') : '',
                        log: snapshotLogElement(session.logEl, 16000),
                    };
                }),
                activeTabIdx: activeTabIdx,
                buttons: {
                    downloadDisabled: !!($('builder-download-btn') && $('builder-download-btn').disabled),
                    importDisabled: !!($('builder-import-btn') && $('builder-import-btn').disabled),
                },
            };
        }

        function persistBuilderStateNow() {
            if (suppressPersistence) return;
            if (persistTimer) {
                clearTimeout(persistTimer);
                persistTimer = null;
            }
            var snapshot = serializeBuilderState();
            if (hasRestorableData(snapshot)) {
                writeSessionSnapshot(snapshot);
            } else {
                writeSessionSnapshot(null);
            }
        }

        function schedulePersistBuilderState() {
            if (suppressPersistence) return;
            if (persistTimer) clearTimeout(persistTimer);
            persistTimer = setTimeout(persistBuilderStateNow, 120);
        }

        function clearTeamPreview() {
            var summaryEl = $('builder-summary');
            var gridEl = $('builder-persona-grid');
            var workflowEl = $('builder-workflow');
            var yamlEl = $('builder-yaml-code');
            if (summaryEl) {
                summaryEl.classList.add('builder-hidden');
                summaryEl.innerHTML = '';
            }
            if (gridEl) gridEl.innerHTML = '';
            if (workflowEl) workflowEl.classList.add('builder-hidden');
            if (yamlEl) yamlEl.textContent = '';
        }

        function clearJobPolling() {
            if (state.jobPollTimer) {
                clearInterval(state.jobPollTimer);
                state.jobPollTimer = null;
            }
        }

        function startJobPolling() {
            clearJobPolling();
            state.jobPollTimer = setInterval(function () {
                loadJobs();
            }, 1500);
        }

        function normalizeStoredRole(role) {
            if (!role || typeof role !== 'object') return null;
            var name = String(role.role_name || '').trim();
            if (!name) return null;
            return hydratePresetRole({
                role_name: name,
                personality_traits: Array.isArray(role.personality_traits) ? role.personality_traits : [],
                primary_responsibilities: Array.isArray(role.primary_responsibilities) ? role.primary_responsibilities : [],
                depends_on: Array.isArray(role.depends_on) ? role.depends_on : [],
                tools_used: Array.isArray(role.tools_used) ? role.tools_used : [],
                _expert_tag: role.expert_tag || role._expert_tag || '',
                _expert_source: role.expert_source || role._expert_source || '',
                _full_persona: role.full_persona || role._full_persona || '',
                _persona_preview: role.persona_preview || role._persona_preview || '',
                source_url: role.source_url || '',
                output_target: Array.isArray(role.output_target) ? role.output_target : [],
            });
        }

        function jobStatusTone(status) {
            var s = String(status || '').toLowerCase();
            if (s === 'complete') return 'success';
            if (s === 'failed') return 'error';
            if (s === 'running') return 'running';
            return 'warning';
        }

        function jobStatusLabel(status) {
            var s = String(status || '').toLowerCase();
            if (s === 'complete') return t('creator_job_status_complete');
            if (s === 'failed') return t('creator_job_status_failed');
            if (s === 'running') return t('creator_job_status_running');
            return t('creator_job_status_pending');
        }

        function formatJobTimestamp(value) {
            if (!value) return '-';
            var date = new Date(value);
            if (isNaN(date.getTime())) return String(value);
            return date.toLocaleString(currentLang === 'zh-CN' ? 'zh-CN' : 'en-US', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
            });
        }

        function applyJobDetail(job) {
            if (!job) return;
            state.selectedJobId = job.job_id || '';
            setMode('direct');
            if ($('builder-team-name')) $('builder-team-name').value = job.team_name || '';
            if ($('builder-task-desc')) $('builder-task-desc').value = job.task_description || '';
            state.roles = Array.isArray(job.extracted_roles) ? job.extracted_roles.map(normalizeStoredRole).filter(Boolean) : [];
            renderRoles();

            if (job.team_config && Object.keys(job.team_config).length) {
                state.teamConfig = job.team_config;
                renderTeamPreview(job.team_config);
                $('builder-download-btn').disabled = false;
                $('builder-import-btn').disabled = false;
                setBuilderPill('已加载构建记录', jobStatusTone(job.status));
                setBuilderStatus('已恢复历史构建结果，可继续下载、导入或修改后重建', jobStatusTone(job.status));
                setPreviewStatus('已载入构建记录 · ' + (job.status || '-'), jobStatusTone(job.status));
            } else {
                state.teamConfig = null;
                clearTeamPreview();
                $('builder-download-btn').disabled = true;
                $('builder-import-btn').disabled = true;
                if (String(job.status || '').toLowerCase() === 'failed') {
                    setBuilderPill('构建失败', 'error');
                    setBuilderStatus('该构建记录以失败结束', 'error');
                    setPreviewStatus(job.error || '该构建记录失败，没有可恢复的结果', 'error');
                } else if (String(job.status || '').toLowerCase() === 'running') {
                    setBuilderPill('构建中...', 'running');
                    setBuilderStatus('该构建记录仍在运行中', 'running');
                    setPreviewStatus('该构建仍在运行中，结果尚未生成', 'running');
                } else {
                    setPreviewStatus('该构建记录尚未产出可恢复结果', 'warning');
                }
            }
            schedulePersistBuilderState();
        }

        async function openJob(jobId) {
            if (!jobId) return;
            setPreviewStatus('正在加载构建记录...', 'running');
            try {
                var resp = await fetch('/api/team-creator/jobs/' + encodeURIComponent(jobId));
                var data = await resp.json();
                if (!resp.ok || !data.ok) throw new Error(data.error || '加载构建记录失败');
                applyJobDetail(data.job || {});
                await loadJobs();
            } catch (err) {
                setPreviewStatus('构建记录加载失败: ' + (err.message || String(err)), 'error');
            }
        }

        function defaultSessionStatusLabel(status, roles) {
            if (status === 'done') return '✅ ' + ((roles && roles.length) || 0) + ' 个角色';
            if (status === 'running') return '提取中...';
            if (status === 'error') return '❌ 失败';
            return '等待中';
        }

        function restoreExtractionSessions(sessionSnapshots, restoredActiveTabIdx) {
            var tabBar = $('extraction-tab-bar');
            var panelsEl = $('extraction-panels');
            if (tabBar) tabBar.innerHTML = '';
            if (panelsEl) panelsEl.innerHTML = '';

            extractionSessions = [];
            activeTabIdx = -1;

            if (!Array.isArray(sessionSnapshots) || !sessionSnapshots.length) {
                return;
            }

            sessionSnapshots.forEach(function (sessionSnapshot, idx) {
                if (!sessionSnapshot || !sessionSnapshot.url) return;
                var session = createSessionPanel(idx, {
                    url: sessionSnapshot.url,
                    title: sessionSnapshot.title || '',
                });
                if (!session) return;

                session.roles = Array.isArray(sessionSnapshot.roles) ? sessionSnapshot.roles : [];
                extractionSessions.push(session);

                if (sessionSnapshot.log) {
                    restoreLogElement(session.logEl, sessionSnapshot.log);
                }
                if (session.watchEl) {
                    session.watchEl.innerHTML = sessionSnapshot.watchHtml || '';
                }
                if (sessionSnapshot.previewUrl) {
                    if (session.iframeEl) session.iframeEl.src = sessionSnapshot.previewUrl;
                    if (session.placeholderEl) session.placeholderEl.style.display = 'none';
                } else {
                    if (session.iframeEl) session.iframeEl.removeAttribute('src');
                    if (session.placeholderEl) session.placeholderEl.style.display = 'flex';
                }

                updateSessionStatus(
                    session,
                    sessionSnapshot.status || 'pending',
                    sessionSnapshot.statusLabel || defaultSessionStatusLabel(sessionSnapshot.status, session.roles)
                );
            });

            ensureExtractionTabBarBinding();
            if (extractionSessions.length) {
                var targetIdx = typeof restoredActiveTabIdx === 'number' ? restoredActiveTabIdx : 0;
                targetIdx = Math.max(0, Math.min(targetIdx, extractionSessions.length - 1));
                switchTab(targetIdx);
            }
        }

        function restoreBuilderState() {
            var snapshot = readSessionSnapshot();
            if (!hasRestorableData(snapshot)) return false;

            suppressPersistence = true;
            try {
                state.selectedJobId = snapshot.selectedJobId || '';
                state.roles = Array.isArray(snapshot.roles) ? snapshot.roles : [];
                state.teamConfig = snapshot.teamConfig || null;
                state.mode = snapshot.mode === 'discover' ? 'discover' : 'direct';
                state.building = false;
                state.discoverController = null;
                state.discoverPhase = snapshot.discoverPhase || 'idle';
                state.discoverStopRequested = false;
                state.discoveredPages = Array.isArray(snapshot.discoveredPages) ? snapshot.discoveredPages : [];
                state.discoveredRoles = Array.isArray(snapshot.discoveredRoles) ? snapshot.discoveredRoles : [];

                discoverySelectMode = snapshot.discoveryMode === 'manual' ? 'manual' : 'llm';
                discoverySelected = snapshot.discoverySelected && typeof snapshot.discoverySelected === 'object'
                    ? snapshot.discoverySelected
                    : {};
                discoveryPresetMatches = snapshot.discoveryPresetMatches && typeof snapshot.discoveryPresetMatches === 'object'
                    ? snapshot.discoveryPresetMatches
                    : {};

                if ($('builder-team-name')) $('builder-team-name').value = snapshot.teamName || '';
                if ($('builder-task-desc')) $('builder-task-desc').value = snapshot.taskDescription || '';
                if ($('builder-max-roles') && snapshot.maxRoles) $('builder-max-roles').value = String(snapshot.maxRoles);

                setMode(state.mode);
                setDiscoverySelectMode(discoverySelectMode);
                renderRoles();
                renderDiscoveredPages(state.discoveredPages);
                renderDiscoveryRoles();
                restoreExtractionSessions(snapshot.extractionSessions, snapshot.activeTabIdx);
                setDiscoveryPhase(state.discoverPhase);

                var sections = snapshot.sections || {};
                if ($('discovery-log-section')) $('discovery-log-section').style.display = sections.discoveryLogVisible ? '' : 'none';
                if ($('discovery-live-section')) $('discovery-live-section').style.display = sections.liveVisible ? '' : 'none';
                if ($('discovery-extraction-progress')) $('discovery-extraction-progress').style.display = sections.extractionVisible ? '' : 'none';
                if ($('discovery-roles-review')) $('discovery-roles-review').style.display = sections.reviewVisible ? '' : 'none';

                var logs = snapshot.logs || {};
                restoreLogElement($('discovery-live-log'), logs.discovery);
                restoreLogElement($('discovery-extraction-log'), logs.extraction);

                var llmStatusEl = $('discovery-llm-status');
                if (llmStatusEl) {
                    if (logs.llmStatus) {
                        llmStatusEl.style.display = logs.llmStatus.display || '';
                        llmStatusEl.textContent = logs.llmStatus.text || '';
                        llmStatusEl.className = logs.llmStatus.className || 'discovery-llm-status';
                    } else {
                        llmStatusEl.style.display = 'none';
                        llmStatusEl.textContent = '';
                        llmStatusEl.className = 'discovery-llm-status';
                    }
                }

                if (state.teamConfig) {
                    renderTeamPreview(state.teamConfig);
                } else {
                    clearTeamPreview();
                }

                var statusSnapshot = snapshot.statuses || {};
                if (snapshot.hadInFlightWork) {
                    setBuilderPill('已恢复上次记录', 'warning');
                    setBuilderStatus('已恢复刷新前的记录；浏览器刷新会中断实时发现/构建，需要手动继续。', 'warning');
                    if (state.teamConfig) {
                        setPreviewStatus(
                            statusSnapshot.previewStatusText || '已恢复构建结果，仍可下载 ZIP 或导入 Clawcross',
                            statusSnapshot.previewStatusTone || 'success'
                        );
                    } else {
                        setPreviewStatus('已恢复日志与配置，未完成的实时任务需要重新启动。', 'warning');
                    }
                    if (state.discoverPhase === 'extraction' || extractionSessions.length) {
                        appendExtractionSummaryLog('RESTORED', '已恢复刷新前的提取记录；实时任务已中断，需要手动继续。', 'warning');
                    } else if (state.discoverPhase === 'discovery') {
                        appendDiscoveryLog('RESTORED', '已恢复刷新前的发现记录；实时任务已中断，需要手动继续。', 'warning');
                    }
                } else {
                    setBuilderPill(statusSnapshot.builderPillText || '等待配置', statusSnapshot.builderPillTone || 'muted');
                    setBuilderStatus(statusSnapshot.builderStatusText || '先描述任务或导入角色，然后开始构建团队。', statusSnapshot.builderStatusTone || 'muted');
                    setPreviewStatus(statusSnapshot.previewStatusText || '构建后将在这里预览 Team Config、Persona 和 DAG。', statusSnapshot.previewStatusTone || 'muted');
                }

                if ($('builder-download-btn')) $('builder-download-btn').disabled = !state.teamConfig;
                if ($('builder-import-btn')) $('builder-import-btn').disabled = !state.teamConfig;
                if ($('builder-build-btn')) $('builder-build-btn').disabled = false;
                setDiscoveryButtons(false);
            } finally {
                suppressPersistence = false;
            }

            persistBuilderStateNow();
            return true;
        }

        function bindPersistenceListeners() {
            ['builder-team-name', 'builder-task-desc', 'builder-max-roles'].forEach(function (id) {
                var el = $(id);
                if (!el) return;
                el.addEventListener('input', schedulePersistBuilderState);
                el.addEventListener('change', schedulePersistBuilderState);
            });

            ['builder-roles-list', 'discovery-roles-list'].forEach(function (id) {
                var el = $(id);
                if (!el) return;
                el.addEventListener('input', schedulePersistBuilderState);
                el.addEventListener('change', schedulePersistBuilderState);
            });

            window.addEventListener('beforeunload', persistBuilderStateNow);
            document.addEventListener('visibilitychange', function () {
                if (document.visibilityState === 'hidden') {
                    persistBuilderStateNow();
                }
            });
        }

        function handleRoleRemove(e) {
            var btn = e.target.closest('.builder-role-remove');
            if (!btn) return;
            var card = btn.closest('.builder-role-card');
            if (!card) return;
            var container = card.closest('.builder-roles-list');
            var idx = parseInt(card.dataset.roleIdx, 10);

            if (container && container.id === 'discovery-roles-list') {
                state.discoveredRoles = collectRolesFromUI('discovery-roles-list');
                state.discoveredRoles.splice(idx, 1);
                // Re-map selection and preset match indices after deletion
                var newSelected = {};
                var newMatches = {};
                for (var k in discoverySelected) {
                    var ki = parseInt(k, 10);
                    if (ki === idx) continue;
                    var newIdx = ki > idx ? ki - 1 : ki;
                    newSelected[newIdx] = discoverySelected[k];
                }
                for (var m in discoveryPresetMatches) {
                    var mi = parseInt(m, 10);
                    if (mi === idx) continue;
                    var newMIdx = mi > idx ? mi - 1 : mi;
                    newMatches[newMIdx] = discoveryPresetMatches[m];
                }
                discoverySelected = newSelected;
                discoveryPresetMatches = newMatches;
                renderDiscoveryRoles();
            } else {
                state.roles = collectRolesFromUI('builder-roles-list');
                state.roles.splice(idx, 1);
                renderRoles();
            }
        }

        // ── Mode Switch ──
        function setMode(mode) {
            state.mode = mode;
            var directBtn = $('builder-mode-direct');
            var discoverBtn = $('builder-mode-discover');
            var importColleagueBtn = $('builder-mode-import-colleague');
            var importMentorBtn = $('builder-mode-import-mentor');
            var rolesStep = $('builder-step-roles');
            var discoverStep = $('builder-step-discover');
            var importColleagueStep = $('builder-step-import-colleague');
            var importMentorStep = $('builder-step-import-mentor');
            if (directBtn) directBtn.classList.toggle('builder-mode-active', mode === 'direct');
            if (discoverBtn) discoverBtn.classList.toggle('builder-mode-active', mode === 'discover');
            if (importColleagueBtn) importColleagueBtn.classList.toggle('builder-mode-active', mode === 'import-colleague');
            if (importMentorBtn) importMentorBtn.classList.toggle('builder-mode-active', mode === 'import-mentor');
            if (rolesStep) rolesStep.classList.toggle('builder-hidden', mode !== 'direct');
            if (discoverStep) discoverStep.classList.toggle('builder-hidden', mode !== 'discover');
            if (importColleagueStep) importColleagueStep.classList.toggle('builder-hidden', mode !== 'import-colleague');
            if (importMentorStep) importMentorStep.classList.toggle('builder-hidden', mode !== 'import-mentor');
            schedulePersistBuilderState();
        }

        // ── Add Expert from Pool as Role ──
        function addExpertAsRole(expert) {
            // Collect current roles to avoid duplicates
            state.roles = collectRolesFromUI('builder-roles-list');

            // Check for duplicate by name
            var dname = getLocalizedExpertName(expert);
            var alreadyExists = state.roles.some(function (r) {
                return r.role_name === dname;
            });
            if (alreadyExists) {
                setBuilderPill('角色已存在', 'warning');
                setBuilderStatus('「' + dname + '」已在角色列表中', 'warning');
                return;
            }

            var persona = getExpertFullPersona(expert);
            var description = expert.description || '';

            // Build role from expert data — preserve _expert_tag so backend
            // can directly use the preset persona instead of generating a new one.
            // Store full persona for display — no need to split into fields.
            var role = {
                role_name: dname,
                personality_traits: [],
                primary_responsibilities: [],
                depends_on: [],
                tools_used: [],
                _expert_tag: expert.tag || '',
                _expert_source: expert.source || '',
                _full_persona: persona || description || '',
                _persona_preview: getPersonaPreview(expert) || (persona ? persona.slice(0, 200) : ''),
            };

            state.roles.push(role);
            setMode('direct');
            renderRoles();
            setBuilderPill(state.roles.length + ' 个角色', 'success');
            setBuilderStatus('已添加预设专家「' + dname + '」(' + (expert.tag || '') + ') — 构建时将使用完整预设 Persona', 'success');

            // Scroll to the new role card
            var cards = document.querySelectorAll('#builder-roles-list .builder-role-card');
            if (cards.length) {
                cards[cards.length - 1].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        }

        // ── JSON Paste Modal ──
        function showJsonModal() {
            var overlay = document.createElement('div');
            overlay.className = 'builder-modal-overlay';
            overlay.innerHTML =
                '<div class="builder-modal">' +
                    '<h3>' + escapeHtml(t('creator_json_modal_title')) + '</h3>' +
                    '<p class="builder-hint">' + escapeHtml(t('creator_json_modal_hint')) + '</p>' +
                    '<textarea id="builder-json-input" placeholder="' + escapeHtml(t('creator_json_modal_placeholder')) + '"></textarea>' +
                    '<div class="builder-modal-actions">' +
                        '<button class="creator-btn creator-btn-secondary" id="builder-json-cancel" type="button">' + escapeHtml(t('creator_json_modal_cancel')) + '</button>' +
                        '<button class="creator-btn" id="builder-json-confirm" type="button">' + escapeHtml(t('creator_json_modal_import')) + '</button>' +
                    '</div>' +
                '</div>';
            document.body.appendChild(overlay);

            $('builder-json-cancel').addEventListener('click', function () { overlay.remove(); });
            overlay.addEventListener('click', function (e) {
                if (e.target === overlay) overlay.remove();
            });
            $('builder-json-confirm').addEventListener('click', function () {
                var raw = ($('builder-json-input') || {}).value || '';
                try {
                    var parsed = JSON.parse(raw);
                    var arr = Array.isArray(parsed) ? parsed : (parsed.roles || [parsed]);
                    if (!arr.length) throw new Error('Empty');
                    state.roles = arr.map(function (item) {
                        return {
                            role_name: item.role_name || item.name || '',
                            personality_traits: item.personality_traits || [],
                            primary_responsibilities: item.primary_responsibilities || item.responsibilities || [],
                            depends_on: item.depends_on || [],
                            tools_used: item.tools_used || item.tools || [],
                        };
                    }).filter(function (r) { return r.role_name; });
                    renderRoles();
                    overlay.remove();
                    setBuilderPill(state.roles.length + ' 个角色', 'success');
                    setBuilderStatus('已导入 ' + state.roles.length + ' 个角色', 'success');
                } catch (err) {
                    alert('JSON 解析失败: ' + err.message);
                }
            });
        }

        // ═════════════════════════════════════════════════
        //  TinyFish Discovery Pipeline
        // ═════════════════════════════════════════════════

        function setDiscoveryPhase(phase) {
            state.discoverPhase = phase;
            var phases = ['discovery', 'extraction', 'confirm'];
            phases.forEach(function (p) {
                var el = $('phase-' + p);
                if (!el) return;
                el.classList.remove('phase-active', 'phase-done');
                if (p === phase) el.classList.add('phase-active');
                else if (phases.indexOf(p) < phases.indexOf(phase)) el.classList.add('phase-done');
            });
            schedulePersistBuilderState();
        }

        function appendDiscoveryLog(title, detail, tone) {
            var el = $('discovery-live-log');
            if (!el) return;
            var colors = { normal: '#dbeafe', success: '#86efac', warning: '#fcd34d', error: '#fca5a5', info: '#93c5fd' };
            if (el.dataset.empty !== '0') { el.textContent = ''; el.dataset.empty = '0'; }
            var row = document.createElement('div');
            row.style.padding = '8px 0';
            row.style.borderBottom = '1px solid rgba(148, 163, 184, 0.16)';
            var titleEl = document.createElement('div');
            titleEl.style.fontWeight = '700';
            titleEl.style.color = colors[tone] || colors.normal;
            titleEl.textContent = title;
            row.appendChild(titleEl);
            if (detail) {
                var detailEl = document.createElement('div');
                detailEl.style.marginTop = '4px';
                detailEl.style.color = 'rgba(219, 234, 254, 0.82)';
                detailEl.textContent = detail;
                row.appendChild(detailEl);
            }
            el.appendChild(row);
            el.scrollTop = el.scrollHeight;
            schedulePersistBuilderState();
        }

        // ── Parallel Extraction Session State ──
        // Each session: { id, url, title, status, abortController, roles, logEl, iframeEl, placeholderEl, watchEl }
        var extractionSessions = [];
        var activeTabIdx = -1;
        
        function ensureExtractionTabBarBinding() {
            var tabBar = $('extraction-tab-bar');
            if (!tabBar || extractionTabBarBound) return;
            tabBar.addEventListener('click', function (e) {
                var tab = e.target.closest('.extraction-tab');
                if (!tab) return;
                var idx = parseInt(tab.dataset.tabIdx, 10);
                if (!isNaN(idx)) switchTab(idx);
            });
            extractionTabBarBound = true;
        }

        function createSessionPanel(idx, page) {
            var panelsEl = $('extraction-panels');
            if (!panelsEl) return null;
            var id = 'ext-session-' + idx;

            // Create tab button
            var tabBar = $('extraction-tab-bar');
            var tabBtn = document.createElement('button');
            tabBtn.className = 'extraction-tab' + (idx === 0 ? ' active' : '');
            tabBtn.dataset.tabIdx = idx;
            tabBtn.type = 'button';
            var localizedTitle = getDynamicDisplayText(page.title || page.url || '', 'page');
            var shortTitle = localizedTitle.slice(0, 28);
            if (shortTitle.length < localizedTitle.length) shortTitle += '…';
            tabBtn.innerHTML =
                '<span class="extraction-tab-dot dot-pending"></span>' +
                '<span class="extraction-tab-label">' + escapeHtml(shortTitle || t('creator_page_fallback', { index: idx + 1 })) + '</span>';
            if (tabBar) tabBar.appendChild(tabBtn);

            // Create panel
            var panel = document.createElement('div');
            panel.className = 'extraction-panel' + (idx === 0 ? ' active' : '');
            panel.id = id;
            panel.innerHTML =
                '<div class="extraction-session-layout">' +
                    '<div class="extraction-session-head">' +
                        '<div class="extraction-session-title" id="' + id + '-title"></div>' +
                        '<span class="extraction-session-status status-pending" id="' + id + '-status"></span>' +
                    '</div>' +
                    '<div class="extraction-session-watch" id="' + id + '-watch"></div>' +
                    '<div class="extraction-session-preview">' +
                        '<iframe class="extraction-session-iframe" id="' + id + '-iframe" title="TinyFish Session ' + (idx + 1) + '" loading="lazy"></iframe>' +
                        '<div class="extraction-session-placeholder" id="' + id + '-placeholder"></div>' +
                    '</div>' +
                    '<div class="extraction-session-log" id="' + id + '-log"></div>' +
                '</div>';
            panelsEl.appendChild(panel);

            var titleEl = panel.querySelector('#' + id + '-title');
            var statusEl = panel.querySelector('#' + id + '-status');
            var placeholderEl = panel.querySelector('#' + id + '-placeholder');
            var logEl = panel.querySelector('#' + id + '-log');
            setDynamicText(titleEl, '🐟 ' + (page.title || page.url), { context: 'page' });
            setDynamicText(statusEl, t('creator_session_waiting'), { context: 'status' });
            setDynamicText(placeholderEl, t('creator_session_waiting_tinyfish'), { context: 'status' });
            setDynamicText(logEl, t('creator_session_preparing') + ': ' + page.url, { context: 'status' });

            return {
                id: id,
                idx: idx,
                url: page.url,
                title: page.title || '',
                status: 'pending',
                roles: [],
                abortController: null,
                tabBtn: tabBtn,
                panelEl: panel,
                titleEl: titleEl,
                logEl: logEl,
                iframeEl: panel.querySelector('#' + id + '-iframe'),
                placeholderEl: placeholderEl,
                watchEl: panel.querySelector('#' + id + '-watch'),
                statusEl: statusEl,
            };
        }

        function switchTab(idx) {
            activeTabIdx = idx;
            var tabBar = $('extraction-tab-bar');
            if (tabBar) {
                var tabs = tabBar.querySelectorAll('.extraction-tab');
                tabs.forEach(function (t) { t.classList.toggle('active', parseInt(t.dataset.tabIdx) === idx); });
            }
            var panelsEl = $('extraction-panels');
            if (panelsEl) {
                var panels = panelsEl.querySelectorAll('.extraction-panel');
                panels.forEach(function (p, i) { p.classList.toggle('active', i === idx); });
            }
            schedulePersistBuilderState();
        }

        function updateSessionStatus(session, status, label) {
            session.status = status;
            var dot = session.tabBtn.querySelector('.extraction-tab-dot');
            if (dot) { dot.className = 'extraction-tab-dot dot-' + status; }
            if (session.statusEl) {
                session.statusEl.className = 'extraction-session-status status-' + status;
                setDynamicText(session.statusEl, label || status, { context: 'status' });
            }
            schedulePersistBuilderState();
        }

        function appendSessionLog(session, title, detail, tone) {
            var el = session.logEl;
            if (!el) return;
            var colors = { normal: '#dbeafe', success: '#86efac', warning: '#fcd34d', error: '#fca5a5', info: '#93c5fd' };
            if (el.dataset.empty !== '0') { el.textContent = ''; el.dataset.empty = '0'; }
            var row = document.createElement('div');
            row.style.padding = '4px 0';
            row.style.borderBottom = '1px solid rgba(148, 163, 184, 0.16)';
            var titleEl = document.createElement('div');
            titleEl.style.fontWeight = '700';
            titleEl.style.color = colors[tone] || colors.normal;
            titleEl.style.fontSize = '12px';
            setDynamicText(titleEl, title, { context: 'log' });
            row.appendChild(titleEl);
            if (detail) {
                var detailEl = document.createElement('div');
                detailEl.style.marginTop = '2px';
                detailEl.style.color = 'rgba(219, 234, 254, 0.82)';
                detailEl.style.fontSize = '11px';
                setDynamicText(detailEl, detail, { context: 'log' });
                row.appendChild(detailEl);
            }
            el.appendChild(row);
            el.scrollTop = el.scrollHeight;
            schedulePersistBuilderState();
        }

        function setSessionPreview(session, url) {
            if (!session.iframeEl || !session.placeholderEl) return;
            if (!url) {
                session.iframeEl.removeAttribute('src');
                session.placeholderEl.style.display = 'flex';
                schedulePersistBuilderState();
                return;
            }
            session.iframeEl.src = url;
            session.placeholderEl.style.display = 'none';
            schedulePersistBuilderState();
        }

        function setSessionWatchLink(session, url) {
            if (!session.watchEl) return;
            if (!url) {
                session.watchEl.innerHTML = '';
                schedulePersistBuilderState();
                return;
            }
            session.watchEl.innerHTML = '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(t('creator_open_live_watch')) + '</a>';
            schedulePersistBuilderState();
        }

        function refreshExtractionSessionI18n() {
            extractionSessions.forEach(function (session) {
                if (!session) return;
                if (session.tabBtn) {
                    var localizedTitle = getDynamicDisplayText(session.title || session.url || '', 'page');
                    var shortTitle = localizedTitle.slice(0, 28);
                    if (shortTitle.length < localizedTitle.length) shortTitle += '…';
                    var labelEl = session.tabBtn.querySelector('.extraction-tab-label');
                    if (labelEl) {
                        labelEl.textContent = shortTitle || t('creator_page_fallback', { index: session.idx + 1 });
                    }
                }
                if (session.iframeEl && session.iframeEl.getAttribute('src')) {
                    setSessionWatchLink(session, session.iframeEl.getAttribute('src'));
                }
            });
        }

        function setDiscoveryButtons(running) {
            var startBtn = $('builder-discover-btn');
            var stopBtn = $('builder-discover-stop');
            if (startBtn) startBtn.disabled = running;
            if (stopBtn) stopBtn.disabled = !running;
            schedulePersistBuilderState();
        }

        function eventDetail(event) {
            if (window.ClawcrossTinyFish && window.ClawcrossTinyFish.formatTinyFishEventDetail) {
                return window.ClawcrossTinyFish.formatTinyFishEventDetail(event, { maxLength: 800 });
            }
            return '';
        }

        function appendDiscoveryLog(title, detail, tone) {
            var el = $('discovery-live-log');
            if (!el) return;
            var colors = { normal: '#dbeafe', success: '#86efac', warning: '#fcd34d', error: '#fca5a5', info: '#93c5fd' };
            if (el.dataset.empty !== '0') { el.textContent = ''; el.dataset.empty = '0'; }
            var row = document.createElement('div');
            row.style.padding = '8px 0';
            row.style.borderBottom = '1px solid rgba(148, 163, 184, 0.16)';
            var titleEl = document.createElement('div');
            titleEl.style.fontWeight = '700';
            titleEl.style.color = colors[tone] || colors.normal;
            setDynamicText(titleEl, title, { context: 'log' });
            row.appendChild(titleEl);
            if (detail) {
                var detailEl = document.createElement('div');
                detailEl.style.marginTop = '4px';
                detailEl.style.color = 'rgba(219, 234, 254, 0.82)';
                setDynamicText(detailEl, detail, { context: 'log' });
                row.appendChild(detailEl);
            }
            el.appendChild(row);
            el.scrollTop = el.scrollHeight;
            schedulePersistBuilderState();
        }

        function appendExtractionSummaryLog(title, detail, tone) {
            var el = $('discovery-extraction-log');
            if (!el) return;
            var colors = { normal: '#dbeafe', success: '#86efac', warning: '#fcd34d', error: '#fca5a5', info: '#93c5fd' };
            if (el.dataset.empty !== '0') { el.textContent = ''; el.dataset.empty = '0'; }
            var row = document.createElement('div');
            row.style.padding = '6px 0';
            row.style.borderBottom = '1px solid rgba(148, 163, 184, 0.16)';
            var titleEl = document.createElement('div');
            titleEl.style.fontWeight = '700';
            titleEl.style.color = colors[tone] || colors.normal;
            setDynamicText(titleEl, title, { context: 'log' });
            row.appendChild(titleEl);
            if (detail) {
                var detailEl = document.createElement('div');
                detailEl.style.marginTop = '3px';
                detailEl.style.color = 'rgba(219, 234, 254, 0.82)';
                detailEl.style.fontSize = '12px';
                setDynamicText(detailEl, detail, { context: 'log' });
                row.appendChild(detailEl);
            }
            el.appendChild(row);
            el.scrollTop = el.scrollHeight;
            schedulePersistBuilderState();
        }

        function collectExtractionRoles() {
            var allRoles = [];
            var seenNames = {};
            extractionSessions.forEach(function (session) {
                (session.roles || []).forEach(function (role) {
                    var roleName = String(role && role.role_name || '').trim();
                    if (!roleName || seenNames[roleName]) return;
                    seenNames[roleName] = true;
                    allRoles.push(role);
                });
            });
            return allRoles;
        }

        function showDiscoveryRoleReview(roles, options) {
            var reviewEl = $('discovery-roles-review');
            var stopped = !!(options && options.stopped);
            var roleCount = roles.length;

            state.discoveredRoles = roles;
            setDiscoveryPhase('confirm');
            if (reviewEl) reviewEl.style.display = '';
            renderDiscoveryRoles();

            if (stopped) {
                appendExtractionSummaryLog('STOPPED', '已停止并保留 ' + roleCount + ' 个已提取角色，进入确认步骤', 'warning');
                setBuilderPill('⏸ 已保留 ' + roleCount + ' 个角色', 'warning');
                setBuilderStatus('提取已停止，已保留 ' + roleCount + ' 个已完成角色，请确认后继续构建', 'warning');
            } else {
                setBuilderPill('✅ 发现 ' + roleCount + ' 个角色', 'success');
                if (options && options.llmDirect) {
                    setBuilderStatus('LLM 已生成 ' + roleCount + ' 个角色（无 TinyFish），请确认后构建团队', 'success');
                } else {
                    setBuilderStatus('TinyFish 并行提取完成，共 ' + roleCount + ' 个角色，请确认后构建团队', 'success');
                }
            }
            schedulePersistBuilderState();
        }

        // Phase 1: Discovery — search for SOP/org pages
        async function startDiscovery() {
            var taskDesc = ($('builder-task-desc') || {}).value || '';
            if (!taskDesc.trim()) {
                setBuilderPill('需要任务描述', 'warning');
                setBuilderStatus('请先在 Step 1 中填写任务/业务描述', 'warning');
                return;
            }

            // Reset state
            state.discoveredPages = [];
            state.discoveredRoles = [];
            state.discoverController = new AbortController();
            state.discoverStopRequested = false;
            extractionSessions = [];
            activeTabIdx = -1;
            discoverySelected = {};
            discoveryPresetMatches = {};
            setDiscoveryPhase('discovery');
            setDiscoveryButtons(true);

            // Show discovery log section
            var logSection = $('discovery-log-section');
            if (logSection) logSection.style.display = '';
            // Hide extraction tabs section initially
            var liveSection = $('discovery-live-section');
            if (liveSection) liveSection.style.display = 'none';
            // Clear extraction tab bar / panels
            var tabBar = $('extraction-tab-bar');
            if (tabBar) tabBar.innerHTML = '';
            var panelsEl = $('extraction-panels');
            if (panelsEl) panelsEl.innerHTML = '';

            var logEl = $('discovery-live-log');
            if (logEl) { logEl.textContent = ''; logEl.dataset.empty = '1'; }
            var pagesEl = $('builder-discover-pages');
            if (pagesEl) pagesEl.innerHTML = '';
            var reviewEl = $('discovery-roles-review');
            if (reviewEl) reviewEl.style.display = 'none';
            var extractEl = $('discovery-extraction-progress');
            if (extractEl) extractEl.style.display = 'none';
            var llmStatusEl = $('discovery-llm-status');
            if (llmStatusEl) llmStatusEl.style.display = 'none';

            setBuilderPill('🔍 发现中...', 'running');
            setBuilderStatus('AI Agent 正在搜索与你的任务相关的 SOP 和组织架构 URL...', 'running');
            appendDiscoveryLog('STARTED', '开始 AI 搜索: ' + taskDesc.trim().slice(0, 100), 'info');
            schedulePersistBuilderState();

            var discoveryResult = null;

            try {
                var resp = await fetch('/api/team-creator/discover', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_description: taskDesc.trim() }),
                    signal: state.discoverController.signal,
                });

                if (!resp.ok) {
                    var errData = await resp.json().catch(function () { return {}; });
                    throw new Error(errData.error || 'Discovery failed (HTTP ' + resp.status + ')');
                }

                if (!window.ClawcrossTinyFish || !window.ClawcrossTinyFish.consumeJsonSseStream) {
                    throw new Error('TinyFish live helper 未加载');
                }

                await window.ClawcrossTinyFish.consumeJsonSseStream(resp, async function (event) {
                    if (window.ClawcrossTinyFish && window.ClawcrossTinyFish.normalizeTinyFishEvent) {
                        event = window.ClawcrossTinyFish.normalizeTinyFishEvent(event);
                    }
                    var type = String(event._tinyfish_type || event.type || '').toUpperCase();
                    var label = window.ClawcrossTinyFish && window.ClawcrossTinyFish.getTinyFishEventLabel
                        ? window.ClawcrossTinyFish.getTinyFishEventLabel(event)
                        : (type || 'EVENT');

                    if (type === 'STREAMING_URL') {
                        var url = event.streaming_url || event.url || '';
                        appendDiscoveryLog('STREAMING_URL', url, 'info');
                        return;
                    }
                    if (window.ClawcrossTinyFish && window.ClawcrossTinyFish.isIgnorableHeartbeat && window.ClawcrossTinyFish.isIgnorableHeartbeat(event)) {
                        return;
                    }
                    if (type === 'STARTED') {
                        appendDiscoveryLog(label, eventDetail(event) || 'TinyFish 已启动', 'info');
                        return;
                    }
                    if (type === 'PROGRESS') {
                        appendDiscoveryLog(label, eventDetail(event) || 'progress update', 'normal');
                        return;
                    }
                    if (type === 'COMPLETE') {
                        discoveryResult = event;
                        var det = eventDetail(event);
                        appendDiscoveryLog(label, det || 'Discovery 阶段完成', 'success');
                        return;
                    }
                    if (type === 'ERROR') {
                        appendDiscoveryLog(label, eventDetail(event) || 'unknown error', 'error');
                        return;
                    }
                    // Generic event
                    var det = eventDetail(event);
                    appendDiscoveryLog(label, det || 'no structured detail', type === 'HEARTBEAT' ? 'info' : 'normal');
                });

            } catch (err) {
                if (err.name === 'AbortError') {
                    appendDiscoveryLog('STOPPED', '发现已停止', 'warning');
                    setBuilderPill('已停止', 'warning');
                    setBuilderStatus('发现流程已停止', 'warning');
                    state.discoverStopRequested = false;
                } else {
                    appendDiscoveryLog('ERROR', err.message || String(err), 'error');
                    setBuilderPill('发现失败', 'error');
                    setBuilderStatus(err.message || '发现失败', 'error');
                }
                setDiscoveryButtons(false);
                state.discoverController = null;
                return;
            }

            state.discoverController = null;

            // Result: pages (TinyFish path) or llm_direct roles (no TINYFISH_API_KEY)
            var pages = [];
            var directRoles = null;
            if (discoveryResult && discoveryResult.result) {
                var raw = discoveryResult.result;
                if (typeof raw === 'string') {
                    try { raw = JSON.parse(raw); } catch (e) { raw = null; }
                }
                if (raw && raw.pages && Array.isArray(raw.pages)) {
                    pages = raw.pages.filter(function (p) { return p && p.url; });
                }
                if (raw && raw.llm_direct && Array.isArray(raw.roles) && raw.roles.length) {
                    directRoles = raw.roles;
                }
            }

            if (directRoles && directRoles.length) {
                var normalized = directRoles.map(function (item) {
                    if (!item || typeof item !== 'object') return null;
                    var name = String(item.role_name || '').trim();
                    if (!name) return null;
                    return {
                        role_name: name,
                        personality_traits: item.personality_traits || [],
                        primary_responsibilities: item.primary_responsibilities || [],
                        depends_on: item.depends_on || item.input_dependency || [],
                        tools_used: item.tools_used || [],
                        _output_target: item.output_target || [],
                    };
                }).filter(Boolean);
                if (!normalized.length) {
                    appendDiscoveryLog('WARNING', 'LLM 返回的角色格式无效', 'warning');
                    setBuilderPill('解析失败', 'warning');
                    setBuilderStatus('无法解析角色数据，请重试', 'warning');
                    setDiscoveryButtons(false);
                    schedulePersistBuilderState();
                    return;
                }
                state.discoveredPages = [];
                renderDiscoveredPages([]);
                appendDiscoveryLog('COMPLETE', 'LLM 直接生成 ' + normalized.length + ' 个角色（无 TinyFish）', 'success');
                showDiscoveryRoleReview(normalized, { stopped: false, llmDirect: true });
                setDiscoveryButtons(false);
                schedulePersistBuilderState();
                return;
            }

            state.discoveredPages = pages;
            renderDiscoveredPages(pages);

            if (!pages.length) {
                appendDiscoveryLog('WARNING', '未发现可用页面。请尝试更具体的任务描述，或切换到手动模式。', 'warning');
                setBuilderPill('未发现页面', 'warning');
                setBuilderStatus('AI 未找到相关页面，请调整描述或手动添加角色', 'warning');
                setDiscoveryButtons(false);
                schedulePersistBuilderState();
                return;
            }

            appendDiscoveryLog('PAGES', '发现 ' + pages.length + ' 个候选页面，TinyFish 开始深度提取角色...', 'success');

            // Phase 2: Extraction — extract roles from discovered pages
            await startExtraction(pages);
        }

        function renderDiscoveredPages(pages) {
            var el = $('builder-discover-pages');
            if (!el) return;
            if (!pages.length) {
                el.innerHTML = '';
                schedulePersistBuilderState();
                return;
            }
            prefetchDynamicTranslations(
                pages.reduce(function (acc, page) {
                    acc.push(page.title || page.url || '');
                    acc.push(page.type || 'page');
                    return acc;
                }, []),
                { context: 'page' }
            ).then(function (changed) {
                if (changed) renderDiscoveredPages(pages);
            });

            el.innerHTML = '<div class="creator-card-kicker" style="margin-bottom:8px;">' + escapeHtml(t('creator_discovered_pages_title', { count: pages.length })) + '</div>' +
                pages.map(function (page) {
                    var pageType = getDynamicDisplayText(page.type || 'page', 'page');
                    var pageTitle = getDynamicDisplayText(page.title || page.url, 'page');
                    return (
                        '<div class="builder-discover-page-item">' +
                            '<span class="builder-discover-page-type">' + escapeHtml(pageType) + '</span>' +
                            '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' +
                                escapeHtml(pageTitle) +
                            '</span>' +
                            '<a href="' + escapeHtml(page.url) + '" target="_blank" style="color:var(--creator-primary);font-size:12px;flex-shrink:0;">🔗</a>' +
                        '</div>'
                    );
                }).join('');
            schedulePersistBuilderState();
        }

        // Phase 2: Parallel Extraction — one TinyFish session per page
        async function startExtraction(pages) {
            setDiscoveryPhase('extraction');

            // Show the extraction tab area
            var liveSection = $('discovery-live-section');
            if (liveSection) liveSection.style.display = '';

            // Show extraction summary progress
            var extractEl = $('discovery-extraction-progress');
            if (extractEl) extractEl.style.display = '';
            var summaryLogEl = $('discovery-extraction-log');
            if (summaryLogEl) { summaryLogEl.textContent = ''; summaryLogEl.dataset.empty = '1'; }

            // Clear previous sessions
            var tabBar = $('extraction-tab-bar');
            if (tabBar) tabBar.innerHTML = '';
            var panelsEl = $('extraction-panels');
            if (panelsEl) panelsEl.innerHTML = '';
            extractionSessions = [];
            activeTabIdx = 0;

            setBuilderPill('🐟 并行提取中...', 'running');
            setBuilderStatus('TinyFish 正在并行提取 ' + pages.length + ' 个页面的角色数据...', 'running');
            appendExtractionSummaryLog('STARTED', '启动 ' + pages.length + ' 个并行提取 session', 'info');

            // Create session panels
            for (var i = 0; i < pages.length; i++) {
                var session = createSessionPanel(i, pages[i]);
                if (session) extractionSessions.push(session);
            }
            ensureExtractionTabBarBinding();
            schedulePersistBuilderState();

            // Launch all extractions in parallel
            var promises = extractionSessions.map(function (session) {
                return runSingleExtraction(session);
            });

            await Promise.allSettled(promises);

            var stopRequested = state.discoverStopRequested === true;
            state.discoverStopRequested = false;
            var allRoles = collectExtractionRoles();

            var doneCount = extractionSessions.filter(function (s) { return s.status === 'done'; }).length;
            var errCount = extractionSessions.filter(function (s) { return s.status === 'error'; }).length;
            appendExtractionSummaryLog(
                stopRequested ? 'PARTIAL RESULT' : 'ALL COMPLETE',
                doneCount + ' 成功, ' + errCount + ' 失败, 共 ' + allRoles.length + ' 个去重角色',
                stopRequested ? 'warning' : (errCount === extractionSessions.length ? 'error' : 'success')
            );

            if (!allRoles.length) {
                if (stopRequested) {
                    setBuilderPill('已停止', 'warning');
                    setBuilderStatus('提取已停止，但当前还没有可确认的角色。可以重新开始或切换到手动模式。', 'warning');
                } else {
                    setBuilderPill('未提取到角色', 'warning');
                    setBuilderStatus('TinyFish 未能从页面中提取到角色数据。可以尝试手动定义角色。', 'warning');
                }
                setDiscoveryButtons(false);
                return;
            }

            // Phase 3: Show roles for confirmation
            showDiscoveryRoleReview(allRoles, { stopped: stopRequested });
            setDiscoveryButtons(false);
        }

        // Run extraction for a single session (called in parallel)
        async function runSingleExtraction(session) {
            updateSessionStatus(session, 'running', '提取中...');
            appendSessionLog(session, 'STARTED', '开始提取: ' + session.url, 'info');
            appendExtractionSummaryLog('SESSION ' + (session.idx + 1), '开始: ' + (session.title || session.url).slice(0, 60), 'info');

            // Auto-switch to this tab if it's the first one to start running
            if (activeTabIdx === session.idx || extractionSessions.filter(function(s) { return s.status === 'running'; }).length === 1) {
                switchTab(session.idx);
            }

            var extractResult = null;
            try {
                session.abortController = new AbortController();
                var resp = await fetch('/api/team-creator/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: session.url, title: session.title }),
                    signal: session.abortController.signal,
                });

                if (!resp.ok) {
                    var errData = await resp.json().catch(function () { return {}; });
                    throw new Error(errData.error || 'HTTP ' + resp.status);
                }

                await window.ClawcrossTinyFish.consumeJsonSseStream(resp, async function (event) {
                    if (window.ClawcrossTinyFish && window.ClawcrossTinyFish.normalizeTinyFishEvent) {
                        event = window.ClawcrossTinyFish.normalizeTinyFishEvent(event);
                    }
                    var type = String(event._tinyfish_type || event.type || '').toUpperCase();
                    var label = window.ClawcrossTinyFish && window.ClawcrossTinyFish.getTinyFishEventLabel
                        ? window.ClawcrossTinyFish.getTinyFishEventLabel(event)
                        : (type || 'EVENT');
                    if (type === 'STREAMING_URL') {
                        var url = event.streaming_url || event.url || '';
                        setSessionPreview(session, url);
                        setSessionWatchLink(session, url);
                        appendSessionLog(session, label, url, 'info');
                        return;
                    }
                    if (window.ClawcrossTinyFish && window.ClawcrossTinyFish.isIgnorableHeartbeat && window.ClawcrossTinyFish.isIgnorableHeartbeat(event)) {
                        return;
                    }
                    if (type === 'COMPLETE') {
                        extractResult = event;
                        appendSessionLog(session, label, eventDetail(event) || '提取完成', 'success');
                        return;
                    }
                    if (type === 'PROGRESS') {
                        appendSessionLog(session, label, eventDetail(event) || 'progress', 'normal');
                        return;
                    }
                    if (type === 'ERROR') {
                        appendSessionLog(session, label, eventDetail(event) || 'error', 'error');
                        return;
                    }
                    appendSessionLog(session, label, eventDetail(event) || '', type === 'HEARTBEAT' ? 'info' : 'normal');
                });

                // Parse roles from result
                if (extractResult && extractResult.result) {
                    var raw = extractResult.result;
                    if (typeof raw === 'string') {
                        try { raw = JSON.parse(raw); } catch (e) { raw = null; }
                    }
                    if (raw) {
                        var roleList = raw.roles || raw.data || raw.results || [];
                        if (!Array.isArray(roleList)) {
                            Object.values(raw).forEach(function (v) {
                                if (Array.isArray(v) && v.length && typeof v[0] === 'object') roleList = v;
                            });
                        }
                        (roleList || []).forEach(function (item) {
                            if (!item || typeof item !== 'object') return;
                            var name = String(item.role_name || item.agent_role || item.name || '').trim();
                            if (!name) return;
                            session.roles.push({
                                role_name: name,
                                personality_traits: item.personality_traits || item.persona_traits || [],
                                primary_responsibilities: item.primary_responsibilities || item.core_duties || [],
                                depends_on: item.depends_on || item.input_dependency || [],
                                tools_used: item.tools_used || [],
                                _output_target: item.output_target || [],
                            });
                        });
                    }
                }

                updateSessionStatus(session, 'done', '✅ ' + session.roles.length + ' 个角色');
                appendSessionLog(session, 'DONE', '提取了 ' + session.roles.length + ' 个角色', 'success');
                appendExtractionSummaryLog('SESSION ' + (session.idx + 1) + ' ✅', (session.title || session.url).slice(0, 40) + ' → ' + session.roles.length + ' 角色', 'success');
                schedulePersistBuilderState();

            } catch (err) {
                if (err.name === 'AbortError') {
                    updateSessionStatus(session, 'error', '已停止');
                    appendSessionLog(session, 'STOPPED', '提取已停止', 'warning');
                } else {
                    updateSessionStatus(session, 'error', '❌ 失败');
                    appendSessionLog(session, 'ERROR', err.message || String(err), 'error');
                    appendExtractionSummaryLog('SESSION ' + (session.idx + 1) + ' ❌', (session.title || '').slice(0, 40) + ': ' + (err.message || ''), 'error');
                }
            } finally {
                session.abortController = null;
            }
        }

        function stopDiscovery() {
            state.discoverStopRequested = true;
            if (state.discoverController) {
                state.discoverController.abort();
                state.discoverController = null;
            }
            // Abort all parallel extraction sessions
            extractionSessions.forEach(function (session) {
                if (session.abortController) {
                    session.abortController.abort();
                    session.abortController = null;
                }
            });
            if (state.discoverPhase === 'extraction') {
                appendExtractionSummaryLog('STOPPING', '正在停止并保留已完成提取的角色...', 'warning');
                setBuilderPill('⏸ 正在停止...', 'warning');
                setBuilderStatus('正在停止 TinyFish 并行提取，并准备保留已完成角色进入确认步骤...', 'warning');
            } else {
                appendDiscoveryLog('STOPPING', '正在停止发现流程...', 'warning');
                setBuilderPill('⏸ 正在停止...', 'warning');
                setBuilderStatus('正在停止发现流程...', 'warning');
            }
            setDiscoveryButtons(false);
            schedulePersistBuilderState();
        }

        // ── LLM Smart Select ──
        async function runLlmSmartSelect() {
            var roles = state.discoveredRoles;
            if (!roles.length) {
                setBuilderPill('无角色可筛选', 'warning');
                return;
            }

            var maxRoles = getMaxRoles();
            var taskDesc = ($('builder-task-desc') || {}).value || '';
            var btn = $('discovery-llm-select-btn');
            var statusEl = $('discovery-llm-status');
            if (btn) {
                btn.disabled = true;
                setDynamicText(btn, '🤖 智能筛选中...', { context: 'status' });
            }
            if (statusEl) {
                statusEl.style.display = '';
                setDynamicText(statusEl, '🤖 正在调用 LLM 分析 ' + roles.length + ' 个角色，筛选最重要的 ' + maxRoles + ' 个并匹配预设专家池...', { context: 'status' });
                statusEl.className = 'discovery-llm-status discovery-llm-status-running';
            }

            try {
                var resp = await fetch('/api/team-creator/smart-select', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        roles: roles,
                        max_roles: maxRoles,
                        task_description: taskDesc.trim(),
                    }),
                });
                var data = await resp.json();
                if (!resp.ok || !data.ok) {
                    throw new Error(data.error || 'Smart select failed');
                }

                // Apply selection
                var selectedIndices = data.selected_indices || [];
                var presetMatches = data.preset_matches || [];

                // Reset selection
                discoverySelected = {};
                discoveryPresetMatches = {};

                // Mark selected roles
                selectedIndices.forEach(function (idx) {
                    if (idx >= 0 && idx < roles.length) {
                        discoverySelected[idx] = true;
                    }
                });

                // Apply preset matches
                presetMatches.forEach(function (match) {
                    var idx = match.role_index;
                    if (idx >= 0 && idx < roles.length) {
                        discoveryPresetMatches[idx] = {
                            matched_tag: match.matched_preset_tag || '',
                            matched_name: match.matched_preset_name || '',
                            confidence: match.confidence || 0,
                        };
                    }
                });

                renderDiscoveryRoles();

                var matchCount = presetMatches.length;
                if (statusEl) {
                    setDynamicText(statusEl, '✅ LLM 已选择 ' + selectedIndices.length + ' 个角色' +
                        (matchCount ? '，其中 ' + matchCount + ' 个匹配到预设专家' : '') +
                        '。你可以手动调整选择。', { context: 'status' });
                    statusEl.className = 'discovery-llm-status discovery-llm-status-done';
                }
                setBuilderPill('已筛选 ' + selectedIndices.length + ' 个角色', 'success');

            } catch (err) {
                if (statusEl) {
                    setDynamicText(statusEl, '❌ 智能筛选失败: ' + (err.message || String(err)), { context: 'status' });
                    statusEl.className = 'discovery-llm-status discovery-llm-status-error';
                }
                setBuilderPill('筛选失败', 'error');
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = t('creator_discovery_smart_select_btn');
                }
            }
        }

        // Transfer discovered roles into main role editor and switch mode
        function useDiscoveredRoles() {
            // Collect any edits from the discovery review panel
            var allRoles = collectRolesFromUI('discovery-roles-list');
            if (!allRoles.length) {
                allRoles = state.discoveredRoles;
            }

            // Only transfer selected (checked) roles
            var selectedRoles = [];
            var hasAnySelection = false;
            for (var k in discoverySelected) {
                if (discoverySelected[k]) { hasAnySelection = true; break; }
            }

            if (hasAnySelection) {
                allRoles.forEach(function (role, i) {
                    if (discoverySelected[i]) {
                        // If this role has a preset match, inject _expert_tag
                        var match = discoveryPresetMatches[i];
                        if (match && match.matched_tag) {
                            role._expert_tag = match.matched_tag;
                            // Also find full persona from expert pool cache
                            var preset = expertPoolCache.find(function (exp) {
                                return exp.tag === match.matched_tag;
                            });
                            if (preset) {
                                role._expert_source = preset.source || '';
                                role._full_persona = getExpertFullPersona(preset) || preset.description || '';
                                role._persona_preview = getPersonaPreview(preset) || (getExpertFullPersona(preset) || preset.description || '').slice(0, 200);
                            }
                        }
                        selectedRoles.push(role);
                    }
                });
            } else {
                // No selection made — use all roles (backward compat)
                selectedRoles = allRoles;
            }

            if (!selectedRoles.length) {
                setBuilderPill('未选择角色', 'warning');
                setBuilderStatus('请至少选择一个角色', 'warning');
                return;
            }

            state.roles = selectedRoles;
            setMode('direct');
            renderRoles();
            var presetCount = selectedRoles.filter(function (r) { return r._expert_tag; }).length;
            setBuilderPill(selectedRoles.length + ' 个角色已就绪', 'success');
            setBuilderStatus(
                '已导入 ' + selectedRoles.length + ' 个角色' +
                (presetCount ? ' (含 ' + presetCount + ' 个预设专家匹配)' : '') +
                '，可直接构建或继续调整',
                'success'
            );
            schedulePersistBuilderState();
        }

        function addDiscoveryRole() {
            state.discoveredRoles = collectRolesFromUI('discovery-roles-list');
            var newIdx = state.discoveredRoles.length;
            state.discoveredRoles.push({
                role_name: '',
                personality_traits: [],
                primary_responsibilities: [],
                depends_on: [],
                tools_used: [],
            });
            discoverySelected[newIdx] = true;  // Auto-select newly added role
            renderDiscoveryRoles();
            var cards = document.querySelectorAll('#discovery-roles-list .builder-role-card');
            if (cards.length) {
                var last = cards[cards.length - 1];
                var nameInput = last.querySelector('.builder-role-name');
                if (nameInput) nameInput.focus();
            }
            schedulePersistBuilderState();
        }

        // ═════════════════════════════════════════════════
        //  TinyFish Status Sidebar
        // ═════════════════════════════════════════════════

        async function loadTinyfishStatus() {
            var el = $('tinyfish-status-content');
            if (!el) return;
            try {
                var resp = await fetch('/api/tinyfish/status');
                var data = await resp.json();
                if (!resp.ok || data.ok === false) throw new Error(data.error || 'Failed');

                var cfg = data.config || {};
                var configured = cfg.api_key_configured;
                state.tinyfishReady = configured;

                el.innerHTML =
                    '<div class="creator-item">' +
                        '<div class="creator-item-title">' + escapeHtml(configured ? t('creator_tinyfish_state_ready') : t('creator_tinyfish_state_missing')) + '</div>' +
                        '<div class="creator-item-subtitle">' + escapeHtml(t('creator_tinyfish_base_url_label')) + ': ' + escapeHtml(cfg.base_url || '-') + '</div>' +
                    '</div>' +
                    (data.recent_runs && data.recent_runs.length ?
                        '<div class="creator-item">' +
                            '<div class="creator-item-title">' + escapeHtml(t('creator_tinyfish_recent_runs_label')) + '</div>' +
                            data.recent_runs.slice(0, 3).map(function (run) {
                                return '<div class="creator-item-subtitle">' +
                                    escapeHtml(getDynamicDisplayText(run.site_name || run.site_key, 'status')) +
                                    ' · ' +
                                    escapeHtml(getDynamicDisplayText(run.status || '-', 'status')) +
                                '</div>';
                            }).join('') +
                        '</div>' : '');

                if (!configured) {
                    $('builder-mode-discover') && ($('builder-mode-discover').title = t('creator_tinyfish_mode_disabled'));
                } else if ($('builder-mode-discover')) {
                    $('builder-mode-discover').title = '';
                }
            } catch (err) {
                el.innerHTML = '<div class="creator-item"><div class="creator-item-subtitle">' + escapeHtml(t('creator_tinyfish_status_load_failed', { error: err.message || String(err) })) + '</div></div>';
            }
        }

        // ── Build Team ──
        async function buildTeam() {
            if (state.building) return;

            var roles = collectRolesFromUI('builder-roles-list');
            if (!roles.length) {
                setPreviewStatus('请至少添加一个角色', 'warning');
                return;
            }
            var teamName = ($('builder-team-name') || {}).value || '';
            if (!teamName.trim()) {
                teamName = t('creator_default_team_name_prefix') + Date.now();
                if ($('builder-team-name')) $('builder-team-name').value = teamName;
            }
            var taskDesc = ($('builder-task-desc') || {}).value || '';

            state.building = true;
            state.selectedJobId = '';
            setBuilderPill('构建中...', 'running');
            setPreviewStatus('正在构建团队...', 'running');
            $('builder-build-btn').disabled = true;
            startJobPolling();
            schedulePersistBuilderState();

            try {
                var resp = await fetch('/api/team-creator/build', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        roles: roles,
                        team_name: teamName.trim(),
                        task_description: taskDesc.trim(),
                    }),
                });
                var data = {};
                try {
                    data = await resp.json();
                } catch (_parseErr) {
                    data = {};
                }
                var responseJobId = (data.job && data.job.job_id) || data.job_id || '';
                state.selectedJobId = responseJobId || state.selectedJobId;
                if (!resp.ok || !data.ok) {
                    if (responseJobId) {
                        await openJob(responseJobId);
                    }
                    throw new Error(data.error || ('Build failed (HTTP ' + resp.status + ')'));
                }

                state.teamConfig = data.team_config;
                if (responseJobId) {
                    await openJob(responseJobId);
                } else {
                    renderTeamPreview(data.team_config);
                    $('builder-download-btn').disabled = false;
                    $('builder-import-btn').disabled = false;
                }
                setBuilderPill('构建完成', 'success');
                setBuilderStatus('团队构建成功！你可以继续手动精修 Persona，然后下载 ZIP 或直接导入 Clawcross', 'success');
                setPreviewStatus('构建完成 · ' + (data.team_config.summary || {}).total_roles + ' 个角色 · Persona 可继续编辑', 'success');
            } catch (err) {
                if (!state.selectedJobId) {
                    setBuilderPill('构建失败', 'error');
                    setBuilderStatus('本次构建未能生成可恢复结果', 'error');
                    setPreviewStatus(err.message || '构建失败', 'error');
                    state.teamConfig = null;
                    clearTeamPreview();
                    $('builder-download-btn').disabled = true;
                    $('builder-import-btn').disabled = true;
                }
            } finally {
                state.building = false;
                await loadJobs();
                $('builder-build-btn').disabled = false;
                schedulePersistBuilderState();
            }
        }

        // ── Render Team Preview ──
        function renderTeamPreview(config) {
            var summaryEl = $('builder-summary');
            var experts = config.oasis_experts || [];

            prefetchDynamicTranslations(
                experts.reduce(function (acc, expert) {
                    acc.push(expert.name || '');
                    acc.push(expert.persona || '');
                    acc.push(getPersonaPreview(expert) || '');
                    acc.push(expert.matched_preset || expert.source || '');
                    return acc;
                }, []),
                { context: 'persona' }
            ).then(function (changed) {
                if (changed) renderTeamPreview(config);
            });

            if (summaryEl && config.summary) {
                var s = config.summary;
                var summaryStats = [
                    { value: String(s.total_roles || 0), label: t('creator_build_summary_total_roles') },
                    { value: String(s.workflow_nodes || 0), label: t('creator_build_summary_workflow_nodes') },
                    { value: String(s.selector_nodes || 0), label: t('creator_build_summary_selectors') },
                    { value: String(s.review_loops || 0), label: t('creator_build_summary_review_loops') },
                    { value: String(s.preset_matched || 0), label: t('creator_build_summary_preset_matched') },
                    { value: String(s.custom_generated || 0), label: t('creator_build_summary_custom_persona') },
                    { value: getDynamicDisplayText((s.workflow_mode || 'heuristic').toUpperCase(), 'status'), label: t('creator_build_summary_workflow_mode') },
                ];
                summaryEl.classList.remove('builder-hidden');
                summaryEl.innerHTML =
                    '<div class="creator-card-kicker">' + escapeHtml(t('creator_build_summary_title')) + '</div>' +
                    '<div class="builder-summary-grid">' +
                        summaryStats.map(function (item) {
                            return '<div class="builder-summary-stat">' +
                                '<div class="builder-summary-stat-value">' + escapeHtml(item.value) + '</div>' +
                                '<div class="builder-summary-stat-label">' + escapeHtml(item.label) + '</div>' +
                            '</div>';
                        }).join('') +
                    '</div>';
            }

            var gridEl = $('builder-persona-grid');
            if (gridEl && experts) {
                gridEl.innerHTML = experts.map(function (expert, i) {
                    var isPreset = expert.source !== 'generated';
                    var badgeClass = isPreset ? 'builder-persona-badge-preset' : 'builder-persona-badge-generated';
                    var badgeText = getDynamicDisplayText(isPreset ? (expert.matched_preset || expert.source || 'preset') : 'custom', 'persona');
                    var previewSource = getDynamicDisplayText(getPersonaPreview(expert) || t('creator_persona_missing_preview'), 'persona');
                    var personaValue = getDynamicDisplayText(String(expert.persona || ''), 'persona');
                    var displayName = getDynamicDisplayText(expert.name, 'persona');
                    var tagText = (expert.tag || '') + ' · T=' + (expert.temperature || 0.7);
                    var personaHint = isPreset
                        ? t('creator_persona_origin_preset_hint')
                        : t('creator_persona_origin_generated_hint');

                    return (
                        '<div class="builder-persona-card">' +
                            '<div class="builder-persona-top">' +
                                '<div class="builder-persona-avatar" style="background:' + avatarColor(i) + '">' + avatarLetter(displayName) + '</div>' +
                                '<div class="builder-persona-info">' +
                                    '<div class="builder-persona-name" title="' + escapeHtml(displayName) + '">' + escapeHtml(displayName) + '</div>' +
                                    '<div class="builder-persona-tag" title="' + escapeHtml(tagText) + '">' + escapeHtml(tagText) + '</div>' +
                                '</div>' +
                                '<span class="builder-persona-badge ' + badgeClass + '" title="' + escapeHtml(badgeText) + '">' + escapeHtml(badgeText) + '</span>' +
                            '</div>' +
                            '<div class="builder-persona-preview" title="' + escapeHtml(previewSource) + '">' + escapeHtml(previewSource) + '</div>' +
                            '<label class="builder-persona-field">' +
                                '<span class="builder-persona-field-label">' + escapeHtml(t('creator_role_persona_label')) + '</span>' +
                                '<span class="builder-persona-field-hint">' + escapeHtml(personaHint) + '</span>' +
                                '<textarea class="builder-persona-input" data-persona-idx="' + i + '" rows="8">' + escapeHtml(personaValue) + '</textarea>' +
                            '</label>' +
                            '<div class="builder-persona-footer">' +
                                '<span class="builder-persona-origin">' + escapeHtml(isPreset ? t('creator_persona_origin_preset_label') : t('creator_persona_origin_generated_label')) + '</span>' +
                                '<span class="builder-persona-count">' + escapeHtml(t('creator_chars_label', { count: personaValue.length })) + '</span>' +
                            '</div>' +
                        '</div>'
                    );
                }).join('');
            }

            var workflowEl = $('builder-workflow');
            var yamlEl = $('builder-yaml-code');
            if (workflowEl && config.yaml_workflow && config.oasis_experts) {
                workflowEl.classList.remove('builder-hidden');
                renderDagCanvas(config);
                if (yamlEl) yamlEl.textContent = config.yaml_workflow;
            }
            schedulePersistBuilderState();
        }

        function syncPersonaOverride(index, value) {
            if (!state.teamConfig || !state.teamConfig.oasis_experts || !state.teamConfig.oasis_experts[index]) return;
            var expert = state.teamConfig.oasis_experts[index];
            expert.persona = value;
            (state.teamConfig.internal_agents || []).forEach(function (agent) {
                if (!agent) return;
                if ((agent.tag && agent.tag === expert.tag) || (agent.name && agent.name === expert.name)) {
                    agent.persona = value;
                }
            });
        }

        function handlePersonaInput(e) {
            var input = e.target.closest('.builder-persona-input');
            if (!input) return;
            var idx = parseInt(input.dataset.personaIdx, 10);
            if (isNaN(idx)) return;
            syncPersonaOverride(idx, input.value);
            var card = input.closest('.builder-persona-card');
            var countEl = card && card.querySelector('.builder-persona-count');
            if (countEl) countEl.textContent = t('creator_chars_label', { count: input.value.length });
            schedulePersistBuilderState();
        }

        function handlePersonaChange(e) {
            var input = e.target.closest('.builder-persona-input');
            if (!input) return;
            setPreviewStatus('Persona 已更新，下载 ZIP / 导入团队会使用当前版本', 'success');
        }

        // ── TAG_EMOJI map (matches ClawcrossHub + Agency categories) ──
        var TAG_EMOJI = {
            creative: '🎨', critical: '🔍', data: '📊', synthesis: '🎯',
            economist: '📈', lawyer: '⚖️', cost_controller: '💰',
            revenue_planner: '📊', entrepreneur: '🚀', common_person: '🧑',
        };
        var CAT_EMOJI = {
            design: '🎨', engineering: '⚙️', marketing: '📢',
            product: '📦', 'project-management': '📋',
            'spatial-computing': '🥽', specialized: '🔬',
            support: '🛡️', testing: '🧪',
        };

        function getCreatorTextLayout() {
            if (typeof window === 'undefined') return null;
            return window.ClawcrossTextLayout || null;
        }

        function getDagNodeLabel(node) {
            var fallback = String((node && (node.name || node.role_name || node.tag)) || 'Untitled');
            return compactText(fallback) || 'Untitled';
        }

        function getDagNodeTagLabel(node) {
            if (!node) return 'expert';
            if (node.type === 'manual') {
                if (node.author === 'begin') return 'begin boundary';
                if (node.author === 'bend') return 'end boundary';
                return String(node.author || 'manual');
            }
            return String(node.tag || 'expert');
        }

        function buildFallbackGraphLayout(experts, roles) {
            var HGAP = 74, VGAP = 26, PAD = 30;
            var nameToIdx = {};
            var metricsByIdx = {};
            experts.forEach(function (e, i) { nameToIdx[e.name] = i; });
            experts.forEach(function (e, i) { metricsByIdx[i] = getDagNodeMetrics(e); });
            var adj = {};
            var inDeg = {};
            experts.forEach(function (_, i) { adj[i] = []; inDeg[i] = 0; });

            roles.forEach(function (role) {
                var tgtIdx = nameToIdx[role.role_name];
                if (tgtIdx === undefined) return;
                (role.depends_on || []).forEach(function (depName) {
                    var srcIdx = nameToIdx[depName];
                    if (srcIdx !== undefined && srcIdx !== tgtIdx) {
                        adj[srcIdx].push(tgtIdx);
                        inDeg[tgtIdx]++;
                    }
                });
            });

            var columns = [];
            var assigned = {};
            var queue = [];
            experts.forEach(function (_, i) {
                if (inDeg[i] === 0) queue.push(i);
            });
            while (queue.length > 0) {
                columns.push(queue.slice());
                queue.forEach(function (id) { assigned[id] = true; });
                var next = [];
                queue.forEach(function (id) {
                    (adj[id] || []).forEach(function (tgt) {
                        inDeg[tgt]--;
                        if (inDeg[tgt] === 0 && !assigned[tgt]) next.push(tgt);
                    });
                });
                queue = next;
            }
            experts.forEach(function (_, i) {
                if (!assigned[i]) { columns.push([i]); assigned[i] = true; }
            });

            var hasEdges = false;
            experts.forEach(function (_, i) { if (adj[i] && adj[i].length) hasEdges = true; });

            var nodes = [];
            var cx = PAD;
            var maxBottom = PAD, widestCol = 0;
            columns.forEach(function (col) {
                var colWidth = 0;
                col.forEach(function (idx) {
                    colWidth = Math.max(colWidth, (metricsByIdx[idx] && metricsByIdx[idx].w) || 150);
                });
                widestCol = Math.max(widestCol, colWidth);
                var startY = PAD;
                col.forEach(function (idx, rowIdx) {
                    var e = experts[idx];
                    var metrics = metricsByIdx[idx] || getDagNodeMetrics(e);
                    var ny = startY;
                    nodes.push({
                        id: idx,
                        x: cx + Math.round((colWidth - metrics.w) / 2),
                        y: ny,
                        w: metrics.w,
                        h: metrics.h,
                        name: e.name, tag: e.tag, persona: e.persona || '',
                        temperature: e.temperature, source: e.source || '',
                        matched_preset: e.matched_preset || '',
                        displayNameText: metrics.displayNameText,
                        displayTagText: metrics.displayTagText,
                        infoWidth: metrics.infoWidth,
                    });
                    maxBottom = Math.max(maxBottom, ny + metrics.h);
                    startY += metrics.h + VGAP;
                });
                cx += colWidth + HGAP;
            });

            if (maxBottom > PAD + 54) {
                var centerY = (maxBottom + PAD) / 2;
                var colMap = {};
                nodes.forEach(function (n) { if (!colMap[n.x]) colMap[n.x] = []; colMap[n.x].push(n); });
                Object.keys(colMap).forEach(function (cx) {
                    var colNodes = colMap[cx];
                    if (colNodes.length === 1) colNodes[0].y = centerY - colNodes[0].h / 2;
                });
            }

            var edges = [];
            if (hasEdges) {
                experts.forEach(function (_, srcIdx) {
                    (adj[srcIdx] || []).forEach(function (tgtIdx) { edges.push({ source: srcIdx, target: tgtIdx }); });
                });
            } else if (nodes.length > 1) {
                for (var i = 0; i < nodes.length - 1; i++) edges.push({ source: nodes[i].id, target: nodes[i + 1].id });
            }

            var totalW = PAD, totalH = PAD;
            nodes.forEach(function (n) { totalW = Math.max(totalW, n.x + n.w + PAD); totalH = Math.max(totalH, n.y + n.h + PAD); });
            totalW = Math.max(totalW, PAD + widestCol + PAD);
            return { width: totalW, height: totalH, nodes: nodes, edges: edges, conditionalEdges: [], selectorEdges: [] };
        }

        function getDagNodeMetrics(node) {
            var textLayout = getCreatorTextLayout();
            var label = getDagNodeLabel(node);
            var tagLabel = getDagNodeTagLabel(node);
            var typeConfig = node && node.type === 'manual'
                ? { minW: 146, maxW: 182, minH: 70, nameWidth: 118, maxLines: 1 }
                : node && node.isSelector
                    ? { minW: 192, maxW: 248, minH: 92, nameWidth: 166, maxLines: 2 }
                    : node && node.tag === 'all'
                        ? { minW: 184, maxW: 232, minH: 86, nameWidth: 154, maxLines: 2 }
                        : { minW: 174, maxW: 224, minH: 84, nameWidth: 148, maxLines: 2 };
            if (!textLayout || typeof textLayout.measureDisplay !== 'function') {
                return {
                    w: typeConfig.minW,
                    h: typeConfig.minH,
                    displayNameText: label,
                    displayTagText: tagLabel,
                    infoWidth: typeConfig.nameWidth,
                };
            }
            var nameMeasure = textLayout.measureDisplay(label, {
                font: '700 13px Arial',
                lineHeight: 16,
                maxWidth: typeConfig.nameWidth,
                maxLines: typeConfig.maxLines,
                suffix: '…',
            });
            var tagMeasure = textLayout.fitSingleLine(tagLabel, typeConfig.nameWidth, {
                font: '10px Menlo',
                lineHeight: 12,
                suffix: '…',
            });
            var contentWidth = Math.max(Math.ceil(nameMeasure.width), Math.ceil(tagMeasure.width));
            var w = Math.max(typeConfig.minW, Math.min(typeConfig.maxW, contentWidth + 58));
            var infoWidth = Math.max(94, w - 58);
            return {
                w: Math.round(w),
                h: Math.round(Math.max(typeConfig.minH, 44 + Math.ceil(nameMeasure.height) + 12)),
                displayNameText: (nameMeasure.lines || [nameMeasure.text || label]).join('\n'),
                displayTagText: tagMeasure.text || tagLabel,
                infoWidth: infoWidth,
            };
        }

        function buildWorkflowLayout(config) {
            var workflowLayout = config && config.workflow_layout;
            if (workflowLayout && Array.isArray(workflowLayout.nodes) && workflowLayout.nodes.length) {
                var nodes = workflowLayout.nodes.map(function (node) {
                    var metrics = getDagNodeMetrics(node || {});
                var normalizedNode = {};
                Object.keys(node || {}).forEach(function (key) { normalizedNode[key] = node[key]; });
                normalizedNode.w = metrics.w;
                normalizedNode.h = metrics.h;
                normalizedNode.displayNameText = metrics.displayNameText;
                normalizedNode.displayTagText = metrics.displayTagText;
                normalizedNode.infoWidth = metrics.infoWidth;
                return normalizedNode;
            });

                var totalW = 40;
                var totalH = 40;
                nodes.forEach(function (node) {
                    totalW = Math.max(totalW, (node.x || 0) + node.w + 40);
                    totalH = Math.max(totalH, (node.y || 0) + node.h + 40);
                });

                return {
                    width: totalW,
                    height: totalH,
                    nodes: nodes,
                    edges: Array.isArray(workflowLayout.edges) ? workflowLayout.edges : [],
                    conditionalEdges: Array.isArray(workflowLayout.conditionalEdges) ? workflowLayout.conditionalEdges : [],
                    selectorEdges: Array.isArray(workflowLayout.selectorEdges) ? workflowLayout.selectorEdges : [],
                };
            }

            return buildFallbackGraphLayout(config.oasis_experts || [], collectRolesFromUI('builder-roles-list'));
        }

        function buildExpertLookup(config) {
            var lookup = { byTag: {}, byName: {} };
            (config.oasis_experts || []).forEach(function (expert) {
                if (expert.tag) lookup.byTag[expert.tag] = expert;
                if (expert.name) lookup.byName[expert.name] = expert;
            });
            return lookup;
        }

        function getEdgeGeometry(sourceNode, targetNode) {
            var x1 = sourceNode.x + sourceNode.w;
            var y1 = sourceNode.y + sourceNode.h / 2;
            var x2 = targetNode.x;
            var y2 = targetNode.y + targetNode.h / 2;
            var cpx = (x1 + x2) / 2;
            var isLoop = x2 < x1 - 8;
            var path = isLoop
                ? 'M' + x1 + ',' + y1 + ' C' + (x1 + 60) + ',' + (y1 - 74) + ' ' + (x2 - 60) + ',' + (y2 - 74) + ' ' + x2 + ',' + y2
                : 'M' + x1 + ',' + y1 + ' C' + cpx + ',' + y1 + ' ' + cpx + ',' + y2 + ' ' + x2 + ',' + y2;
            return {
                path: path,
                labelX: isLoop ? (x1 + x2) / 2 : cpx,
                labelY: isLoop ? Math.min(y1, y2) - 44 : ((y1 + y2) / 2) - 12,
            };
        }

        function edgeLabelSvg(x, y, text, kind) {
            var textLayout = getCreatorTextLayout();
            var measurement = textLayout && typeof textLayout.fitSingleLine === 'function'
                ? textLayout.fitSingleLine(text || '', 150, {
                    font: '600 11px Arial',
                    lineHeight: 14,
                    suffix: '…',
                })
                : { text: String(text || ''), width: Math.max(18, String(text || '').length * 7) };
            var label = String(measurement.text || text || '');
            var width = Math.max(34, Math.ceil(measurement.width || 0) + 16);
            var left = Math.round(x - width / 2);
            var top = Math.round(y - 11);
            return '<g class="fg-edge-label fg-edge-label-' + kind + '" transform="translate(' + left + ',' + top + ')">' +
                '<rect width="' + width + '" height="22" rx="11"></rect>' +
                '<text x="' + Math.round(width / 2) + '" y="15">' + escapeHtml(label) + '</text>' +
            '</g>';
        }

        function renderDagCanvas(config) {
            var canvas = $('builder-dag-canvas');
            var inner = $('builder-dag-inner');
            if (!canvas || !inner) return;

            var layout = buildWorkflowLayout(config);
            var expertLookup = buildExpertLookup(config);

            var w = Math.max(860, layout.width);
            var h = Math.max(320, layout.height);
            inner.style.width = w + 'px';
            inner.style.height = h + 'px';

            var nodeById = {};
            layout.nodes.forEach(function (n) { nodeById[n.id] = n; });

            var svgHtml = '<svg class="fg-edges" width="' + w + '" height="' + h + '">' +
                '<defs>' +
                    '<marker id="fg-arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#58a6ff" /></marker>' +
                    '<marker id="fg-arrow-green" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#3fb950" /></marker>' +
                    '<marker id="fg-arrow-amber" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#d97706" /></marker>' +
                    '<marker id="fg-arrow-rose" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="#dc2626" /></marker>' +
                '</defs>';

            layout.edges.forEach(function (edge) {
                var src = nodeById[edge.source];
                var tgt = nodeById[edge.target];
                if (!src || !tgt) return;
                var geo = getEdgeGeometry(src, tgt);
                svgHtml += '<path d="' + geo.path + '" stroke="#58a6ff" stroke-width="2" fill="none" marker-end="url(#fg-arrow)" opacity="0.82" />';
            });

            (layout.selectorEdges || []).forEach(function (entry) {
                var src = nodeById[entry.source];
                if (!src || !entry.choices) return;
                Object.keys(entry.choices).sort(function (a, b) {
                    var aNum = parseInt(a, 10);
                    var bNum = parseInt(b, 10);
                    if (!isNaN(aNum) && !isNaN(bNum)) return aNum - bNum;
                    return String(a).localeCompare(String(b));
                }).forEach(function (choiceKey) {
                    var tgt = nodeById[entry.choices[choiceKey]];
                    if (!tgt) return;
                    var geo = getEdgeGeometry(src, tgt);
                    svgHtml += '<path d="' + geo.path + '" stroke="#3fb950" stroke-width="2" fill="none" stroke-dasharray="7 6" marker-end="url(#fg-arrow-green)" opacity="0.9" />';
                    svgHtml += edgeLabelSvg(geo.labelX, geo.labelY, '#' + choiceKey, 'selector');
                });
            });

            (layout.conditionalEdges || []).forEach(function (entry) {
                var src = nodeById[entry.source];
                var thenNode = nodeById[entry.then];
                if (src && thenNode) {
                    var thenGeo = getEdgeGeometry(src, thenNode);
                    svgHtml += '<path d="' + thenGeo.path + '" stroke="#d97706" stroke-width="2" fill="none" stroke-dasharray="8 5" marker-end="url(#fg-arrow-amber)" opacity="0.9" />';
                    svgHtml += edgeLabelSvg(thenGeo.labelX, thenGeo.labelY, 'IF', 'conditional');
                }
                if (src && entry.else && nodeById[entry.else]) {
                    var elseGeo = getEdgeGeometry(src, nodeById[entry.else]);
                    svgHtml += '<path d="' + elseGeo.path + '" stroke="#dc2626" stroke-width="2" fill="none" stroke-dasharray="8 5" marker-end="url(#fg-arrow-rose)" opacity="0.85" />';
                    svgHtml += edgeLabelSvg(elseGeo.labelX, elseGeo.labelY, 'ELSE', 'else');
                }
            });
            svgHtml += '</svg>';

            var nodesHtml = '';
            layout.nodes.forEach(function (node) {
                var expert = expertLookup.byTag[node.tag] || expertLookup.byName[node.name];
                var isManual = node.type === 'manual';
                var isSelector = !!node.isSelector;
                var emoji = node.emoji || (isManual
                    ? (node.author === 'begin' ? '🚀' : (node.author === 'bend' ? '🏁' : '📝'))
                    : (TAG_EMOJI[node.tag] || CAT_EMOJI[node.tag] || '⭐'));
                var personaText = expert ? String(expert.persona || '') : String(node.content || '');
                var personaShort = personaText.slice(0, 220);
                if (personaText.length > 220) personaShort += '…';
                var sourceLabel = isManual
                    ? 'manual'
                    : (expert && expert.matched_preset ? 'preset: ' + expert.matched_preset : ((expert && expert.source) || node.source || 'custom'));
                var tagLabel = isManual
                    ? (node.author === 'begin' ? 'begin boundary' : (node.author === 'bend' ? 'end boundary' : (node.author || 'manual')))
                    : (node.tag || 'expert');
                var displayNameText = node.displayNameText || node.name;
                var displayTagText = node.displayTagText || tagLabel;
                var infoWidth = node.infoWidth || Math.max(94, node.w - 58);
                var nodeClass = 'fg-node' +
                    (isManual ? ' fg-node-manual' : '') +
                    (isSelector ? ' fg-node-selector' : '') +
                    (node.tag === 'all' ? ' fg-node-all' : '');
                var badgeHtml = isSelector
                    ? '<span class="fg-node-badge fg-node-badge-selector">Selector</span>'
                    : (isManual ? '<span class="fg-node-badge fg-node-badge-manual">' + escapeHtml(node.author === 'begin' ? 'BEGIN' : (node.author === 'bend' ? 'END' : 'MANUAL')) + '</span>' : '');
                var instructionHtml = node.content ? '<div class="tt-instruction">' + escapeHtml(node.content) + '</div>' : '';

                nodesHtml +=
                    '<div class="' + nodeClass + '" style="left:' + node.x + 'px;top:' + node.y + 'px;width:' + node.w + 'px;height:' + node.h + 'px;">' +
                        '<div class="fg-port port-in"></div>' +
                        '<span class="fg-emoji">' + emoji + '</span>' +
                        '<div class="fg-info" style="max-width:' + infoWidth + 'px;"><div class="fg-name" style="max-width:' + infoWidth + 'px;">' + escapeHtml(displayNameText) + '</div><div class="fg-tag" style="max-width:' + infoWidth + 'px;">' + escapeHtml(displayTagText) + '</div></div>' +
                        badgeHtml +
                        '<div class="fg-port port-out"></div>' +
                        '<div class="fg-tooltip">' +
                            '<div class="tt-name">' + emoji + ' ' + escapeHtml(node.name) + '</div>' +
                            '<div class="tt-tag">tag: ' + escapeHtml(tagLabel) + '</div>' +
                            instructionHtml +
                            (personaShort ? '<div class="tt-persona">' + escapeHtml(personaShort) + '</div>' : '') +
                            (!isManual ? '<div class="tt-temp">🌡️ temperature: ' + ((expert && expert.temperature) || node.temperature || 0.7) + '</div>' : '') +
                            '<div class="tt-source">' + escapeHtml(sourceLabel) + '</div>' +
                        '</div>' +
                    '</div>';
            });

            inner.innerHTML = svgHtml + nodesHtml;
            initDagCanvasEngine(canvas, inner, layout);
        }

        function initDagCanvasEngine(container, inner, layout) {
            var fgState = { zoom: 1, panX: 0, panY: 0, panning: null };

            function applyTransform() {
                inner.style.transform = 'translate(' + fgState.panX + 'px,' + fgState.panY + 'px) scale(' + fgState.zoom + ')';
                var label = container.querySelector('.fg-zoom-label');
                if (label) label.textContent = Math.round(fgState.zoom * 100) + '%';
            }

            function autoFit() {
                var cw = container.offsetWidth || 600;
                var ch = container.offsetHeight || 280;
                var iw = parseInt(inner.style.width) || cw;
                var ih = parseInt(inner.style.height) || ch;
                var fitZoom = Math.min(cw / iw, ch / ih, 1);
                if (fitZoom < 1) {
                    fgState.zoom = fitZoom * 0.9;
                    fgState.panX = (cw - iw * fgState.zoom) / 2;
                    fgState.panY = (ch - ih * fgState.zoom) / 2;
                } else {
                    fgState.zoom = 1;
                    fgState.panX = (cw - iw) / 2;
                    fgState.panY = (ch - ih) / 2;
                }
                applyTransform();
            }

            requestAnimationFrame(function () { requestAnimationFrame(autoFit); });

            container.addEventListener('wheel', function (e) {
                e.preventDefault();
                var rect = container.getBoundingClientRect();
                var mx = e.clientX - rect.left, my = e.clientY - rect.top;
                var oldZoom = fgState.zoom;
                var delta = e.deltaY > 0 ? -0.08 : 0.08;
                fgState.zoom = Math.min(3, Math.max(0.15, oldZoom + delta));
                fgState.panX = mx - (mx - fgState.panX) * (fgState.zoom / oldZoom);
                fgState.panY = my - (my - fgState.panY) * (fgState.zoom / oldZoom);
                applyTransform();
            }, { passive: false });

            container.addEventListener('mousedown', function (e) {
                if (e.button !== 0 && e.button !== 1) return;
                var tgt = e.target;
                var isBlank = tgt === container || tgt.classList.contains('builder-dag-inner') || tgt.tagName === 'svg';
                if (e.button === 0 && !isBlank) return;
                e.preventDefault();
                fgState.panning = { startX: e.clientX, startY: e.clientY, origPanX: fgState.panX, origPanY: fgState.panY };
                container.classList.add('fg-grabbing');
            });

            document.addEventListener('mousemove', function (e) {
                if (!fgState.panning) return;
                var p = fgState.panning;
                fgState.panX = p.origPanX + (e.clientX - p.startX);
                fgState.panY = p.origPanY + (e.clientY - p.startY);
                applyTransform();
            });

            document.addEventListener('mouseup', function () {
                if (fgState.panning) { fgState.panning = null; container.classList.remove('fg-grabbing'); }
            });

            function fgZoom(delta) {
                var rect = container.getBoundingClientRect();
                var mx = rect.width / 2, my = rect.height / 2;
                var oldZoom = fgState.zoom;
                fgState.zoom = Math.min(3, Math.max(0.15, oldZoom + delta));
                fgState.panX = mx - (mx - fgState.panX) * (fgState.zoom / oldZoom);
                fgState.panY = my - (my - fgState.panY) * (fgState.zoom / oldZoom);
                applyTransform();
            }

            var navIn = container.querySelector('[data-fg-action="zoom-in"]');
            var navOut = container.querySelector('[data-fg-action="zoom-out"]');
            var navReset = container.querySelector('[data-fg-action="reset"]');
            if (navIn) navIn.addEventListener('click', function () { fgZoom(0.15); });
            if (navOut) navOut.addEventListener('click', function () { fgZoom(-0.15); });
            if (navReset) navReset.addEventListener('click', autoFit);
        }

        // ── Download ZIP ──
        async function downloadZip() {
            if (!state.teamConfig) {
                setPreviewStatus('请先构建团队', 'warning');
                return;
            }
            var teamName = ($('builder-team-name') || {}).value || 'team';
            var btn = $('builder-download-btn');
            if (btn) {
                btn.disabled = true;
                setDynamicText(btn, '⏳ 下载中...', { context: 'status' });
            }
            setPreviewStatus('正在生成 ZIP...', 'running');
            try {
                var resp = await fetch('/api/team-creator/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ team_name: teamName.trim(), team_config: state.teamConfig }),
                });
                if (!resp.ok) {
                    var errText = await resp.text();
                    var errMsg = 'Download failed';
                    try { var errData = JSON.parse(errText); errMsg = errData.error || errMsg; } catch (e) { errMsg = errText || errMsg; }
                    throw new Error(errMsg);
                }
                var contentType = resp.headers.get('content-type') || '';
                if (contentType.indexOf('application/zip') === -1 && contentType.indexOf('application/octet') === -1) {
                    // Server returned JSON error instead of ZIP
                    var bodyText = await resp.text();
                    try { var errData = JSON.parse(bodyText); throw new Error(errData.error || 'Server returned non-ZIP response'); } catch (e) { if (e.message.indexOf('non-ZIP') !== -1) throw e; throw new Error('Unexpected response type: ' + contentType); }
                }
                var blob = await resp.blob();
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = getDownloadFilename(resp, teamName.trim().replace(/\s+/g, '_') + '_team.zip');
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
                setPreviewStatus('ZIP 已下载 ✅', 'success');
            } catch (err) {
                setPreviewStatus('下载失败: ' + (err.message || String(err)), 'error');
                console.error('[ClawcrossCreator] downloadZip error:', err);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = t('creator_download_btn');
                }
            }
        }

        // ── Import to Clawcross ──
        async function importToClawcross() {
            if (!state.teamConfig) return;
            var baseName = (($('builder-team-name') || {}).value || 'team').trim();
            setPreviewStatus('正在导入...', 'running');
            try {
                // Resolve non-conflicting team name (_v1, _v2, ...)
                var teamsResp = await fetch('/teams');
                var teamsData = teamsResp.ok ? await teamsResp.json() : {};
                var existing = new Set(teamsData.teams || []);
                var teamName = baseName;
                if (existing.has(teamName)) {
                    var i = 1;
                    while (existing.has(baseName + '_v' + i)) i++;
                    teamName = baseName + '_v' + i;
                }

                var resp = await fetch('/api/team-creator/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ team_name: teamName, team_config: state.teamConfig }),
                });
                if (!resp.ok) throw new Error('Failed to generate ZIP');
                var blob = await resp.blob();
                var formData = new FormData();
                formData.append('file', blob, teamName + '_team.zip');
                formData.append('team', teamName);
                var uploadResp = await fetch('/teams/snapshot/upload', { method: 'POST', body: formData });
                var uploadData = await uploadResp.json();
                if (!uploadResp.ok) throw new Error(uploadData.error || 'Upload failed');
                setPreviewStatus('成功导入到 Clawcross！' + (teamName !== baseName ? '（团队名已调整为 ' + teamName + '）' : ''), 'success');
                setBuilderPill('已导入', 'success');
                // Show navigation dialog
                var overlay = document.createElement('div');
                overlay.className = 'builder-modal-overlay';
                overlay.innerHTML =
                    '<div class="builder-modal" style="text-align:center;max-width:360px;">' +
                        '<div style="font-size:36px;margin-bottom:12px;">✅</div>' +
                        '<h3 style="margin:0 0 8px;">导入成功</h3>' +
                        '<p class="builder-hint" style="margin-bottom:20px;">团队「' + escapeHtml(teamName) + '」已导入 Clawcross，是否前往查看？</p>' +
                        '<div class="builder-modal-actions">' +
                            '<button class="creator-btn creator-btn-secondary" id="creator-stay-btn" type="button">留在此页</button>' +
                            '<button class="creator-btn" id="creator-goto-btn" type="button">前往团队页面 →</button>' +
                        '</div>' +
                    '</div>';
                document.body.appendChild(overlay);
                overlay.querySelector('#creator-stay-btn').addEventListener('click', function () { overlay.remove(); });
                overlay.querySelector('#creator-goto-btn').addEventListener('click', function () {
                    window.location.href = '/studio?tab=group&team=' + encodeURIComponent(teamName);
                });
            } catch (err) {
                setPreviewStatus('导入失败: ' + err.message, 'error');
            }
        }

        // ── Load Expert Pool from /proxy_visual/experts (same API as 消息中心通讯录) ──
        async function loadExpertPool() {
            var el = $('expert-pool-list');
            if (!el) return;
            try {
                var resp = await fetch('/proxy_visual/experts');
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var experts = await resp.json();
                if (!Array.isArray(experts) || !experts.length) {
                    el.innerHTML = '<div class="creator-item"><div class="creator-item-subtitle">' + escapeHtml(t('creator_expert_pool_empty')) + '</div></div>';
                    return;
                }

                expertPoolCache = experts;
                refreshPresetRolesFromExpertPool();

                // Group by category (same logic as 消息中心通讯录)
                expertPoolByCat = {};
                experts.forEach(function (exp) {
                    var cat;
                    if (exp.source === 'custom' || exp.source === 'team') {
                        cat = '_custom';
                    } else if (exp.source === 'agency' && exp.category) {
                        cat = exp.category;
                    } else {
                        cat = '_public';
                    }
                    if (!expertPoolByCat[cat]) expertPoolByCat[cat] = [];
                    expertPoolByCat[cat].push(exp);
                });

                renderExpertPool(getExpertPoolFilterValue());
            } catch (err) {
                el.innerHTML = '<div class="creator-item"><div class="creator-item-subtitle">' + escapeHtml(t('creator_expert_pool_load_failed')) + ': ' + escapeHtml(err.message) + '</div></div>';
            }
        }

        function renderExpertPool(filter) {
            var el = $('expert-pool-list');
            if (!el) return;
            var filterLower = (filter || '').toLowerCase();

            // Sort categories: _public first, then alphabetical, _custom last
            var cats = Object.keys(expertPoolByCat).sort(function (a, b) {
                if (a === '_public') return -1;
                if (b === '_public') return 1;
                if (a === '_custom') return 1;
                if (b === '_custom') return -1;
                return a.localeCompare(b);
            });

            var html = '';
            var totalShown = 0;

            prefetchDynamicTranslations(
                cats.reduce(function (acc, cat) {
                    (expertPoolByCat[cat] || []).forEach(function (exp) {
                        acc.push(getLocalizedExpertName(exp));
                        acc.push(getLocalizedExpertSummary(exp));
                    });
                    return acc;
                }, []),
                { context: 'expert' }
            ).then(function (changed) {
                if (changed) renderExpertPool(filter);
            });

            cats.forEach(function (cat) {
                var items = expertPoolByCat[cat];
                // Apply filter
                var filtered = items;
                if (filterLower) {
                    filtered = items.filter(function (exp) {
                        return matchesExpertPoolFilter(exp, filterLower);
                    });
                }
                if (!filtered.length) return;
                totalShown += filtered.length;

                var info = CAT_LABELS[cat] || { icon: '📂', labelKey: null };
                var uid = 'epool-cat-' + cat.replace(/[^a-zA-Z0-9]/g, '_');
                var isPublic = (cat === '_public');
                var expanded = isPublic || !!filterLower;
                var catLabel = info.labelKey ? t(info.labelKey) : cat;

                html +=
                    '<div class="expert-pool-cat-group">' +
                        '<div class="expert-pool-cat-header' + (expanded ? ' expanded' : '') + '" data-cat-uid="' + uid + '">' +
                            '<span class="expert-pool-cat-icon">' + info.icon + '</span>' +
                            '<span class="expert-pool-cat-name">' + escapeHtml(catLabel) + '</span>' +
                            '<span class="expert-pool-cat-count">' + filtered.length + '</span>' +
                            '<span class="expert-pool-cat-arrow">▶</span>' +
                        '</div>' +
                        '<div id="' + uid + '" class="expert-pool-cat-list' + (expanded ? ' expanded' : '') + '">' +
                        filtered.map(function (exp, idx) {
                            var dname = getDynamicDisplayText(getLocalizedExpertName(exp), 'expert') || exp.tag || '?';
                            var emoji = exp.emoji || info.icon || '⭐';
                            var desc = getDynamicDisplayText(getLocalizedExpertSummary(exp), 'expert');
                            if (desc.length > 60) desc = desc.slice(0, 60) + '…';
                            var tagLabel = exp.tag || '';
                            return (
                                '<div class="expert-pool-item" data-expert-idx="' + idx + '" data-expert-cat="' + escapeHtml(cat) + '">' +
                                    '<div class="expert-pool-item-left">' +
                                        '<span class="expert-pool-item-emoji">' + emoji + '</span>' +
                                        '<div class="expert-pool-item-info">' +
                                            '<div class="expert-pool-item-name">' + escapeHtml(dname) + '</div>' +
                                            (desc ? '<div class="expert-pool-item-desc">' + escapeHtml(desc) + '</div>' : '') +
                                        '</div>' +
                                    '</div>' +
                                    '<div class="expert-pool-item-right">' +
                                        (tagLabel ? '<span class="expert-pool-item-tag">' + escapeHtml(tagLabel) + '</span>' : '') +
                                        '<button class="expert-pool-add-btn" type="button" title="' + escapeHtml(t('creator_expert_pool_add_title')) + '">+</button>' +
                                    '</div>' +
                                '</div>'
                            );
                        }).join('') +
                        '</div>' +
                    '</div>';
            });

            if (!totalShown) {
                html = '<div class="creator-item"><div class="creator-item-subtitle">' + escapeHtml(t('creator_expert_pool_no_match')) + '</div></div>';
            }

            el.innerHTML = html;
        }

        function handleExpertPoolClick(e) {
            // Toggle category collapse
            var catHeader = e.target.closest('.expert-pool-cat-header');
            if (catHeader) {
                var uid = catHeader.dataset.catUid;
                var listEl = document.getElementById(uid);
                if (listEl) {
                    catHeader.classList.toggle('expanded');
                    listEl.classList.toggle('expanded');
                }
                return;
            }

            // Add expert as role
            var addBtn = e.target.closest('.expert-pool-add-btn');
            var item = e.target.closest('.expert-pool-item');
            if (!item) return;

            var cat = item.dataset.expertCat;
            var idx = parseInt(item.dataset.expertIdx, 10);
            var catItems = expertPoolByCat[cat];
            if (!catItems || idx < 0 || idx >= catItems.length) return;

            // Apply current filter to get the correct item
            var filterVal = ($('expert-pool-search') || {}).value || '';
            var filterLower = filterVal.toLowerCase();
            var filtered = catItems;
            if (filterLower) {
                filtered = catItems.filter(function (exp) {
                    return matchesExpertPoolFilter(exp, filterLower);
                });
            }

            var expert = filtered[idx];
            if (expert) addExpertAsRole(expert);
        }

        function initExpertPoolSearch() {
            var searchInput = $('expert-pool-search');
            if (!searchInput) return;
            var debounceTimer = null;
            searchInput.addEventListener('input', function () {
                clearTimeout(debounceTimer);
                debounceTimer = setTimeout(function () {
                    renderExpertPool(searchInput.value);
                }, 200);
            });
        }

        function handleJobListClick(e) {
            var button = e.target.closest('.builder-job-item');
            if (!button) return;
            var jobId = String(button.dataset.jobId || '').trim();
            if (!jobId) return;
            openJob(jobId);
        }

        async function loadJobs() {
            var el = $('builder-jobs');
            if (!el) return;
            try {
                var resp = await fetch('/api/team-creator/jobs?limit=12');
                var data = {};
                try {
                    data = await resp.json();
                } catch (_parseErr) {
                    data = {};
                }
                if (!resp.ok || !data.ok) {
                    throw new Error(data.error || ('HTTP ' + resp.status));
                }
                var jobs = data.jobs || [];
                if (jobs.some(function (job) { return String(job.status || '').toLowerCase() === 'running'; })) {
                    if (!state.jobPollTimer) startJobPolling();
                } else {
                    clearJobPolling();
                }
                if (!jobs.length) {
                    el.innerHTML = '<div class="creator-item"><div class="creator-item-subtitle">' + escapeHtml(t('creator_jobs_empty')) + '</div></div>';
                    return;
                }
                el.innerHTML = jobs.map(function (j) {
                    var selected = state.selectedJobId && state.selectedJobId === j.job_id;
                    var tone = jobStatusTone(j.status);
                    var totalRoles = (j.team_config_summary && j.team_config_summary.total_roles) || j.extracted_roles_count || 0;
                    var meta = totalRoles
                        ? t('creator_job_meta_roles', { count: totalRoles, time: formatJobTimestamp(j.updated_at || j.created_at) })
                        : t('creator_job_meta_empty', { time: formatJobTimestamp(j.updated_at || j.created_at) });
                    var errorHtml = j.error
                        ? '<div class="creator-item-subtitle builder-job-error">' + escapeHtml(getDynamicDisplayText(j.error, 'status')) + '</div>'
                        : '';
                    return '<button class="creator-item builder-job-item' + (selected ? ' builder-job-item-active' : '') + '" type="button" data-job-id="' + escapeHtml(j.job_id || '') + '">' +
                        '<div class="builder-job-head">' +
                            '<div class="builder-job-title creator-item-title">' + escapeHtml(j.team_name || j.job_id) + '</div>' +
                            '<span class="builder-job-status builder-job-status-' + tone + '">' + escapeHtml(jobStatusLabel(j.status)) + '</span>' +
                        '</div>' +
                        '<div class="creator-item-subtitle">' + escapeHtml(meta) + '</div>' +
                        errorHtml +
                    '</button>';
                }).join('');
            } catch (err) {
                clearJobPolling();
                el.innerHTML = '<div class="creator-item"><div class="creator-item-subtitle">' + escapeHtml(t('creator_jobs_load_failed', { error: err.message || String(err) })) + '</div></div>';
            }
        }

        function applyImportedTeamConfig(teamConfig, successMessage) {
            state.teamConfig = teamConfig;
            if (teamConfig && teamConfig.oasis_experts) {
                state.roles = teamConfig.oasis_experts.map(function (expert) {
                    return {
                        role_name: expert.name || '',
                        personality_traits: [],
                        primary_responsibilities: [],
                        depends_on: [],
                        tools_used: [],
                        _expert_tag: expert.tag || '',
                        _full_persona: expert.persona || '',
                    };
                });
                setMode('direct');
                renderRoles();
            }
            if (successMessage) {
                setBuilderPill(t('creator_import_success'), 'success');
                setBuilderStatus(successMessage, 'success');
            }
            renderTeamPreview(teamConfig);
        }

        // ── Import Colleague Skill ──
        async function handleImportColleague() {
            var metaInput = $('import-colleague-meta');
            var personaInput = $('import-colleague-persona');
            var workInput = $('import-colleague-work');
            var dirInput = $('import-colleague-dir');
            var statusEl = $('import-colleague-status');
            var hasDirPath = !!(dirInput && String(dirInput.value || '').trim());

            if ((!metaInput || !metaInput.files || !metaInput.files.length) && !hasDirPath) {
                if (statusEl) statusEl.textContent = 'meta.json is required';
                return;
            }
            if ((!personaInput || !personaInput.files || !personaInput.files.length) && !hasDirPath) {
                if (statusEl) statusEl.textContent = 'persona.md is required';
                return;
            }

            if (statusEl) statusEl.textContent = '正在导入...';
            try {
                var metaJson = null;
                var personaMd = '';
                var workMd = (workInput && workInput.files && workInput.files.length) ? await workInput.files[0].text() : '';
                if (metaInput && metaInput.files && metaInput.files.length) {
                    var metaText = await metaInput.files[0].text();
                    metaJson = JSON.parse(metaText);
                }
                if (personaInput && personaInput.files && personaInput.files.length) {
                    personaMd = await personaInput.files[0].text();
                }

                var teamName = (($('builder-team-name') || {}).value || '').trim();
                var taskDesc = (($('builder-task-desc') || {}).value || '').trim();
                var colleagueDirPath = hasDirPath ? String(dirInput.value || '').trim() : '';

                var resp = await fetch('/api/team-creator/import-colleague', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        meta_json: metaJson,
                        persona_md: personaMd,
                        work_md: workMd,
                        colleague_dir_path: colleagueDirPath,
                        team_name: teamName,
                        task_description: taskDesc,
                    }),
                });
                var data = await resp.json();
                if (!resp.ok || !data.ok) throw new Error(data.error || t('creator_import_error'));

                if (statusEl) statusEl.textContent = t('creator_import_success');
                var colleagueName = (metaJson && metaJson.name) || (((data.summary || {}).colleague_meta || {}).name) || '同事';
                applyImportedTeamConfig(data.team_config, t('creator_import_success') + ' — ' + colleagueName + ' 已导入为团队角色');
            } catch (err) {
                if (statusEl) statusEl.textContent = t('creator_import_error') + ': ' + err.message;
                setBuilderStatus(t('creator_import_error') + ': ' + err.message, 'error');
            }
        }

        // ── Import Mentor Skill ──
        async function handleImportMentor() {
            var mentorInput = $('import-mentor-json');
            var skillInput = $('import-mentor-skill');
            var mentorPathInput = $('import-mentor-json-path');
            var skillPathInput = $('import-mentor-skill-path');
            var statusEl = $('import-mentor-status');
            var hasMentorPath = !!(mentorPathInput && String(mentorPathInput.value || '').trim());

            if ((!mentorInput || !mentorInput.files || !mentorInput.files.length) && !hasMentorPath) {
                if (statusEl) statusEl.textContent = '{name}.json is required';
                return;
            }

            if (statusEl) statusEl.textContent = '正在导入...';
            try {
                var mentorJson = null;
                if (mentorInput && mentorInput.files && mentorInput.files.length) {
                    var mentorText = await mentorInput.files[0].text();
                    mentorJson = JSON.parse(mentorText);
                }
                var skillMd = (skillInput && skillInput.files && skillInput.files.length) ? await skillInput.files[0].text() : '';

                var teamName = (($('builder-team-name') || {}).value || '').trim();
                var taskDesc = (($('builder-task-desc') || {}).value || '').trim();
                var mentorJsonPath = hasMentorPath ? String(mentorPathInput.value || '').trim() : '';
                var skillMdPath = (skillPathInput && String(skillPathInput.value || '').trim()) ? String(skillPathInput.value || '').trim() : '';

                var resp = await fetch('/api/team-creator/import-mentor', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        mentor_json: mentorJson,
                        skill_md: skillMd,
                        mentor_json_path: mentorJsonPath,
                        skill_md_path: skillMdPath,
                        team_name: teamName,
                        task_description: taskDesc,
                    }),
                });
                var data = await resp.json();
                if (!resp.ok || !data.ok) throw new Error(data.error || t('creator_import_error'));

                if (statusEl) statusEl.textContent = t('creator_import_success');
                var mentorName = (mentorJson && mentorJson.profile && (mentorJson.profile.name_zh || mentorJson.profile.name_en)) || (mentorJson && mentorJson.meta && mentorJson.meta.mentor_name) || (((data.summary || {}).mentor_meta || {}).name) || '导师';
                applyImportedTeamConfig(data.team_config, t('creator_import_success') + ' — ' + mentorName + ' 已导入为导师角色');
            } catch (err) {
                if (statusEl) statusEl.textContent = t('creator_import_error') + ': ' + err.message;
                setBuilderStatus(t('creator_import_error') + ': ' + err.message, 'error');
            }
        }

        async function handleGenerateColleagueFromFeishu() {
            var appId = (($('feishu-app-id') || {}).value || '').trim();
            var appSecret = (($('feishu-app-secret') || {}).value || '').trim();
            var targetName = (($('feishu-target-name') || {}).value || '').trim();
            var role = (($('feishu-role') || {}).value || '').trim();
            var company = (($('feishu-company') || {}).value || '').trim();
            var level = (($('feishu-level') || {}).value || '').trim();
            var personalityTags = (($('feishu-personality-tags') || {}).value || '').trim();
            var msgLimit = parseInt((($('feishu-msg-limit') || {}).value || '500'), 10);
            var statusEl = $('import-colleague-status');

            if (!appId || !appSecret || !targetName) {
                if (statusEl) statusEl.textContent = 'Feishu app_id / app_secret / target_name are required';
                return;
            }

            if (statusEl) statusEl.textContent = t('creator_generate_colleague_loading');
            try {
                var resp = await fetch('/api/team-creator/feishu-collect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        app_id: appId,
                        app_secret: appSecret,
                        target_name: targetName,
                        company: company,
                        role: role,
                        level: level,
                        msg_limit: isNaN(msgLimit) ? 500 : msgLimit,
                        personality_tags: personalityTags
                            ? personalityTags.split(',').map(function (item) { return item.trim(); }).filter(Boolean)
                            : [],
                        auto_distill: true,
                        auto_import: true,
                        team_name: (($('builder-team-name') || {}).value || '').trim(),
                        task_description: (($('builder-task-desc') || {}).value || '').trim(),
                    }),
                });
                var data = await resp.json();
                if (!resp.ok || !data.ok) throw new Error(data.error || t('creator_import_error'));

                var colleagueName = (((data.summary || {}).colleague_meta || {}).name) || targetName;
                var successText = t('creator_generate_colleague_success', { name: colleagueName }) +
                    ' · ' + String(data.messages_length || 0) + ' chars collected';
                if (statusEl) statusEl.textContent = successText;
                applyImportedTeamConfig(data.team_config, successText);
            } catch (err) {
                if (statusEl) statusEl.textContent = t('creator_import_error') + ': ' + err.message;
                setBuilderStatus(t('creator_import_error') + ': ' + err.message, 'error');
            }
        }

        async function handleGenerateMentorFromArxiv() {
            var authorName = (($('mentor-arxiv-name') || {}).value || '').trim();
            var affiliation = (($('mentor-arxiv-affiliation') || {}).value || '').trim();
            var maxResults = parseInt((($('mentor-arxiv-max-results') || {}).value || '20'), 10);
            var statusEl = $('import-mentor-status');

            if (!authorName) {
                if (statusEl) statusEl.textContent = 'author_name is required';
                return;
            }

            if (statusEl) statusEl.textContent = t('creator_generate_mentor_loading');
            try {
                var resp = await fetch('/api/team-creator/arxiv-search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        author_name: authorName,
                        affiliation: affiliation,
                        max_results: isNaN(maxResults) ? 20 : maxResults,
                        auto_import: true,
                        team_name: (($('builder-team-name') || {}).value || '').trim(),
                        task_description: (($('builder-task-desc') || {}).value || '').trim(),
                    }),
                });
                var data = await resp.json();
                if (!resp.ok || !data.ok) throw new Error(data.error || t('creator_import_error'));

                var mentorName = (((data.summary || {}).mentor_meta || {}).name) || authorName;
                var successText = t('creator_generate_mentor_success', {
                    name: mentorName,
                    count: data.papers_count || 0,
                });
                if (statusEl) statusEl.textContent = successText;
                applyImportedTeamConfig(data.team_config, successText);
            } catch (err) {
                if (statusEl) statusEl.textContent = t('creator_import_error') + ': ' + err.message;
                setBuilderStatus(t('creator_import_error') + ': ' + err.message, 'error');
            }
        }

        // ── Init ──
        function init() {
            // Mode switch
            $('builder-mode-direct').addEventListener('click', function () { setMode('direct'); });
            $('builder-mode-discover').addEventListener('click', function () { setMode('discover'); });
            $('builder-mode-import-colleague').addEventListener('click', function () { setMode('import-colleague'); });
            $('builder-mode-import-mentor').addEventListener('click', function () { setMode('import-mentor'); });

            // Import colleague handler
            $('import-colleague-btn').addEventListener('click', handleImportColleague);
            $('generate-colleague-btn').addEventListener('click', handleGenerateColleagueFromFeishu);
            // Import mentor handler
            $('import-mentor-btn').addEventListener('click', handleImportMentor);
            $('generate-mentor-btn').addEventListener('click', handleGenerateMentorFromArxiv);

            // Example instruction
            $('builder-example-btn').addEventListener('click', function () {
                var nameInput = $('builder-team-name');
                var descInput = $('builder-task-desc');
                if (nameInput) nameInput.value = t('creator_example_team_name');
                if (descInput) descInput.value = t('creator_example_task_desc');
                setBuilderPill('示例已注入', 'success');
                setBuilderStatus('已填入示例指令 — 你可以直接添加角色并构建，或修改描述后使用 AI 发现', 'success');
            });

            // Role editor
            $('builder-add-role').addEventListener('click', addEmptyRole);
            $('builder-paste-json').addEventListener('click', showJsonModal);
            $('builder-roles-list').addEventListener('click', handleRoleRemove);

            // Discovery
            $('builder-discover-btn').addEventListener('click', startDiscovery);
            $('builder-discover-stop').addEventListener('click', stopDiscovery);
            $('discovery-use-roles').addEventListener('click', useDiscoveredRoles);
            $('discovery-add-role').addEventListener('click', addDiscoveryRole);
            $('discovery-roles-list').addEventListener('click', handleRoleRemove);

            // Discovery selection mode
            $('discovery-mode-llm').addEventListener('click', function () { setDiscoverySelectMode('llm'); });
            $('discovery-mode-manual').addEventListener('click', function () { setDiscoverySelectMode('manual'); });
            $('discovery-llm-select-btn').addEventListener('click', runLlmSmartSelect);
            $('discovery-select-all').addEventListener('click', selectAllDiscoveryRoles);
            $('discovery-deselect-all').addEventListener('click', deselectAllDiscoveryRoles);

            // Discovery checkbox handler (event delegation)
            $('discovery-roles-list').addEventListener('change', function (e) {
                var cb = e.target.closest('.discovery-role-checkbox');
                if (!cb) return;
                var idx = parseInt(cb.dataset.roleIdx, 10);
                if (!isNaN(idx)) {
                    discoverySelected[idx] = cb.checked;
                    var card = cb.closest('.builder-role-card');
                    if (card) card.classList.toggle('discovery-role-selected', cb.checked);
                    updateDiscoverySelectSummary();
                    schedulePersistBuilderState();
                }
            });

            // Build / Download / Import
            $('builder-build-btn').addEventListener('click', buildTeam);
            $('builder-download-btn').addEventListener('click', downloadZip);
            $('builder-import-btn').addEventListener('click', importToClawcross);
            $('builder-persona-grid').addEventListener('input', handlePersonaInput);
            $('builder-persona-grid').addEventListener('change', handlePersonaChange);

            // Expert Pool
            var poolList = $('expert-pool-list');
            if (poolList) poolList.addEventListener('click', handleExpertPoolClick);
            var jobsList = $('builder-jobs');
            if (jobsList) jobsList.addEventListener('click', handleJobListClick);
            initExpertPoolSearch();
            bindPersistenceListeners();

            // Initial renders
            var restored = restoreBuilderState();
            if (!restored) renderRoles();
            loadExpertPool();
            loadJobs();
            loadTinyfishStatus();
        }

        function refreshI18n() {
            var currentRoles = collectRolesFromUI('builder-roles-list');
            var currentDiscoveredRoles = collectRolesFromUI('discovery-roles-list');

            if (currentRoles.length || state.roles.length) {
                state.roles = currentRoles;
            }
            if (currentDiscoveredRoles.length || state.discoveredRoles.length) {
                state.discoveredRoles = currentDiscoveredRoles;
            }

            renderRoles();
            renderDiscoveryRoles();
            renderDiscoveredPages(state.discoveredPages || []);
            refreshExtractionSessionI18n();
            if (state.teamConfig) {
                renderTeamPreview(state.teamConfig);
            }
            if (expertPoolCache.length || Object.keys(expertPoolByCat).length) {
                renderExpertPool(getExpertPoolFilterValue());
            }
            loadJobs();
            loadTinyfishStatus();
        }

        return { init: init, refreshI18n: refreshI18n, getDagNodeMetrics: getDagNodeMetrics };
    })();

    // ─── Bootstrap ───────────────────────────────────
    window.__ClawcrossCreatorBuilder = BuilderModule;

    window.addEventListener('DOMContentLoaded', function () {
        BuilderModule.init();
        applyTranslations();
    });

    window.addEventListener('storage', function (event) {
        if (!event || (event.key !== 'lang' && event.key !== 'clawcross_lang')) return;
        var nextLang = readCurrentLang();
        if (nextLang === currentLang) return;
        currentLang = nextLang;
        applyTranslations();
    });
})();
