和官方链路会话互通，自动匹配出口时区 · Conversations shared with the official sign-in, exit timezone matched

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **和官方链路会话互通**：Codex 自己登录过（`codex login`，ChatGPT 账号或 API key）时，桌面版模式和
  `excel-codex` 改为把 Codex 自带的 `openai` provider 指到桥接（`openai_base_url`），不再用独立的
  `excel-bridge` provider。开着桥接时官方链路的旧对话都在列表里、接着聊走 Excel 链路；关掉桥接后，
  开着桥接时建的对话也照样在列表里、接着聊走官方链路。
- **272k 版改用官方模型名**：`gpt-6-sol` 这样的名字（列表里显示「6-Sol Excel」），默认 `gpt-5.6-sol`。
  以前的 `gpt-6-sol-excel` 这类名字照样能用，只是不再出现在模型列表里；1M 版官方没有，名字不变，
  关掉桥接后 1M 版的对话需要在模型菜单里换成官方模型。
- 两个后端加密的推理内容不一定互认：Excel 后端不认官方那份时，桥接去掉推理内容自动重发一次，
  对话文字不受影响。反过来还没有实测，关掉桥接后某条对话报错的话新建一条即可。
- Codex 自带的 provider 会先试 WebSocket，桥接回 426 让它马上改用 HTTP；`codex exec` 里那行
  `failed to connect to websocket: 426` 属于正常现象。
- Codex 没登录时行为和以前一样（独立的 `excel-bridge` provider）。
- **旧对话迁移**：0.5.3 及更早版本建的桥接对话记在 `excel-bridge` 名下、不在共享列表里。先退出 Codex
  桌面版，再运行 `excel-codex threads migrate` 就能并进共享列表（`excel-codex threads` 先看有哪些）。
  只改 Codex 的对话索引、不碰对话内容，改前自动备份，`excel-codex threads undo` 可撤销。
- **指定生图模型**：`--image-model <模型名>`（或 `EXCEL_BRIDGE_IMAGE_MODEL`）让 Codex 的生图工具改用指定模型，
  默认仍是 gpt-image-2。Codex 不会把用的模型告诉对话里的模型，桥接窗口会显示每次生图用的模型。
- **出口时区**：经过桥接的请求，桥接查出代理出口 IP 的时区，把 Codex 写进对话的时区和当天日期换成出口
  那边的。Windows 上 `excel-codex-desktop.cmd` 打开期间，还会查官方 ChatGPT 登录（`chatgpt.com`，API key
  则是 `api.openai.com`）的出口时区，每分钟用 `tzutil` 把系统时区对上，不需要另外运行别的程序。
  `excel-codex timezone` 查看状态，`excel-codex timezone restore` 恢复原时区，`--timezone off`
  （或 `EXCEL_BRIDGE_TIMEZONE=off`）关闭。只把出口 IP 发给 ipwho.is / ipapi.co 查时区。
- SUB2API 的模型列表不变，仍是 `*-excel` 名字。

## 下载

- **Windows**：`excel-codex-bridge-0.5.4-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.4-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.4-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.4/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.4/excel-codex-bridge-0.5.4-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.4-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Conversations shared with the official sign-in**: when Codex itself is signed in (`codex login`,
  with a ChatGPT account or an API key), desktop mode and `excel-codex` point Codex's own `openai`
  provider at the bridge (`openai_base_url`) instead of a separate `excel-bridge` provider. With the
  bridge on, the earlier conversations of the official sign-in are listed and carry on through
  Excel; with it off, the conversations started with the bridge on are still listed and carry on
  through the official sign-in.
- **The 272k versions use OpenAI's model names**: names like `gpt-6-sol` (shown as "6-Sol Excel"),
  default `gpt-5.6-sol`. The earlier names such as `gpt-6-sol-excel` still work but are no longer
  in the model list. OpenAI has no 1M versions, so their names stay; after the bridge is closed a
  1M conversation needs an OpenAI model picked in the model menu.
- The reasoning each backend encrypts may not be readable by the other: when the Excel backend
  cannot read the official one's, the bridge sends the conversation again once without it; the
  conversation's text is unaffected. The other direction is not tested yet; if a conversation
  fails after the bridge is closed, start a new one.
- Codex's own provider tries a WebSocket first and the bridge answers 426 so that it switches to
  HTTP right away; the `failed to connect to websocket: 426` line in `codex exec` is expected.
- With Codex not signed in nothing changes (a separate `excel-bridge` provider).
- **Moving earlier conversations**: bridge conversations started with 0.5.3 or earlier are filed
  under `excel-bridge` and are not in the shared list. Quit the Codex desktop app, then run
  `excel-codex threads migrate` to move them in (`excel-codex threads` shows which). Only Codex's
  conversation index changes, never the conversations; it is copied first, and
  `excel-codex threads undo` undoes it.
- **Choosing the image model**: `--image-model <name>` (or `EXCEL_BRIDGE_IMAGE_MODEL`) makes Codex's
  image tool draw with that model; the default is still gpt-image-2. Codex does not tell the model in
  the conversation which model drew; the bridge window shows it for each picture.
- **Exit timezone**: for requests through the bridge, the bridge looks up the proxy exit IP's
  timezone and puts it, with today's date there, in place of the timezone and date Codex writes
  into the conversation. On Windows, while `excel-codex-desktop.cmd` is open, it also looks up the
  exit timezone of the official ChatGPT sign-in (`chatgpt.com`, or `api.openai.com` with an API
  key) and sets the system timezone to it with `tzutil` every minute; nothing else has to run.
  `excel-codex timezone` shows the state, `excel-codex timezone restore` restores the earlier
  timezone, and `--timezone off` (or `EXCEL_BRIDGE_TIMEZONE=off`) turns it off. Only the exit IP is
  sent to ipwho.is / ipapi.co.
- The SUB2API model list is unchanged and keeps the `*-excel` names.

## Download

- **Windows**: `excel-codex-bridge-0.5.4-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.4-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.4-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.4/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.4/excel-codex-bridge-0.5.4-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.4-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
