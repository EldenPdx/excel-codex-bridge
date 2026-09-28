出口时区以 Cloudflare 的国家为准，关窗恢复 Windows 时区 · Exit timezone checked against Cloudflare's country, Windows timezone put back on close

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **出口时区不再被查错的国家带偏**：0.5.4 只信一个 IP 定位服务，它把美国出口定位到台湾时，Windows
  就被改成台北时间。现在以 Cloudflare（OpenAI 前面那一层）看到的出口国家为准：定位服务给出的国家
  和它不一致就不采用，依次换 ipwho.is、ipapi.co、get.geojs.io、api.ip.sb；都不一致时不改时区，
  并在窗口里写明各家说的是哪里。窗口里显示为 `exit … (Cloudflare: US) is in America/Los_Angeles`。
  0.5.4 缓存的查询结果不再沿用，会重新查一次。
- **关掉窗口就恢复 Windows 时区**：`excel-codex-desktop.cmd` 的窗口不论是点右上角关闭还是按 Ctrl+C，
  都会把时区恢复成第一次修改前的，窗口里会显示 `Windows timezone: put back …`。窗口被强制结束
  （例如任务管理器）时恢复不了，需要手动运行 `excel-codex timezone restore`。
- 已经被 0.5.4 改成台北时间的：运行一次 `excel-codex timezone restore`，或者打开 0.5.5 的
  `excel-codex-desktop.cmd` 再关掉，都会回到原来的时区。

0.5.4 的新功能（和官方链路会话互通、旧对话迁移、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.5-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.5-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.5-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.5/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.5/excel-codex-bridge-0.5.5-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.5-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **The exit timezone no longer follows a wrong country**: 0.5.4 believed a single IP lookup
  service, so when it placed a US exit in Taiwan, Windows was set to Taipei time. Now the country
  Cloudflare (in front of OpenAI) sees the exit in decides: an answer in another country is not
  used, and ipwho.is, ipapi.co, get.geojs.io and api.ip.sb are tried in turn; when none agrees the
  timezone is left alone and the window says what each one answered. The window shows
  `exit … (Cloudflare: US) is in America/Los_Angeles`. A lookup cached by 0.5.4 is not reused.
- **Closing the window puts the Windows timezone back**: whether the `excel-codex-desktop.cmd`
  window is closed with its close button or Ctrl+C, the timezone from before the first change is
  put back and the window says `Windows timezone: put back …`. A window that is killed (for example
  from Task Manager) cannot do that; run `excel-codex timezone restore` then.
- If 0.5.4 already set Taipei time: run `excel-codex timezone restore` once, or open 0.5.5's
  `excel-codex-desktop.cmd` and close it; either puts back the earlier timezone.

For what 0.5.4 added (conversations shared with the official sign-in, moving earlier
conversations, choosing the image model, the exit timezone), see the
[v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.5-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.5-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.5-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.5/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.5/excel-codex-bridge-0.5.5-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.5-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
