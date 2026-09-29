桌面版不再卡加载、网络闪断自动重连、长对话不再报请求过大 · No more stuck loading, reconnects after network drops, long conversations fit

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **长对话不再报 `Invalid request body: request body is too large`**：请求解压后的上限从 64 MiB 提到
  1 GiB。长对话（尤其带图片）在默认 450k 自动压缩前就会超过 64 MiB，大任务跑到一半就报这个错。
  截断的压缩请求现在报 400，不会被当成空请求。SUB2API 部署的上限提到 256 MiB。
- **网络闪断不再直接 502**：代理节点掉线时，Codex 几秒内重试 5 次就报
  `502 Bad Gateway: Could not connect to bps.openai.com (ConnectError)`。现在后端还没回应的请求由桥接
  接着重试，最多 2 分钟：前 5 秒 Codex 看不到，之后显示在工作；连上了照常回答，2 分钟还连不上就结束这一轮，
  Codex 不再自己重试。`EXCEL_BRIDGE_CONNECT_WAIT` 可改时长（`0` 关掉）。
- **桌面版不再一直“加载中”**：Codex 用 ChatGPT 登录时，打开对话要等 chatgpt.com 回应 Apps（最多 30 秒）
  和插件推荐（每一轮 5 秒）；代理不通时对话一直加载、新任务一直“启动中”。`excel-codex desktop` 开着期间
  关掉这两项（关窗口时随配置恢复），实测一轮从 45 秒降到约 5 秒；`--keep-apps` 可保留。
- **系统时区不再来回跳**：代理在台湾、日本等节点间轮换时，新的出口时区要连续 3 次检查一致才采用；
  桥接记住查过的出口 IP 的时区。Windows“自动设置时区”把时区改回本地时会再改回去，并提示去哪里关掉。
  偶尔一次查询失败不再刷屏；代理节点连不上时说明是哪个代理（不显示密码），并提示 Codex 自己也要连
  chatgpt.com。

0.5.10 的变化（中转站名下的对话并到 `openai`）见
[v0.5.10 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.10)；0.5.9 的变化（普通版到 450k 才压缩、迁移过的对话关掉桥接后能接着聊）见
[v0.5.9 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9)；0.5.8 的变化（被限流时最多等 5 分钟）见
[v0.5.8 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8)；0.5.7 的变化（限流等待、
旧的桥接对话启动时自动并进共享列表）见
[v0.5.7 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.11-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.11-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.11-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.11/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.11/excel-codex-bridge-0.5.11-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.11-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Long conversations no longer fail with `Invalid request body: request body is too large`**: a
  request can now be up to 1 GiB once decompressed, up from 64 MiB. Long conversations (with pictures
  above all) passed 64 MiB before the default 450k auto-compaction, so big tasks failed halfway. A
  compressed request that was cut off is now a 400 rather than read as an empty one. SUB2API
  deployments take up to 256 MiB.
- **A network drop is no longer a 502 straight away**: when a proxy node went down, Codex retried five
  times within seconds and showed `502 Bad Gateway: Could not connect to bps.openai.com
  (ConnectError)`. Now the bridge keeps sending a request the backend has not answered yet, for up to
  2 minutes: Codex sees nothing for the first 5 seconds and then shows it is working; once connected
  it answers as usual, and after 2 minutes without a connection the turn ends and Codex does not
  retry. `EXCEL_BRIDGE_CONNECT_WAIT` sets how long (`0` turns it off).
- **The desktop app no longer sits on "loading"**: signed in with ChatGPT, Codex waits on chatgpt.com
  for apps (up to 30 seconds) and plugin suggestions (5 seconds a turn) when a conversation opens, so
  through a proxy that is down conversations kept loading and new tasks stayed on "starting".
  `excel-codex desktop` turns both off while it runs (they come back with the config); a turn went
  from 45 seconds to about 5 in testing. `--keep-apps` leaves them on.
- **The system timezone no longer flips back and forth**: with a proxy taking turns between nodes
  (Taiwan and Japan, say), another exit's timezone is taken once it holds for 3 checks in a row, and
  the bridge remembers each exit IP's timezone. When Windows "Set time zone automatically" changes it
  back, the window sets it again and says where to turn that off. A single failed check no longer
  fills the window; a proxy node that cannot be reached is named (without its password), with a note
  that Codex itself needs chatgpt.com too.

For 0.5.10's changes (a relay's conversations moving under `openai`), see the
[v0.5.10 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.10); for 0.5.9's changes (standard models compacting at 450k, moved conversations carrying on with the
bridge off), see the [v0.5.9 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9); for
0.5.8's (up to 5 minutes' wait under the rate limit), the
[v0.5.8 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8); for 0.5.7's
(the rate-limit wait, the bridge's earlier conversations moved into the shared list at start), the
[v0.5.7 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7); for what 0.5.4
added (conversations shared with the official sign-in, choosing the image model, the exit timezone),
the [v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.11-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.11-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.11-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.11/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.11/excel-codex-bridge-0.5.11-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.11-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
