迁移过的对话关掉桥接后能接着聊，1M 版换成官方版 · Moved conversations carry on with the bridge off, 1M ones on the official version

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **迁移过的对话关掉桥接后不再报错**：以前迁移只改了对话文件第一行的 provider，后面几行
  （`turn_context`、`thread_settings_applied`）里还记着桥接的模型名。Codex 按这些重建索引时，会把
  对话改回桥接的模型名，关掉桥接后走官方账号或中转站都会失败。现在这几行里的模型名和 provider 也会改，
  每处只原地换掉这一个值，文件其余内容、对话内容和修改时间都不变；`threads undo` 会一并改回。
- **1M 版的对话换成同一模型的官方版**：官方没有 1M 版，迁移时 `gpt-6-sol-1m-excel` 换成 `gpt-6-sol`
  （272k）。开着桥接时想继续用 1M，在模型菜单里再选回来。
- **0.5.6 到 0.5.8 迁移过的对话自动补齐**：升级后在 Codex 完全退出时启动一次桥接，窗口里会显示
  `Finished N conversation(s) moved by an earlier excel-codex`。`excel-codex threads` 也会列出还没补齐的。
- **关掉桥接后报 `os error 10061`（由于目标计算机积极拒绝，无法连接）、“正在重新连接 x/5”**：这是
  Codex 还在运行（Windows 上关掉窗口后还在托盘里），开着桥接时打开的对话还连着已关掉的桥接。从托盘
  图标右键退出 Codex 再打开即可。桥接窗口启动和关闭时都会提示；关闭时如果还看得到 Codex 进程，会多一句
  `Codex is still running right now`。

0.5.8 的变化（被限流时最多等 5 分钟）见
[v0.5.8 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8)；0.5.7 的变化（限流等待、
旧的桥接对话启动时自动并进共享列表）见
[v0.5.7 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.9-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.9-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.9-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.9/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.9/excel-codex-bridge-0.5.9-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.9-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Moved conversations no longer fail with the bridge off**: migrating used to change only the
  provider on the first line of a conversation file, and later lines (`turn_context`,
  `thread_settings_applied`) still named the bridge's models. Codex rebuilds its index from those, so
  it could put the bridge's model names back, and with the bridge off the conversation failed through
  the official sign-in or a relay. Now the model and provider on those lines change too, each value
  in place: the rest of the file, the conversation itself and the file's modification time stay as
  they are; `threads undo` puts them back as well.
- **1M conversations carry on with the same model's official version**: OpenAI has no 1M versions,
  so migrating turns `gpt-6-sol-1m-excel` into `gpt-6-sol` (272k). Pick a 1M model again in the model
  menu to use one with the bridge on.
- **Conversations moved by 0.5.6 to 0.5.8 are finished**: after upgrading, start the bridge once
  while Codex is fully quit; the window says `Finished N conversation(s) moved by an earlier
  excel-codex`. `excel-codex threads` lists the ones not finished yet.
- **`os error 10061` (connection refused) and "Reconnecting x/5" after the bridge is closed**: Codex
  is still running (on Windows, closing its window leaves it in the tray), and conversations opened
  with the bridge on still go to it. Quit Codex from its tray icon and open it again. The bridge
  window says so when it starts and when it closes; closing while it still sees a Codex process, it
  adds `Codex is still running right now`.

For 0.5.8's changes (up to 5 minutes' wait under the rate limit), see the
[v0.5.8 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8); for 0.5.7's
(the rate-limit wait, the bridge's earlier conversations moved into the shared list at start), the
[v0.5.7 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7); for what 0.5.4
added (conversations shared with the official sign-in, choosing the image model, the exit timezone),
the [v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.9-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.9-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.9-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.9/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.9/excel-codex-bridge-0.5.9-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.9-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
