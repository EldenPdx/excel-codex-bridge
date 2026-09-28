被限流时最多等 5 分钟，等满还不行就结束这一轮 · Up to 5 minutes' wait under the rate limit, then the turn ends cleanly

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **被限流时最多等 5 分钟**：0.5.7 起，回答还没开始就被加载项的共用额度（TPM）限流时，桥接自己隔
  1、2、4、8、15 秒（之后每次 15 秒）重发。等待上限从每个请求 60 秒改为 **5 分钟**。
- **等待期间不会断开**：桥接每 10 秒告诉 Codex 这次回答还在进行，Codex 5 分钟没有动静就断开的规则不会
  被触发。
- **等满还被限流就结束这一轮**：Codex 显示 `The Excel backend is still rate limited after 5 minutes …`，
  稍后再发一次消息即可。以前 Codex 拿到限流报错会自己再重试 5 次，每次桥接又要重新等，最坏会拖很久；
  现在这一轮最多 5 分钟。中途随时可以在 Codex 里中断。
- `EXCEL_BRIDGE_RATE_LIMIT_WAIT=<秒>` 改等待上限：默认 `300`，最多 `1800`；`0` 不等，报错照原样交给 Codex。

0.5.7 的变化（限流等待、旧的桥接对话启动时自动并进共享列表）见
[v0.5.7 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7)；0.5.5 的变化见
[v0.5.5 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.5)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.8-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.8-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.8-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.8/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.8/excel-codex-bridge-0.5.8-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.8-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Up to 5 minutes' wait under the rate limit**: since 0.5.7, when the add-in's shared
  tokens-per-minute budget turns a request away before any of the answer has come, the bridge sends it
  again after 1, 2, 4, 8 and 15 seconds (then every 15). The wait now goes on for up to **5 minutes**
  instead of 60 seconds per request.
- **Codex stays connected meanwhile**: the bridge tells Codex every 10 seconds that the response is
  still going, so Codex's five-minute idle timeout does not fire.
- **Still limited after that, the turn ends**: Codex shows `The Excel backend is still rate limited
  after 5 minutes …`; send the message again later. Before, Codex retried a rate limit five more times
  and the bridge waited each one out afresh; now a turn takes 5 minutes at most. You can interrupt in
  Codex at any time.
- `EXCEL_BRIDGE_RATE_LIMIT_WAIT=<seconds>` sets the wait: `300` by default, at most `1800`; `0` for no
  wait, the error then reaching Codex as it came.

For 0.5.7's changes (the rate-limit wait, the bridge's earlier conversations moved into the shared list
at start), see the [v0.5.7 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7);
for 0.5.5's, the [v0.5.5 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.5);
for what 0.5.4 added (conversations shared with the official sign-in, choosing the image model, the exit
timezone), the [v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.8-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.8-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.8-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.8/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.8/excel-codex-bridge-0.5.8-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.8-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
