中转站名下的对话能并到 openai · A relay's OpenAI conversations can move under openai

> **非官方项目**，与 OpenAI、Microsoft 无关联。使用加载项后端可能违反 OpenAI 服务条款，风险自负。
> **Unofficial.** Not affiliated with OpenAI or Microsoft. Using the add-in's backend may violate OpenAI's terms; use at your own risk.

## 变化

- **中转站名下的对话能并到 `openai` 了**：报“Model provider `OpenAI` not found”的，是经中转站自己的
  provider 建的对话。有的中转站生成的 Codex 配置（例如 SUB2API 的“使用密钥 → Codex”）把 provider 叫作
  `OpenAI`，和 Codex 自带的 `openai` 不是同一个（名字区分大小写）；`config.toml` 里没有这段时 Codex 就打不开
  这些对话。现在先完全退出 Codex，再运行 `excel-codex threads migrate --from OpenAI`，就把它们并到
  `openai` 名下，走官方账号、开着桥接都能接着聊；改法和迁移桥接自己的对话一样（先备份索引、只原地改
  provider 和模型名），`excel-codex threads undo` 可以放回。`excel-codex threads` 末尾会列出其他 provider
  名下各有几个对话。
- **中转站建议配成 Codex 自带的 provider**：删掉 `model_provider = "OpenAI"` 和 `[model_providers.OpenAI]`，
  改成 `openai_base_url = "https://你的中转站/v1"`，再用中转站的 key 登录（`codex login --with-api-key`），
  以后中转站、官方、桥接的对话都在同一个列表里。`openai_base_url` 指向中转站时不要用 ChatGPT 账号登录，
  否则 ChatGPT 的登录凭据会发给中转站。详见 README 的“中转站名下的对话”。

0.5.9 的变化（普通版到 450k 才压缩、迁移过的对话关掉桥接后能接着聊）见
[v0.5.9 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9)；0.5.8 的变化（被限流时最多等 5 分钟）见
[v0.5.8 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8)；0.5.7 的变化（限流等待、
旧的桥接对话启动时自动并进共享列表）见
[v0.5.7 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7)；0.5.4 的新功能
（和官方链路会话互通、指定生图模型、出口时区）见
[v0.5.4 发布说明](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4)。

## 下载

- **Windows**：`excel-codex-bridge-0.5.10-windows-x64.zip`。解压后双击 `excel-codex.exe` 打开 Codex CLI，
  双击 `excel-codex-desktop.cmd` 给桌面版用。
- **macOS（Apple 芯片）**：`excel-codex-bridge-0.5.10-macos-arm64.tar.gz`
- **macOS（Intel）**：`excel-codex-bridge-0.5.10-macos-x64.tar.gz`
- **Linux / WSL 或从源码运行**：下载 Source code，使用 `excel-codex.sh`（需要 Python 3.10+）。
- **Linux / VPS 的 SUB2API 部署**：下载本版本 Source code，按 [部署文档](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.10/docs/sub2api.md) 构建 `packaging/sub2api/compose.yaml`。

macOS 推荐在终端用 `curl` 下载，这样不会被"无法验证开发者"拦下（Intel 芯片把 `arm64` 换成 `x64`）：

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.10/excel-codex-bridge-0.5.10-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.10-macos-arm64/excel-codex status
```

用浏览器下载的，解压后先运行一次 `xattr -dr com.apple.quarantine <解压出的目录>`。

## 注意

- 安装包附带中英文 SUB2API 部署文档。
- **macOS 支持仍是实验性的**：从真实 Mac 版 Excel 读取登录态还没实机验证过，欢迎反馈。
- 程序没有代码签名（Windows 和 macOS 都没有）。可以用同目录的 `.sha256` 文件核对下载是否完整。

交流 QQ 群：966195257

---

## Changes

- **A relay's conversations can move under `openai`**: "Model provider `OpenAI` not found" comes from
  conversations started through a relay's own provider. Some relays hand out a Codex config
  (SUB2API does, for an API key) whose provider is named `OpenAI`, which is not Codex's own `openai`
  (names are case-sensitive); once `config.toml` no longer has it, Codex cannot open those
  conversations. Quit Codex fully and run `excel-codex threads migrate --from OpenAI`: they move under
  `openai` and carry on through the official sign-in, or through the bridge while it is on. They are
  changed the way the bridge's own conversations are (the index copied first, only the provider and
  model names changed in place), and `excel-codex threads undo` puts them back. `excel-codex threads`
  now ends with how many conversations other providers have.
- **Set a relay up as Codex's own provider**: remove `model_provider = "OpenAI"` and the
  `[model_providers.OpenAI]` table, add `openai_base_url = "https://your-relay/v1"`, and sign in with
  the relay's key (`codex login --with-api-key`); the relay's, the official and the bridge's
  conversations then share one list. Do not pair a relay's `openai_base_url` with a ChatGPT sign-in:
  the ChatGPT sign-in would go to the relay. See "A relay's conversations" in the README.

For 0.5.9's changes (standard models compacting at 450k, moved conversations carrying on with the
bridge off), see the [v0.5.9 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.9); for
0.5.8's (up to 5 minutes' wait under the rate limit), the
[v0.5.8 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.8); for 0.5.7's
(the rate-limit wait, the bridge's earlier conversations moved into the shared list at start), the
[v0.5.7 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.7); for what 0.5.4
added (conversations shared with the official sign-in, choosing the image model, the exit timezone),
the [v0.5.4 release notes](https://github.com/Kaixxrua/excel-codex-bridge/releases/tag/v0.5.4).

## Download

- **Windows**: `excel-codex-bridge-0.5.10-windows-x64.zip`. Double-click `excel-codex.exe` for the
  Codex CLI, or `excel-codex-desktop.cmd` for the desktop app.
- **macOS (Apple silicon)**: `excel-codex-bridge-0.5.10-macos-arm64.tar.gz`
- **macOS (Intel)**: `excel-codex-bridge-0.5.10-macos-x64.tar.gz`
- **Linux / WSL, or from source**: download the source code and use `excel-codex.sh` (Python 3.10+).
- **Linux / VPS with SUB2API**: download this release's source and follow the [deployment guide](https://github.com/Kaixxrua/excel-codex-bridge/blob/v0.5.10/docs/sub2api.en.md).

On a Mac, downloading with `curl` avoids the "developer cannot be verified" block (Intel: replace
`arm64` with `x64`):

```
curl -fL https://github.com/Kaixxrua/excel-codex-bridge/releases/download/v0.5.10/excel-codex-bridge-0.5.10-macos-arm64.tar.gz | tar xz
./excel-codex-bridge-0.5.10-macos-arm64/excel-codex status
```

If you downloaded with a browser, run `xattr -dr com.apple.quarantine <extracted folder>` once.

## Notes

- Packages include bilingual SUB2API deployment guides.
- **macOS support is still experimental**: reading the sign-in from a real Mac Excel has not been
  verified on hardware yet; feedback welcome.
- The programs are not code-signed (neither Windows nor macOS). Check downloads against the
  `.sha256` files next to them.
