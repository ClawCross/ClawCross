# 小程序开发指南

## 小程序是什么

小程序是附着在聊天群上的独立网页应用，可以是游戏、协作看板、课程或资料页。
同一群保留成员、邀请和 Agent 通讯能力，网页应用负责自己的页面、交互、规则与存档。
访问者通过 ClawCross 客户端或邀请链接的浏览器进入同一小程序，无须为每个小程序更新客户端。
当前已有精灵对战、方块世界、共享画板、闪卡学堂、协作写作等例子。

Agent 可以帮助开发，也可以作为应用中的设计师、主持人或助手。小程序运行时是否调用 Agent 由应用决定；不需要给前端 API Key。
精灵对战的 AI 设计师生成 SVG 形象、介绍和技能动画，竞技场则由玩家选择背包精灵出招，不要求每次点击都调用模型。

## 架构与部署位置

小程序进程必须和群服务器运行在同一主机。SDK 在 `127.0.0.1` 开一个 HTTP 端口，向本机群服务器登记。
用户访问群链接，群服务检查成员身份后代理页面、HTTP、SSE 和 WebSocket；无需直接暴露应用端口。
本机群的小程序在本机运行；Hub 上创建的群，小程序在 Hub 主机运行。Agent 本身可以来自其他设备。
不要把本机应用登记到远程群服务器，也不要再开发远程应用端口转发协议。

本机运行的群和小程序可通过显式开启 Hub 公网访问分享，获得设备子域名。Cloudflare 是可选方式，不自动下载。
没有公网地址时先向用户解释分享选项；不要擅自开启公网服务。

## 文件布局与发现

运行器会扫描两类目录：

```text
<ClawCross 项目>/examples/space-apps/<应用名>/server.js   # 随项目提供的例子
<ClawCross 数据主目录>/space-apps/<应用名>/server.js      # 用户自己的小程序
```

数据主目录由运行时决定，默认是 `~/.clawcross`；不要假设一定是仓库里的 `data/`。
用户目录的同名应用覆盖内置版本。新应用只需加入自己的目录，不必改 Agent/gateway 或群协议。
`server.js` 顶部注释第一行写应用名，增加 `简介：` 行供启动列表展示。应用自己的前端、图片、样式可随目录存放。
Node 版本至少 18，SDK 本身不额外安装依赖；应用如需依赖应明确声明并由用户安装，不在启动时暗中下载。

## SDK 选择

当前源码中的 SDK 位于 `sdk/js/`，属于 ClawCross 发布包的一部分：

| 文件 | 用途 |
| --- | --- |
| `clawcross-space.js` | 基础连接、成员、Agent 请求、事件、HTTP 与 WebSocket 服务 |
| `space-app.js` | `run/page` 页面工具、路由、按成员筛选状态、Agent 请求队列和 JSON 回复 |
| `space-app-theme.js` | 响应式页面、组件、明暗主题 |
| `space-store.js` | 应用数据的原子落盘保存 |

优先参考一个接近需求的例子。简单页面可用 `flashcards` 或 `task-board`；实时多视图、存档和 AI 设计可参考 `creature-battle`。
内置例子的相对导入是 `require('../../../sdk/js/space-app.js')`。用户目录不具有这个相对位置：先确认本机 ClawCross 安装目录，再使用其 SDK 绝对路径，或把所需 SDK 及其依赖文件作为应用包一起交付；不要照抄导致找不到模块。

## 最小示例

以下例子放在源码的 `examples/space-apps/my-counter/server.js`，在有群身份的运行器中启动：

```js
/* 一起计数
 * 简介：群成员共享一个计数器，每次点击都通知其他人。
 */
const {run, page} = require('../../../sdk/js/space-app.js');

function setup(app) {
  let count = 0;
  app.page(page({
    title: '一起计数',
    body: '<h1>一起计数</h1><p id="count"></p><button id="add">加一</button>',
    script: `
      App.watch(s => {
        document.getElementById('count').textContent = s.state.count;
      });
      document.getElementById('add').onclick = () =>
        App.act(() => App.api('add', {}));
    `
  }));
  app.state(member => ({count}));
  app.route('POST', '/add', (member, body) => {
    count += 1;
    app.changed();
  });
}

if (require.main === module) {
  run({name: '一起计数'}, setup).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
module.exports = {name: '一起计数', setup};
```

这个示例计数只在内存中，重启归零；需要持久化时参照「状态与存档」。请求中的 `member` 由群服务器验证，不由浏览器传入。

## 调用 Agent 与结构化输出

基础 SDK：`space.members()` 返回成员，`space.ask(agentId, text)` 请求指定成员的 Agent，`space.on('agent', handler)` 接收输出。
应用工具层：`app.ask` / `app.askJSON` 管理请求队列；`app.agentsOf(member.id)` 按当前 SDK 的同设备关系筛选 Agent。
这不是通用的用户授权检查：共享设备的应用还应根据自己的所有权记录核对调用范围；只调用当前玩家有权使用的群成员 Agent。
先查询实际成员，不允许浏览器随便提交任意 Agent ID。

将动态规则、用户指令与请求 ID 放入本次请求，要求 Agent 按约定 JSON 格式回复；结果只影响应用状态。
应用应验证字段、长度、请求 ID、发起人和当前版本。过期回复、取消任务和串用户回复不能覆盖当前状态。
Agent 通过群工具回复；收件箱、动态块、模式和工具审批仍由原运行时执行，小程序不会绕过它们。
Agent 生成的文字用 `textContent` 或安全 Markdown 渲染，SVG 用 SDK 的 `safeSvg` 等校验；不执行模型输出的任意脚本。

## 状态与存档

共享状态由服务端持有，`app.state(member => ...)` 只返回该成员可见的数据；变更后调用 `app.changed()`。
私密背包、草稿、设计对话和密钥不能放到向所有成员返回的状态里。
WebSocket/SSE 也必须保留成员身份与授权检查。

运行器提供 `CLAWCROSS_SPACE_DATA_DIR`，基础 SDK 可用 `space.dataDir`；这个目录按群/应用隔离，位于 Agent 工作区外。
可使用 `new Store(directory, initial)`，`store.update(draft => ...)` 暂存变更后原子替换存档；写失败不能把最后一份好数据覆盖掉。
奖励、扣款和重复消息需要请求 ID 或已处理标记，刷新和重试不能重复发奖励。
页面的 iframe 是隔离源，不要依赖与 ClawCross 共享的 Cookie、localStorage 或 IndexedDB；需要长期保存的数据写到应用服务端。

## 实时页面与输入焦点

基础 SDK 支持流式 HTTP、SSE 和原生 WebSocket。`space.serve(handler, {upgrade})` 可处理升级请求。
`space-app.js` 的 `App.watch` 默认用带版本号的长轮询；它也能平滑更新页面，不必每次重建所有 DOM。
保持输入框、选择器、画布、游戏场景和 iframe 的节点身份，只更新变化的数据。
需要复用节点时用 `App.keep(id, make)`，不要在定时刷新中重新 `focus()` 或覆盖仍在输入的草稿。
断线后重连并恢复服务端状态；按钮显示忙碌/失败状态，避免重复提交。
用相对 URL（如 `fetch('state')`），不要写死 `/state`、本机端口或设备域名，否则群代理前缀会丢失。

## 安全边界与凭证

启动器注入 `CLAWCROSS_SPACE_SERVER`、`CLAWCROSS_SPACE_KEY`、`CLAWCROSS_SPACE_LOCAL_TOKEN` 和存档目录。
应用 key 只属于当前群；本机控制 token 只放服务端。SDK 完成登记和认证，不把 token 写进页面、日志、群公告或模型输入。
每次页面请求和 WebSocket 握手都会验证群成员；密钥轮换/断开后旧入口失效。
`req.member` 是可信身份来源；禁止用 POST 参数中的 member/user/owner 覆盖它。
`app.state` 和后端路由仍需检查谁有权修改什么，群成员身份不等于管理员权限。

前端由 sandbox iframe 和响应 CSP 隔离，不共享群网站权限。这个机制隔离网页权限，不是 Node 进程的 OS 沙箱。
自定义小程序是主机执行的代码，部署者需要信任它。不要声称 Agent 的命令沙盒会自动覆盖整个应用进程。

## 启动、分享与停止

1. 在运行应用的主机创建群，把需要的 Agent 加入。
2. 群右上角菜单 → 小程序，选择应用并启动。启动前检查环境与依赖；运行器提供凭证，用户不用把 key 发给模型。
3. 在同一群验证页面和功能。新成员可通过邀请链接或二维码进入，浏览器与客户端看到同一应用。
4. 本机群需要公网访问时由用户显式开启 Hub 公网入口；应用无需另开公网端口。
5. 停止结束进程；断开小程序恢复群聊。正常运行器管理的本机应用会随 ClawCross 重启恢复，存档应能读取。

Hub 的部署者选择可运行的应用目录；普通远程成员不能拿 app key 在自己的机器挂接任意服务。
应用目录更新后重启对应小程序进程即可，不必更新所有客户端。

## 开发验证与排错

优先用临时群、独立存档和 fixture Agent 验证，不读取真实私聊、不向无关成员发测试消息。
至少检查手机竖屏、短横屏、桌面三种尺寸，确认主要按键可见、无横向溢出、刷新不抢输入焦点。
测试双方成员隔离、非法操作、结构化输出错误、重复提交、断线重连和重启后读档。

- 启动列表没有应用：检查用户目录、`server.js` 与注释，再刷新应用列表。
- 找不到 SDK：用户目录没有内置例子的相对层级，改成实际安装路径或完整打包 SDK。
- 应用离线：看运行器日志、Node 版本与依赖，确认应用和群服务器同机；不要盲目换公网端口。
- 页面空白：检查相对 URL、浏览器错误、CSP/iframe 能力与服务端响应。
- Agent 没回复：查成员及归属、工具范围、群回复通道、请求 ID 与日志，不反复发送。
- 状态丢失：检查是否只是内存变量、存档目录是否正确、是否用稳定成员 ID。

源码例子测试入口为 `node test/test_space_apps.js <应用目录名>` 和 `node test/test_space_app_layouts.js <应用目录名>`。
底层协议和更多例子见 [space-apps.md](space-apps.md)。
