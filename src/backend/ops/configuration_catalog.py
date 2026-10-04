"""Server-owned configuration fields. A tool cannot invent fields or form HTML."""
from __future__ import annotations


def field(name, label, help, *, type='text', human_only=False, **kwargs):
    return dict(name=name, label=label, help=help, type=type, human_only=human_only, **kwargs)


CATALOG = {
    'model': {'label': '模型连接', 'scope': 'host', 'help': '此主机所有用户共用的内部模型连接。保存后需重新加载服务；不自动重启。外部 Codex/Claude 的原生模型在各自 Agent 设置中选择。', 'fields': [
        field('LLM_PROVIDER', '服务商', '留空自动判断；例如 openai、deepseek、anthropic、gemini。'),
        field('LLM_MODEL', '模型名称', '填写服务商实际支持的模型编号；不会自动测试或产生 API 调用。'),
        field('LLM_BASE_URL', 'API 地址', '服务商的 API 入口。此地址决定密钥发送到哪里，请本人确认。', human_only=True),
        field('LLM_API_KEY', 'API 密钥', '仅私密表单输入，留空保留已存密钥。', type='password', human_only=True),
        field('LLM_VISION_SUPPORT', '图片支持', '留空自动识别；true 支持图片，false 仅文字。', type='select', options=['', 'true', 'false'])]},
    'voice': {'label': '语音输出', 'scope': 'host', 'help': '主机的语音合成模型和音色。留空使用服务商推荐设置；重新加载后生效。', 'fields': [
        field('TTS_MODEL', '语音模型', '例如 gpt-4o-mini-tts；需要服务商支持。'),
        field('TTS_VOICE', '音色', '例如 alloy；合法音色由服务商决定。')]},
    'search': {'label': 'TinyFish 搜索连接', 'scope': 'host', 'help': '配置可选搜索服务。仅保存设置，不安装、不发送请求；重新加载后生效。', 'fields': [
        field('TINYFISH_API_KEY', 'TinyFish 密钥', '到服务商后台获取，只通过本表单提交。', type='password', human_only=True),
        field('TINYFISH_BASE_URL', 'API 地址', '留空保持现有地址；更换地址请本人确认。', human_only=True)]},
    'context': {'label': '对话上下文与压缩', 'scope': 'agent', 'section': 'context', 'help': '仅当前 Agent，下次调用生效。0 表示自动预算；压缩保留摘要和最近轮次。', 'fields': [
        field('auto_compact', '自动压缩', '接近预算时压缩历史。', type='boolean'),
        field('context_window_tokens', '上下文容量', '0 自动；手动值至少 4096。', type='number', min=0, max=4000000),
        field('history_tokens', '历史预算', '0 自动；限制历史可用 token。', type='number', min=0, max=4000000),
        field('trigger_tokens', '压缩触发点', '0 自动；不能超过历史预算。', type='number', min=0, max=4000000),
        field('target_tokens', '压缩目标', '摘要与保留原文的总量；0 为历史预算的 10%，最多 10,000 tokens；应小于触发点。', type='number', min=0, max=4000000),
        field('preserve_recent_turns', '优先保留最近轮次', '在 token 预算内优先保留原文；过长轮次可以按完整工具交换边界压缩。', type='number', min=1, max=100),
        field('summary_tokens', '摘要预算', '默认上限 8,000；摘要优先占总目标的最多 80%，小窗口自动缩减，剩余用于最新完整工具交换。', type='number', min=128, max=32000),
        field('summarizer_input_tokens', '摘要输入预算', '给摘要模型留出历史、旧摘要和规则空间。', type='number', min=1024, max=128000),
        field('summarizer_model', '摘要模型', '留空沿用当前内部模型。'),
        field('preserve_instructions', '压缩保留事项', '说明摘要应保留的任务事实；不改变工具权限。', type='textarea')]},
    'approval': {'label': '审核与命令沙盒', 'scope': 'agent', 'section': 'approval', 'help': '仅当前 Agent，下次工具调用生效。安全设置由用户本人选择，Agent 不能预填。组件安装仍需在对应设置页显式操作。', 'fields': [
        field('mode', '工具模式', 'chat 无工具；readonly 只读；manual 按策略人工审核；auto 模型审核；bypass 跳过审核，仍受明确拒绝和沙盒限制。', type='select', options=['chat','readonly','manual','auto','bypass'], human_only=True),
        field('command_sandbox', '命令隔离后端', 'off 不启用；auto 自动选择；srt 使用 SRT；landlock 适用于 Linux。Windows 的沙盒资源限制尚未实现，会拒绝沙盒命令。', type='select', options=['off','auto','srt','landlock'], human_only=True),
        field('sandbox_security', '安全等级', 'standard 可在审核后增加限定权限；strict 始终限制在干净工作区，不允许提权。', type='select', options=['standard','strict'], human_only=True),
        field('reviewer_model', '审核模型', '留空沿用内部模型配置。审核是独立 API 调用。'),
        field('reviewer_policy', '补充审核规则', '附加约束；无法放宽服务器最大权限。', type='textarea', human_only=True),
        field('reviewer_timeout_seconds', '审核超时', '最长等待秒数。', type='number', min=5, max=120),
        field('reviewer_max_tokens', '审核输出预算', '输出按 JSON schema 约束；给足预算避免截断。', type='number', min=1024, max=16384)]},
    'inference': {'label': '内部模型思考强度', 'scope': 'agent', 'section': 'inference', 'help': '仅当前 WeBot Agent。外部 Agent 的原生选项请在其设置页选择具体值。', 'fields': [
        field('reasoning_effort', '思考强度', '留空由模型决定；仅支持此参数的模型生效。', type='select', options=['','none','off','minimal','low','medium','high','xhigh','max'])]},
}
