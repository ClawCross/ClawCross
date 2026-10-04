# npm 打包

直接从当前源码目录打包，不需要维护另一份项目副本。

```bash
npm run pack:clawcross
```

产物是 `dist/clawcross-<version>.tgz`；`dist/` 仅存放生成的压缩包，已从 Git 和 npm 文件清单排除。这个命令不发布、不安装组件，也不启动服务。

`package.json` 的 `files` 列出运行所需的 Python 服务、启动入口、前端文件、配置模板、提示词和团队预设。真实 `.env`、用户文件、数据库、测试、交接记录和自演进报告不进入压缩包。根目录 `.npmignore` 不会覆盖显式选入的 `files`，因此配置文件逐个列出，没有把整个 `config/` 加进去。规则见 [npm package.json 文档](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/#files)。

`tools/build/` 还负责前端与团队预设构建，不是废弃的打包副本，应当保留。Phaser、EasyStar 和 Pretext 只在构建时使用，前端运行使用已提交的 bundle，因此放入 `devDependencies`。Playwright 的 Node 模块仍用于浏览器工具，保留运行依赖；浏览器二进制安装仍是单独的显式操作。

发布前验证生成包中的 CLI 版本/帮助、Python 服务和新前端文件是否齐全，使用临时 `CLAWCROSS_HOME`，避免接触实际用户数据。修改包版本不会修改现有运行目录。

Windows 启动入口保留。当前 Windows 的限制是开启 SRT 沙盒后，由于资源限制未适配，沙盒命令会拒绝执行；这不代表 Windows 不能启动项目或聊天，也不能通过重新打包消除这项限制。
