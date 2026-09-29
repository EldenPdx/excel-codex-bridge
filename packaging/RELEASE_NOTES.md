子代理不再报加密内容无法解码、老会话的工具调用不再越错越多 · Subagents get their task as text, older conversations stop failing tool calls

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **子代理不再报“加密内容无法解码”**：桥接转交的调用没有注明参数是明文，Codex（多代理 v2）就把发给子代理的
  任务标成“加密内容”，Excel 后端解不开。现在每个调用都注明是明文；老会话里已经被误标的消息，发给后端前还原成
  文字，不用新开会话。
- **老会话的工具调用不再越错越多**：桥接记不起原始调用时（重启后，SUB2API 多人共用时更常见）重建的调用丢了
  `collaboration.` 前缀；模型没转换成功的 `run_officejs` 调用回放时又被包一层。模型照着历史学，就越来越常出错。
  现在重建时保留完整工具名，没转换成功的调用原样回放，并告诉模型具体错在哪里，不再只是一句“格式不对”。
- 真正由别的后端加密的内容，后端报错时桥接换成一句说明再发一次（以前只处理推理内容）。
- 新增子代理的端到端测试（Windows、macOS、Linux，含桌面版）。

0.5.11 的变化（桌面版不再卡加载、网络闪断自动重连、长对话不再报请求过大、系统时区不再来回跳）见
[v0.5.11 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.11)；0.5.10 的变化（中转站名下的对话并到 `openai`）见
[v0.5.10 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.10)；0.5.9 的变化（普通版到 450k 才压缩、迁移过的对话关掉桥接后能接着聊）见
[v0.5.9 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9)；0.5.8 的变化（被限流时最多等 5 分钟）见
[v0.5.8 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8)；0.5.7 的变化（限流等待、
旧的桥接对话启动时自动并进共享列表）见
[v0.5.7 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.12-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.12-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.12-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.12/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.12/excel-codex-bridge-0.5.12-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.12-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Subagents no longer fail with "encrypted content could not be decoded"**: the calls the bridge
  passed on did not say their arguments were plain text, so Codex (multi-agent v2) labelled the task
  it gave a subagent as encrypted content, which the Excel backend cannot decrypt. Every call now says
  so; messages an older conversation has already labelled encrypted go to the backend as text again,
  no new conversation needed.
- **Tool calls in older conversations no longer fail more and more**: a call the bridge had to rebuild
  because it could not recall the original (after a restart, and more often under SUB2API with many
  users) lost its `collaboration.` prefix, and a `run_officejs` call the model got wrong was wrapped in
  another one when replayed. The model copies its history, so it went wrong more and more often. A
  rebuilt call now keeps the tool's full name, and a call that could not be converted is replayed as it
  was, with the model told what exactly was wrong rather than just that the format was.
- What another backend really encrypted is replaced with a note when the backend fails on it, and the
  request sent again (reasoning only, before).
- A new end-to-end check with a subagent (Windows, macOS and Linux, the desktop app too).

For 0.5.11's changes (no more stuck loading, reconnects after network drops, long conversations
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

- **Windows**: `excel-codex-bridge-0.5.12-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.12-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.12-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.12/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.12/excel-codex-bridge-0.5.12-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.12-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
