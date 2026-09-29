双击 excel-codex-desktop.cmd 会先自动更新再打开 · excel-codex-desktop.cmd updates itself before it opens

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **Windows 免安装版会自己更新了**：双击 `excel-codex-desktop.cmd` 先查一次新版本，有就下载、按 GitHub API
  列出的 SHA-256 校验、解压到安装目录下的 `.update`、先试着运行一次新版，确认没问题后等当前程序退出再换上
  新文件，然后用新版打开。窗口里显示下载进度，按 Esc 这次先跳过。下载失败、校验不对、新版起不来、同一目录
  还有别的 excel-codex 在运行时，都照常打开当前版本，不会动原来的文件。详见 README 的
  [自动更新](https://github.com/Kaixxrua/excel-codex-bridge#自动更新windows-免安装版)。
- 新增 `excel-codex update`：免安装版里提前下载好，下次双击 `excel-codex-desktop.cmd` 时装上；其他情况告诉你
  怎么更新。
- 新环境变量：`EXCEL_BRIDGE_AUTO_UPDATE=0` 只提示不自动更新；`EXCEL_BRIDGE_DOWNLOAD_MIRROR` 给 GitHub 下载链接
  加镜像前缀（仍按 GitHub 列出的 SHA-256 校验）。
- 新增自动更新的 Windows 端到端测试：安装路径带空格、括号和中文，覆盖正常更新、另一个窗口占用、文件被锁时
  回滚、校验不符四种情况。

**0.5.12 及更早的版本还没有自动更新，这次需要手动下载替换一次**，之后就不用了。

0.5.12 的变化（子代理不再报加密内容无法解码、老会话的工具调用不再越错越多）见
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

- **Windows**：`excel-codex-bridge-0.5.13-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.13-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.13-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.13/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.13/excel-codex-bridge-0.5.13-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.13-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **The Windows release zip now updates itself**: double-clicking `excel-codex-desktop.cmd` first
  checks for a newer release; if there is one, it downloads it, checks it against the SHA-256 GitHub's
  API lists, unpacks it into `.update` inside the install folder and runs the new version once to make
  sure it starts, then swaps the files in once the current program has exited and opens the new
  version. The window shows the download's progress; Esc skips it this time. When the download fails,
  the checksum does not match, the new version does not start, or excel-codex from the same folder is
  still running, the current version opens as usual and its files are left alone. See
  [Automatic update](https://github.com/Kaixxrua/excel-codex-bridge/blob/main/README.en.md#automatic-update-windows-release-zip)
  in the README.
- New `excel-codex update`: in the release zip it downloads the update ahead of time, to be installed
  the next time `excel-codex-desktop.cmd` starts; elsewhere it says how to update.
- New environment variables: `EXCEL_BRIDGE_AUTO_UPDATE=0` keeps the notice without installing;
  `EXCEL_BRIDGE_DOWNLOAD_MIRROR` puts a mirror's prefix in front of the GitHub download link (still
  checked against GitHub's SHA-256).
- A new Windows end-to-end check of the update, from a path with spaces, brackets and Chinese letters:
  a normal update, another window in the way, a locked file rolled back, and a checksum mismatch.

**0.5.12 and earlier cannot update themselves yet: download and replace this one by hand once**, and
not again after that.

For 0.5.12's changes (subagents getting their task as text, older conversations no longer failing tool
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

- **Windows**: `excel-codex-bridge-0.5.13-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.13-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.13-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.13/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.13/excel-codex-bridge-0.5.13-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.13-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
