旧的桥接对话自动并进共享列表，关掉桥接也能打开 · The bridge's own conversations move into the shared list by themselves and open with the bridge off

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **旧的桥接对话自动迁移**：0.5.3 及更早版本、或 Codex 没登录时经桥接建的对话，记在 `excel-bridge`
  名下。关掉桥接后，桌面版打开它们会报“Model provider `excel-bridge` not found”。现在
  `excel-codex-desktop.cmd` 和 `excel-codex` 启动时，如果 Codex 已完全退出，就自动把它们并进共享列表，
  不用再手动运行命令。所以要**先开桥接、再开桌面版**；Codex 开着时不迁移，窗口里会提示一句。
- **0.5.4、0.5.5 的 `threads migrate` 迁移不彻底**：它只改了 Codex 的对话索引，而 Codex 会按对话文件
  第一行记的 provider 重建索引，对话于是又回到 `excel-bridge`，关掉桥接照样报错。现在连这一行一起改，
  只改 provider 这一个字段，文件其余内容和修改时间都不变。以前迁移过的对话会自动补齐，迁移后改名、
  归档也不会再变回去。
- `excel-codex threads migrate` 和 `undo` 在 Codex 运行时不执行，因为 Codex 会把改动改回去。设置
  `EXCEL_BRIDGE_AUTO_MIGRATE=0` 可以关掉启动时的自动迁移。

0.5.5 的变化（出口时区以 Cloudflare 看到的国家为准、关窗恢复 Windows 时区）见
[v0.5.5 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.5)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.6-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.6-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.6-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.6/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.6/excel-codex-bridge-0.5.6-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.6-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **The bridge's earlier conversations move by themselves**: conversations started through the
  bridge with 0.5.3 and earlier, or while Codex was not signed in, are filed under `excel-bridge`.
  With the bridge off, the desktop app could not open them: "Model provider `excel-bridge` not
  found". Now `excel-codex-desktop.cmd` and `excel-codex` move them into the shared list when they
  start while Codex is fully quit; no command to run. So **start the bridge first, then the desktop
  app**; while Codex runs nothing is moved, and the window says so.
- **`threads migrate` in 0.5.4 and 0.5.5 did not finish the job**: it changed Codex's conversation
  index only, but Codex rebuilds the index from the provider on the first line of each conversation
  file, so the conversation went back under `excel-bridge` and still failed with the bridge off.
  That line changes too now, and only its provider field: the rest of the file and its modification
  time stay as they are. Conversations moved before are finished, and renaming or archiving a moved
  conversation no longer moves it back.
- `excel-codex threads migrate` and `undo` do nothing while Codex runs, since Codex would put the
  change back. `EXCEL_BRIDGE_AUTO_MIGRATE=0` turns off the move at start.

For 0.5.5's changes (the exit timezone checked against the country Cloudflare sees, the Windows
timezone put back on close), see the
[v0.5.5 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.5); for what
0.5.4 added (conversations shared with the official sign-in, choosing the image model, the exit
timezone), see the [v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.6-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.6-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.6-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.6/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.6/excel-codex-bridge-0.5.6-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.6-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
