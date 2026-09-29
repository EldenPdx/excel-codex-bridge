升级后模型菜单变回旧版时说清原因、手动 serve 跟着更新模型列表 · Says why the model menu is an old one after an update; manual serve updates the model list

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **升级后模型菜单还是旧的，现在能看出原因**：桌面版只在启动时读取模型列表，从没在托盘里完全退出过的话，
  升级后菜单里还是旧版的模型（比如 0.5.1 及更早版本只有 5.6-Sol、6-Astra、5.6-Terra、5.6-Luna，没有 6-Sol、
  6-Luna 和 1M 版）。现在桥接窗口启动时看到 Codex 在运行，会提示先从托盘退出再打开；窗口里
  `now use the Excel bridge 0.5.15` 这一行写着正在运行的版本，打开的是旧文件夹里的旧版时一眼就能看出来。
- **全手动用法（`serve` + `print-config`）升级后模型列表跟着更新**：以前 `serve` 不更新 `print-config`
  片段指向的模型列表文件，菜单一直停在当初运行 `print-config` 的那个版本；现在 `serve` 每次启动都会更新它。
- 详见 README 的
  [在 Codex 桌面版 / IDE 插件中使用](https://github.com/Kaixxrua/excel-codex-bridge#在-codex-桌面版--ide-插件中使用)。

0.5.13 起双击 `excel-codex-desktop.cmd` 会自动装上这一版；0.5.12 及更早的版本需要手动下载替换一次。

0.5.14 的变化（中转站 / Cockpit 下不再报 `exec is not a tool in the catalog`）见
[v0.5.14 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.14)；0.5.13 的变化（双击 `excel-codex-desktop.cmd` 自动更新）见
[v0.5.13 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.13)；0.5.12 的变化（子代理不再报加密内容无法解码、老会话的工具调用不再越错越多）见
[v0.5.12 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.12)；0.5.11 的变化（桌面版不再卡加载、网络闪断自动重连、长对话不再报请求过大、系统时区不再来回跳）见
[v0.5.11 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.11)；0.5.10 的变化（中转站名下的对话并到 `openai`）见
[v0.5.10 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.10)；0.5.9 的变化（普通版到 450k 才压缩、迁移过的对话关掉桥接后能接着聊）见
[v0.5.9 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9)；0.5.8 的变化（被限流时最多等 5 分钟）见
[v0.5.8 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8)；0.5.7 的变化（限流等待、
旧的桥接对话启动时自动并进共享列表）见
[v0.5.7 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.15-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.15-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.15-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.15/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.15/excel-codex-bridge-0.5.15-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.15-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Why the model menu is an old one after an update now shows**: the desktop app reads the model
  list only when it starts, so one never fully quit from the tray keeps an older release's models
  after an update (0.5.1 and earlier, for instance, list only 5.6-Sol, 6-Astra, 5.6-Terra and
  5.6-Luna, without 6-Sol, 6-Luna and the 1M versions). The bridge window now says to quit it from the
  tray and open it again when it starts while it sees Codex running, and its
  `now use the Excel bridge 0.5.15` line names the release it runs, which shows at a glance when an old
  folder's copy was opened.
- **Fully manual use (`serve` plus `print-config`) gets the new model list after an update**: `serve`
  did not update the model list file the `print-config` lines point at, so the menu stayed at the
  release `print-config` was run with; `serve` now updates it each time it starts.
- See [Codex desktop app / IDE extension](https://github.com/Kaixxrua/excel-codex-bridge/blob/main/README.en.md#codex-desktop-app--ide-extension)
  in the README.

From 0.5.13, double-clicking `excel-codex-desktop.cmd` installs this release by itself; 0.5.12 and
earlier need it downloaded and replaced by hand once.

For 0.5.14's changes (no more `exec is not a tool in the catalog` through relays and Cockpit), see the
[v0.5.14 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.14); for 0.5.13's changes (`excel-codex-desktop.cmd` updating itself), see the
[v0.5.13 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.13); for 0.5.12's changes (subagents getting their task as text, older conversations no longer failing tool
calls more and more), see the
[v0.5.12 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.12); for 0.5.11's changes (no more stuck loading, reconnects after network drops, long conversations
fitting, the system timezone holding still), see the
[v0.5.11 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.11); for 0.5.10's changes (a relay's conversations moving under `openai`), see the
[v0.5.10 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.10); for 0.5.9's changes (standard models compacting at 450k, moved conversations carrying on with the
bridge off), see the [v0.5.9 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9); for
0.5.8's (up to 5 minutes' wait under the rate limit), the
[v0.5.8 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8); for 0.5.7's
(the rate-limit wait, the bridge's earlier conversations moved into the shared list at start), the
[v0.5.7 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7); for what 0.5.4
added (conversations shared with the official sign-in, choosing the image model, the exit timezone),
the [v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.15-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.15-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.15-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.15/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.15/excel-codex-bridge-0.5.15-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.15-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
