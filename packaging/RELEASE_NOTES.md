新增 GPT-6 Sol / GPT-6 Luna 两个模型 · Adds the GPT-6 Sol and GPT-6 Luna models

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **新增两个模型**：`gpt-6-sol-excel`（上游 `gpt-6-sol`）和 `gpt-6-luna-excel`（上游 `gpt-6-luna`）。
  Codex 模型列表、桌面版、`--model` 和 SUB2API 的模型列表里都能直接选。
- 上下文窗口按同名前代处理：GPT-6 Sol 按 272k、GPT-6 Luna 按 200k，自动压缩阈值随之设定。
- 默认模型不变，仍是 `gpt-5.6-sol-excel`；已有模型和设置不受影响。
- 顺带确认 Codex 的上下文压缩经本桥工作正常：命令行/桌面版用的本地压缩、以 OpenAI 方式接入（如经
  SUB2API）时用的远端压缩，压缩请求都已在真实后端验证过，压缩后仍记得之前的内容；自动触发时机用模拟后端验证。

## 下载

- **Windows**：`excel-codex-bridge-0.5.2-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.2-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.2-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.2/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.2/excel-codex-bridge-0.5.2-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.2-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Two new models**: `gpt-6-sol-excel` (upstream `gpt-6-sol`) and `gpt-6-luna-excel` (upstream
  `gpt-6-luna`), selectable in Codex's model list, the desktop app, `--model` and the SUB2API model list.
- Context windows follow their namesakes: GPT-6 Sol is treated as 272k and GPT-6 Luna as 200k, with the
  auto-compaction threshold set to match.
- The default model is still `gpt-5.6-sol-excel`; existing models and settings are unchanged.
- Also confirmed: Codex's context compaction works through the bridge. The local compaction used by the
  CLI and desktop app, and the remote compaction used when connecting the OpenAI way (e.g. via SUB2API),
  were both verified against the real backend, with earlier context kept; the auto-trigger timing was
  checked against a simulated backend.

## Download

- **Windows**: `excel-codex-bridge-0.5.2-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.2-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.2-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.2/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.2/excel-codex-bridge-0.5.2-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.2-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
