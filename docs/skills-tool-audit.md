# Skill 正文作为 Memory 条目

内置 Agent 不再通过独立 Skill 管理工具或技能目录路径修改技能。Skill 正文使用通用文件工具的 `storage="memory"` 模式，按稳定编号或名称访问。底层继续使用已有 SKILL.md，不需要搬迁旧数据。

| 工具 | Memory 模式 |
| --- | --- |
| `list_files` | 返回编号、名称、说明、作用域；不返回路径或支持文件列表 |
| `read_file` | 按编号或名称读取 Skill 正文，支持原有分块读取和 sha256 |
| `write_file` | 用新名称创建条目，用编号更新；支持覆盖、追加、字符串替换和 sha256 并发保护 |
| `delete_file` | 只删除正文条目；支持文件保留 |

```python
list_files(storage="memory")
write_file(storage="memory", filename="部署经验", content="# 部署经验\n记录本次确认的操作流程。")
read_file(storage="memory", filename="list 返回的编号")
write_file(storage="memory", filename="list 返回的编号", mode="str_replace", old_string="旧步骤", new_string="新步骤")
```

新内容可直接写 Markdown，系统补齐名称和说明；覆盖已有正文时保留原有 frontmatter。写入仍校验格式、大小和危险内容模式，并更新索引；同一用户和 Team 范围的 Memory 写入串行化。Memory 模式拒绝路径以及跨目录符号链接，错误和返回元信息不会暴露存储路径。现有 Skill 文本自身包含的路径不会被擅自删除。

团队会话默认使用团队范围；显式 `team=""` 访问个人范围。团队列表和读取可查看共享个人条目，写入、删除不会自动回退到个人范围。同名歧义时必须使用编号。支持文件使用普通 `storage="file"` 的文件工具，按正常路径规则访问；Memory 模式不承担支持文件管理或脚本执行。

独立 `skill_evolution_report` 保留，在前端“用量与报告”中展示。它按编号或名称分析近期失败，返回启发式候选和验证建议；不写入正文，不代表验证已经通过。Agent 阅读后自行选择改进，再调用 `write_file(storage="memory")`。

`skill_manage`、`skill_view`、`skill_evolution_apply` 不再向模型注册。旧正文调用兼容映射到 Memory 文件操作；旧支持文件操作不会被重新解释成正文写入，旧自动 apply 调用只返回报告。旧 Skill 专用授权不会自动扩大成普通文件权限。REST 和操作员 CLI 的 Skill 功能保留。
