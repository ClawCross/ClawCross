# 模型能力与思考强度

WeBot 读取已安装的 LangChain `model.profile` 数据，其次读取仓库中的
`src/backend/common/model_catalog.json`。目录记录 2026-10-03 手动获取的
OpenClaw 公开目录快照（MIT，来源和提交版本保存在 JSON 中）。启动和请求不会下载目录。

维护者可手动运行 `python tools/maintenance/update_model_catalog.py`，或用
`--input <快照文件>` 离线更新。供应商和模型 ID 精确匹配；允许去掉供应商前缀，
不再通过 `gpt`、`claude` 等名字片段猜测视觉能力、窗口或费用。

用户显式的 `LLM_VISION_SUPPORT` 和上下文容量优先。上下文容量为 0 时使用
本地模型数据，未知模型沿用 1M 回退值，可手动覆盖；环境变量 `LLM_CONTEXT_WINDOW`
也可覆盖自动值。1M 回退值不代表供应商保证支持。

＋ → 高级选项中的上下文设置会显示已知模型的思考强度。用户默认设置可被会话覆盖，
每次 WeBot 调用读取最新值。只给适配器传入已知支持的档位；未知模型隐藏选项，
切换模型后不支持的旧档位不会发送。外部 ACP Agent 继续使用其原生配置。

费用按固定快照计算并标记 `snapshot_estimate`。未知价格标记 `unavailable`，
不虚构费率；汇总存在未知价格时标记 `partial_unavailable`，金额只包含已知部分。
服务商实际账单可能因价格变更或代理加价不同。

前端 CSS 在开发阶段通过 `npm run build:css` 编译并提交，启动不安装 Tailwind。
Town 图形组件只有打开对应视图后下载。图片在浏览器压缩；群消息发送前检查
512 KiB 总量限制，无法解码的 HEIC 会提示转换格式，不额外下载转换组件。
