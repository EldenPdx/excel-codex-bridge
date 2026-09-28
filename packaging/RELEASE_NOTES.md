每个模型新增 1M 长上下文版 · Every model gets a 1M long-context version

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **每个模型都有两个版本**：原来的名字是 272k 版，另加一个 `-1m` 的 1M 版，例如 `gpt-6-sol-excel` 和
  `gpt-6-sol-1m-excel`，两者用的是同一个上游模型。Codex 模型列表、桌面版、`--model` 和 SUB2API 的模型列表里都能选，
  桌面版里显示为「6-Sol Excel 1M」这样的名字。
- **1M 版的上下文是 918k**：这是真实后端的实测上限，一次最多接受约 918k 输入 token，再多就返回
  `context_length_exceeded`。gpt-5.6-sol、gpt-6-sol、gpt-6-luna、gpt-6-astra 实测结果相同，gpt-5.6-terra 和
  gpt-5.6-luna 按同一上限设置。Codex 在约 826k 时自动压缩，后端兜底压缩阈值设在约 872k。
- **272k 版**：所有模型统一按 272k，Codex 仍在 180k 时自动压缩。gpt-5.6-luna 和 gpt-6-luna 从原来的 200k 改为 272k。
- 长对话每轮发出去的上下文更多，额度消耗也相应更多，用不到这么长时选 272k 版即可。
- 默认模型不变，仍是 `gpt-5.6-sol-excel`；已有配置里的模型名照常可用。

## 下载

- **Windows**：`excel-codex-bridge-0.5.3-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.3-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.3-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.3/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.3/excel-codex-bridge-0.5.3-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.3-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **Every model now comes in two versions**: the existing name is the 272k version, plus a `-1m`
  1M version, e.g. `gpt-6-sol-excel` and `gpt-6-sol-1m-excel`, both using the same upstream model.
  Both are selectable in Codex's model list, the desktop app, `--model` and the SUB2API model list;
  the desktop app shows names like "6-Sol Excel 1M".
- **The 1M versions have a 918k context**: the limit measured on the real backend, which accepts
  up to about 918k input tokens per request and answers `context_length_exceeded` beyond that.
  gpt-5.6-sol, gpt-6-sol, gpt-6-luna and gpt-6-astra measured the same; gpt-5.6-terra and gpt-5.6-luna
  use the same limit. Codex compacts automatically at about 826k, and the backend's fallback
  compaction threshold is about 872k.
- **272k versions**: every model is now 272k, and Codex still compacts at 180k. gpt-5.6-luna and
  gpt-6-luna move from 200k to 272k.
- Each turn of a long conversation sends more context and uses more of your plan, so pick the 272k
  version when you don't need the length.
- The default model is still `gpt-5.6-sol-excel`; model names in existing configs keep working.

## Download

- **Windows**: `excel-codex-bridge-0.5.3-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.3-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.3-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.3/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.3/excel-codex-bridge-0.5.3-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.3-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
