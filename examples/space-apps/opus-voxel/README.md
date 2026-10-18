# 方块世界 / Voxel World

A ClawCross mini app adapted from [X1aoZhe/OpusMC](https://github.com/X1aoZhe/OpusMC),
pinned to `ca677140a47e668aeb2cfab65d4b3057f786f25d`.

- Upstream game source: MIT, copyright (c) 2026 Jeff. See `vendor/opusmc/LICENSE`.
- Upstream `assets.js` is **not included**: its Minecraft textures, GUI images and font
  are explicitly excluded from the upstream MIT license.
- The default pack is original pixel art drawn from code by `pixel-art.js` (blocks, items,
  particles, HUD, containers laid out on the engine's real slots, sky and biome colour maps);
  `procedural-assets.js` turns it into the engine's `window.ASSETS`. See the source list in
  [COMMERCIAL-ART.md](COMMERCIAL-ART.md). The small `asset-layout.json` contains compatible
  names and dimensions, not image data.
- The app keeps its sandboxed opaque origin. Pointer lock requires a user gesture.
  Touch devices get directional movement, swipe camera, jump, dig and place controls.
- Saves go to the host, scoped to the verified member ID, rather than relying on
  IndexedDB in a sandboxed frame. Changing devices preserves saves when signing in
  as the same group member. Other members cannot read those worlds.
- This is a **single-player** voxel game. Joining the same ClawCross group does not
  synchronize players into a shared Minecraft world. No vanilla-protocol multiplayer
  is implemented or advertised.

Select **方块世界** in a local group's app picker, or in a self-hosted Hub lobby.
Nothing is downloaded on launch. WebGL2 is required; performance depends on the device.
The host runner supplies `CLAWCROSS_SPACE_DATA_DIR` outside Agent workspaces.

## 超平坦、JSON 建造与 Agent 控制

`world-tools.js` 从外部给引擎打补丁（与 `touch.js` 相同），上游引擎文件保持原样。

**超平坦地层**：创建新的世界 → 世界类型：超平坦 → **超平坦设置**。从下到上写，用逗号分隔，`2*dirt` 表示两层泥土，例如 `bedrock,2*dirt,grass_block`。预设「默认」就是引擎原来的平坦世界。地层存进世界存档，出生点在最上层之上。

**JSON 建造**：游戏菜单 → **导入建筑…**，可以粘贴或选择文件；也可以用下面的 API。

```json
{"origin": [0, 61, 0], "blocks": [[0, 0, 0, "stone"], [0, 1, 0, "glass"], [1, 0, 0, "oak_planks", 0]]}
```

坐标相对 `origin`（API 默认 `[0,0,0]`；游戏内导入默认是你脚下）。第 4 项是方块名（见 `blocks` 命令），第 5 项是可选的 meta，`"air"` 表示删除。一次最多 500000 个方块。建造只放置方块：液体不会流动，箱子等容器是空的。

**Agent 控制 API**：游戏菜单 → **Agent 控制…** 显示这个成员的 API 地址。**这个地址等同你的身份，只交给信任的 agent。** 页面要开着并进入世界：命令由页面里的游戏执行，页面在后台时浏览器会暂停游戏，移动不会继续。世界没打开时，`build` 先存进存档，下次打开该世界时自动建造。

| 请求 | 说明 |
|---|---|
| `GET api` | 命令说明 |
| `GET api/state` | 位置、朝向、血量、手持与物品栏、准星方块、时间 |
| `POST api/commands` | `{"commands":[…]}` 按顺序执行，最多 50 条，返回每条的 `{ok, result \| error}` |
| `POST api/build` | `{"world"?, "origin"?, "blocks"}` |

命令：`state`、`blocks`、`inspect {from,to}`（最多 32768 格）、`move {forward,strafe,jump,sneak,sprint,ticks}`（每秒 20 刻，最多 200）、`look {yaw,pitch}` 或 `{at:[x,y,z]}`（角度）、`select {slot}`、`dig {x,y,z}`、`place {x,y,z,block?}`、`chat {text}`（可发 `/tp`、`/time`、`/give` 等）、`wait {ticks}`、`build {origin,blocks}`。挖掘和放置要在够得着的距离内；生存模式放置需要背包里有该方块。

## 个人开发测试素材开关

标题页 **选项 → 材质与开发测试 → 完整上游素材**。默认关闭，使用原创程序化素材。切换后重载页面，不修改世界；进行游戏时先保存并退出世界，再切换。

该开关只影响群主本人的画面，其他成员仍使用默认原创材质。选择按群成员身份保存，重启后保留。没有测试文件时自动回到原创材质，不尝试联网下载。

测试文件独立于仓库、Agent 工作区和 npm 包。部署者可将有权测试的兼容 `assets.js` 放在：

```
~/.clawcross/developer-assets/opusmc-ca677140a47e668aeb2cfab65d4b3057f786f25d/assets.js
```

或者显式设置主机环境变量 `CLAWCROSS_VOXEL_TEST_ASSETS` 指向该文件。设置为空字符串可禁用发现。源代码中不包含、启动时不下载此文件。

上游的完整包包含 Minecraft 官方图像、字体和 GUI，它们 **不属于 MIT**。个人开发测试开关不是授权证明，不使这些素材自动获得再发布或商用许可。不要把测试目录放进公开发行物。商业化改造计划见 [COMMERCIAL-ART.md](COMMERCIAL-ART.md)。
