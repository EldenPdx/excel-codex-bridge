# excel-codex-bridge

[简体中文](README.md) | **English**

> **Unofficial.** Not affiliated with, endorsed by, or supported by OpenAI or Microsoft.
> Read [Risks and disclaimer](#risks-and-disclaimer) before using it.

> **Optional SUB2API plugin:** run Excel as a private sidecar upstream without modifying
> SUB2API. See [Docker deployment and SSH session sync](docs/sub2api.en.md).
> This explicitly opted-in mode transfers your session to your trusted server.
> The local-only / OpenAI-only guarantees below describe the default local mode, not this remote mode.

Run the [Codex CLI](https://github.com/openai/codex) and the Codex desktop app on a ChatGPT
sign-in already on *your own* computer. One command (or a double-click) starts a local bridge and
Codex together; quitting stops the bridge. By default it uses Codex's own sign-in
(`codex login`), so Excel need not be installed; if the backend refuses it, it falls back to the
session the **ChatGPT add-in for Excel** caches, opening Excel's ChatGPT pane for you when a
sign-in is needed. See [Sign-in](#sign-in). Nothing listens beyond loopback, and the session
stays in memory and only goes to OpenAI.

## How it works

```
Codex CLI ──(Responses API, 127.0.0.1)──▶ excel-codex-bridge ──(HTTPS)──▶ bps.openai.com
                                                 ▲                      (ChatGPT for Excel backend)
              reads a ChatGPT sign-in already here (Codex's own, or the add-in's), read-only, never persisted
```

- **No login of its own.** No OAuth, no passwords, and it never refreshes or writes a token. It
  only reads a ChatGPT sign-in already on this machine — Codex's own first (`codex login`, kept in
  `~/.codex/auth.json`), then the session the Excel add-in caches in its WebView2 storage
  (`bps_auth_tokens`). With the add-in's, you sign in inside its ChatGPT pane (which the tool
  [opens for you](#automatic-sign-in-windows) when needed). Which one it takes, and how it falls
  back, is under [Sign-in](#sign-in).
- **The token stays in memory.** It is never written to disk or uploaded, and is only sent to
  `bps.openai.com`. The log holds request lines and error types, never prompts or tokens.
- **Local only.** Loopback addresses only. Requests from non-loopback peers, with a non-loopback
  `Host` (DNS-rebinding guard), or with any `Origin` header (browsers) are refused. `serve`
  refuses to bind `0.0.0.0`.
- **Tool calls.** The Excel backend rejects client-defined tools, so the bridge describes
  Codex's tools (shell, apply_patch, …) in the prompt; the model calls them through the
  backend's native `run_officejs`, and the bridge turns those back into Codex tool calls.
  Calls that do not depend on each other (reading several files, independent commands) come in
  one go and Codex runs them at the same time; file edits still happen one after another.
- **Several sessions at once.** Codex windows, desktop conversations and subagents can share one
  bridge and work at the same time without queuing.
- **Pictures.** Pictures go to OpenAI only, like the rest of the request. Where the backend will
  not take them inline, the bridge uploads them to OpenAI the way the add-in's Upload file button
  does; see [Pictures](#pictures).
- **Image generation.** Codex's own image tool is answered by the endpoint the Excel add-in draws
  pictures with (gpt-image-2), on the same session; see [Image generation](#image-generation).
- **Your Codex config is left alone.** The CLI launcher passes the provider and model catalog as
  `-c` overrides, so `~/.codex/config.toml` is untouched. Only [desktop mode](#codex-desktop-app--ide-extension)
  edits it, restores it exactly when its window closes, and keeps a backup.

## Requirements

- Codex CLI: `npm install -g @openai/codex`.
- One ChatGPT sign-in already on this machine, either (see [Sign-in](#sign-in)):
  - **Codex's own sign-in** (the default, no Excel needed): run `codex login` once with ChatGPT,
    on any OS.
  - **The Excel add-in's session** (the fallback): Windows 10/11 with **Microsoft 365 desktop
    Excel** and the **ChatGPT** add-in (publisher OpenAI); on a Mac see
    [macOS](#macos--wsl-experimental). No need to sign in first — the tool opens Excel and walks
    you through it when needed. If the add-in is not installed, Excel usually offers to trust and
    install it; otherwise add it from Home → Add-ins. Your ChatGPT plan must include the add-in.
- Python 3.10+ only when running from source ([python.org](https://www.python.org/downloads/);
  tick *Add python.exe to PATH*); the no-install build does not need it.
- Network access to `bps.openai.com` (see [Proxy](#proxy)).

## Sign-in

The bridge needs a ChatGPT sign-in already on this machine. Two are possible, chosen in order by
default:

1. **Codex's own sign-in** (`codex login`, kept in `$CODEX_HOME/auth.json`, default
   `~/.codex/auth.json`). With this one Excel need not be installed, and it works on any OS.
2. **The session the Excel add-in caches** (the fallback). Used when Codex is not signed in with
   ChatGPT, its sign-in has expired, or the backend refuses it; a sign-in then
   [opens Excel's pane](#automatic-sign-in-windows).

Pick one with `--login` or the `EXCEL_BRIDGE_LOGIN` environment variable:

| Value | Meaning |
| --- | --- |
| `auto` (default) | Try Codex's sign-in, then the Excel add-in's |
| `codex` | Only Codex's sign-in (Excel is never touched) |
| `excel` | Only the Excel add-in's session (the original behaviour) |

- **When it falls back** (in `auto` only): Codex is not signed in / uses an API key / its sign-in
  has expired or expires within 5 minutes / the backend answers 401 or 403. After a 401/403 it
  retries once on the Excel session and then stays on Excel until `auth.json` changes (e.g. you run
  `codex login` again), when it tries Codex once more.
- `EXCEL_BRIDGE_CODEX_AUTH` points at a different `auth.json` (otherwise `$CODEX_HOME`, then
  `~/.codex`).
- When a sign-in is expired or unusable, `excel-codex status` says which one is in use, when it
  expires, and how to renew it (`codex login` for Codex's; `excel-codex login` for Excel's).
- The bridge never refreshes or writes a token; when Codex's expires, run `codex login` yourself
  (in `auto` it falls back to Excel meanwhile).
- [SUB2API remote sync](docs/sub2api.en.md) can now send Codex's sign-in too: `push-session --login`
  (default `auto`: Codex's, then the Excel add-in's), so a machine without Excel — a server — can push
  as well. Note this sends the sign-in you choose to the server you name — a credential export that
  happens only when you run the sync command, and only to a machine you trust.

> **Verified** (2026-09-25): tested with Codex CLI 0.156.1 — after a fresh `codex login` the bps
> backend returns 200, so Codex's own sign-in works directly, with no Excel installed. Should a
> future backend change refuse it, `auto` still falls back to the Excel session.

This idea comes from [MIKUbiu/bps-local](https://github.com/MIKUbiu/bps-local); this project is a
separate implementation of it, see [Credits and license](#credits-and-license).

## Quick start (Windows)

**Option 1: no-install build (recommended)**

1. Download `excel-codex-bridge-<version>-windows-x64.zip` from
   [Releases](https://github.com/Kaixxrua/excel-codex-bridge/releases/latest) and unzip it anywhere,
   e.g. `D:\tools\excel-codex-bridge`.
2. Double-click `excel-codex.exe` to open Codex in your user folder. To work on a project, run
   `D:\tools\excel-codex-bridge\excel-codex.exe` from a terminal in that folder, or add the folder
   to `PATH` and type `excel-codex`.

The exe is not code-signed; if SmartScreen stops the first run, choose "More info → Run anyway".

On the first run there is no session yet, so Excel opens with the ChatGPT pane: sign in there,
Excel closes by itself and Codex starts. See [Automatic sign-in](#automatic-sign-in-windows).

**Option 2: from source**

Clone this repository anywhere and run its `excel-codex.cmd` from your project folder (or
double-click it). The first run creates `.venv` next to the script and installs dependencies;
later runs start instantly.

Common invocations (everything after `--` goes to Codex unchanged):

```
excel-codex                                   interactive Codex, default gpt-5.6-sol (through Excel)
excel-codex --model gpt-6-sol-1m-excel        pick another model
excel-codex -- -c model_reasoning_effort=high set reasoning effort
excel-codex -- exec "add a table of contents to the README"
excel-codex -- resume --last                  resume the last session
excel-codex status                            which sign-in is used, is it usable, when it expires
excel-codex --login codex                      use Codex's own sign-in only (never touch Excel)
excel-codex login                             open Excel's ChatGPT pane to sign in or refresh
excel-codex desktop                           route the Codex desktop app / IDE extension here
excel-codex threads migrate                   move the bridge's own conversations into the shared list (done at start too)
```

Launcher options: `--login <auto|codex|excel>`, `--model`, `--proxy`, `--timezone <auto|off>`,
`--image-model <name>`, `--port`, `--codex <path>`, `--webview-dir <dir>`, `--skip-session-check`,
`--no-auto-signin`.

## Models

Every model comes in two versions: the same upstream model with a different context length.

| 272k version | 1M version | Upstream model |
| --- | --- | --- |
| `gpt-5.6-sol` (default) | `gpt-5.6-sol-1m-excel` | `gpt-5.6-sol` |
| `gpt-5.6-terra` | `gpt-5.6-terra-1m-excel` | `gpt-5.6-terra` |
| `gpt-5.6-luna` | `gpt-5.6-luna-1m-excel` | `gpt-5.6-luna` |
| `gpt-6-sol` | `gpt-6-sol-1m-excel` | `gpt-6-sol` |
| `gpt-6-luna` | `gpt-6-luna-1m-excel` | `gpt-6-luna` |
| `gpt-6-astra` | `gpt-6-astra-1m-excel` | `gpt-6-astra` |

- From 0.5.4 the 272k versions use OpenAI's own names in Codex (the model list shows them as, for
  example, "6-Sol Excel"), so a conversation carries on whether the bridge is on or off, see
  [Session sharing](#session-sharing). The earlier names such as `gpt-6-sol-excel` still work; they
  are just no longer in the model list. OpenAI has no 1M versions, so their names stay.

- **272k version**: 272k context; Codex compacts automatically at 180k.
- **1M version**: 918k context; Codex compacts automatically at about 826k. 918k is the limit measured
  on the real backend: it accepts up to about 918k input tokens per request and answers
  `context_length_exceeded` beyond that. gpt-5.6-sol, gpt-6-sol, gpt-6-luna and gpt-6-astra measured
  the same; gpt-5.6-terra and gpt-5.6-luna use the same limit.
- Each turn of a long conversation sends more context and uses more of your plan, so pick the 272k
  version when you don't need the length.

Reasoning effort `low` / `medium` / `high` / `xhigh`, default `medium`.

To try any other upstream model, set `GHCP_EXCEL_UPSTREAM_MODEL=<upstream name>`, which sends
every request to that model.

## Proxy

For the bridge's outbound connection to `bps.openai.com`:

- `--proxy http://127.0.0.1:7890` (`socks5h://…` works too), or the `EXCEL_BRIDGE_PROXY` variable;
- otherwise `HTTPS_PROXY` and the system proxy are used.

TLS certificate verification is always on. Codex reaches the bridge on `127.0.0.1`, which the
launcher adds to `NO_PROXY`.

## Automatic sign-in (Windows)

The tool never signs in itself; it opens the official ChatGPT pane in Excel for you:

1. With no usable session, it writes a small workbook
   (`%LOCALAPPDATA%\excel-codex-bridge\excel-codex-sign-in.xlsx`) that embeds the ChatGPT add-in
   (Marketplace asset `WA200010215`) and opens it in Excel, which shows the pane.
2. The first time, Excel asks you to trust / install the add-in; then sign in in the pane. If no
   pane appears, click Home → Add-ins → ChatGPT.
3. Once the new session shows up, the workbook is closed again, and Excel too if the tool started
   it and nothing else is open.

When less than 24 hours are left, a running bridge opens Excel minimized in the background; a
signed-in pane usually refreshes on its own, and Excel closes again. `excel-codex login` does this
on demand (`--force` opens the pane even when the session is fine). To keep the tool away from
Excel, pass `--no-auto-signin` or set `EXCEL_BRIDGE_AUTO_SIGNIN=0`.

> Opening the pane relies on Office's "open an add-in with a document" feature. It is new in
> v0.2.1 and not yet verified on every Office build; some builds or organization policies may not
> honor it. Then open the pane by hand (step 2) and the rest still happens automatically.

## Codex desktop app / IDE extension

These clients cannot take `-c` and read only `~/.codex/config.toml`, which the tool can point at
the bridge for as long as you need it:

1. Double-click **`excel-codex-desktop.cmd`** from the release zip (or run `excel-codex desktop`).
   It checks the session (signing in if needed), points `config.toml` at the bridge and runs the
   bridge on `127.0.0.1:8765`.
2. **Fully quit and reopen the Codex desktop app** (or reload the IDE window); the model list
   shows the bridge's models (their display names say Excel). If Codex is signed in, earlier
   conversations are in the list too and carry on, see [Session sharing](#session-sharing).
3. Keep that window open while you work (minimizing is fine). **Closing it or pressing Ctrl+C
   restores `config.toml` exactly.**
4. On Windows the window also sets the system timezone to Codex's exit timezone, see
   [Exit timezone](#exit-timezone).

Details:

- The original file is saved as `config.toml.before-excel-codex`. Your own `model`,
  `model_provider` and similar lines are only commented out and come back byte for byte.
- `config.toml` is shared by every Codex client, so plain `codex` in a terminal also uses the
  bridge while the window is open.
- To keep it on: `excel-codex desktop --keep-config`, and later `excel-codex desktop --off`
  (also the fix if the window was killed before it could restore the config).
- Other model: `excel-codex desktop --model gpt-5.6-terra`; other port: `--port`.

**Error `The '…' model is not supported when using Codex with a ChatGPT account`** (after signing
out of Codex it becomes `401 Unauthorized: Missing bearer or basic authentication` for
`api.openai.com/v1/responses`). The conversation's request did not go through the bridge; it went
straight to OpenAI, so signing out does not help. Models OpenAI does not have (the 1M versions, or
the `*-excel` names of 0.5.3 and earlier) fail like this. The desktop app reads `config.toml` and
the model list only when it starts. Common causes:

- The bridge window was closed (so `config.toml` was restored) but the desktop app was not
  restarted, so the list still shows the bridge's models.
- A 1M conversation is continued after the bridge was closed: pick an OpenAI model in the model
  menu.
- With Codex not signed in (the bridge is then a provider of its own), the conversation was started
  before desktop mode was on.
- A tool such as Cockpit Tools switched Codex back to a ChatGPT account.

Fix: open `excel-codex-desktop.cmd`, fully quit the desktop app (File → Quit, or quit from the
tray; after closing the window it may still run in the background) and reopen it. Each message then
shows a line like `"POST /v1/responses HTTP/1.1" 200` in the bridge window (0.4.2 and later); if
nothing shows up, the request still bypasses the bridge.

**After the bridge is closed, a conversation fails with `stream disconnected before completion:
… (os error 10061)` (connection refused) and "Reconnecting x/5"**: the bridge window was closed
but Codex kept running. A conversation opened while the bridge was on keeps the bridge's local
address (`127.0.0.1:<port>`), where nothing listens any more. Closing the desktop app's window is not
enough: on Windows it keeps running in the tray. Quit it from its tray icon (`Cmd+Q` on macOS),
reload IDE windows that use Codex, then open Codex again and it uses the official sign-in. The bridge
window says so when it closes; closed with Ctrl+C while it still sees a Codex process, it adds
`Codex is still running right now`.

Fully manual alternative: run `excel-codex serve` and add the output of `excel-codex print-config`
to `config.toml`; remove those lines to go back.

## Session sharing

Codex lists and continues conversations by the provider they were started with. Up to 0.5.3 the
bridge was a provider of its own (`excel-bridge`), so with the bridge on the earlier conversations
of the official sign-in were not listed, and with it off the ones started through the bridge could
not carry on.

From 0.5.4, **as long as Codex itself is signed in** (`codex login`, with a ChatGPT account or an
API key), desktop mode and the Codex that `excel-codex` starts point Codex's own `openai` provider
at the bridge (`openai_base_url`) and use OpenAI's model names. So:

- With the bridge on: the earlier conversations of the official sign-in are all listed and carry on
  through Excel.
- With the bridge off: the conversations started with the bridge on are still listed and carry on
  through the official sign-in. A 1M conversation first needs an OpenAI model picked in the model
  menu.
- The reasoning each backend encrypts may not be readable by the other. When the Excel backend
  cannot read the official one's, the bridge sends the conversation again without that reasoning;
  the conversation's text is unaffected (the bridge window shows a line about it). Whether the
  official backend reads the Excel backend's has not been tested yet; if a conversation fails after
  the bridge is closed, start a new one.
- `codex exec` shows a line `failed to connect to websocket: 426`: Codex's own provider tries a
  WebSocket first, the bridge speaks only HTTP and answers 426 so that it switches to HTTP right
  away. This is expected.

When Codex is not signed in, its own provider asks for a sign-in first, so the bridge stays the
separate `excel-bridge` provider and behaves as before: conversations started with the bridge on
are listed only while it is on. After `codex login` they move into the shared list by themselves,
as the next section describes.

### The bridge's own conversations

Conversations started through the bridge with 0.5.3 and earlier, or while Codex was not signed in,
are filed under `excel-bridge`. They are not in the shared list, and with the bridge off Codex
cannot open them: "Model provider `excel-bridge` not found". When Codex is signed in
(`codex login`), the bridge moves them into the shared list by itself:

- `excel-codex-desktop.cmd` (`excel-codex desktop`) and `excel-codex` move them when they start
  while Codex is fully quit; the window says `Moved N conversation(s) … into the shared list`.
- While Codex runs (the desktop app, an IDE extension, or `codex` in a terminal) nothing is moved,
  and the window says so: a running Codex puts the change back, and may be writing to those
  conversation files. So **start the bridge first, then the desktop app**; or quit Codex and run
  `excel-codex threads migrate`.
- Moved conversations are filed under `openai`, with OpenAI's model names (`gpt-6-sol-excel` →
  `gpt-6-sol`). OpenAI has no 1M versions, so a 1M conversation carries on with the same model's
  official version (`gpt-6-sol-1m-excel` → `gpt-6-sol`, 272k); pick a 1M model again in the model
  menu to use one with the bridge on. They open and carry on with the bridge off (through the
  official sign-in or a relay), and renaming or archiving them no longer moves them back.

What changes:

- Codex's conversation index (the threads table in `~/.codex/state_<n>.sqlite`). It is copied first,
  to `state_<n>.sqlite.before-excel-codex-<time>` in the same folder.
- Where each of those conversation files (under `~/.codex/sessions`) says what Codex rebuilds the
  index from: the provider on the first line, and the model and provider on the later
  `turn_context` and `thread_settings_applied` lines. A change to the index alone is put back to
  `excel-bridge` or the bridge's model names when Codex rebuilds it. Each value is changed in place:
  the rest of the file, the conversation itself and the file's modification time stay as they are.
- Conversations moved by earlier versions are finished: 0.5.4 and 0.5.5 changed the index only;
  0.5.6 to 0.5.8 left the model names on the later lines, and the 1M ones, so with the bridge off
  Codex could go back to the bridge's model names and fail, through the official sign-in or a relay.
  The next start of the bridge while Codex is fully quit finishes them; the window says
  `Finished N conversation(s) moved by an earlier excel-codex`.
- `excel-codex threads` lists the conversations still under `excel-bridge`. `excel-codex threads undo`
  (also with Codex quit) undoes the move: conversations it changed, and Codex has not changed since,
  go back under `excel-bridge`.
- To keep it from moving them at start, set `EXCEL_BRIDGE_AUTO_MIGRATE=0` and run
  `excel-codex threads migrate` when you want.

## Exit timezone

Codex writes this computer's timezone and date into every conversation (`<timezone>` and
`<current_date>` in `<environment_context>`). Requests leave from the proxy exit; when this
computer's timezone differs from the exit's, the model assumes you are in another timezone.

- **Requests through the bridge**: the bridge looks up the exit IP
  (`https://bps.openai.com/cdn-cgi/trace`), then that IP's timezone (asking ipwho.is, ipapi.co,
  get.geojs.io and api.ip.sb in turn), and replaces the timezone and today's date in the request
  with the exit's. It checks every 5 minutes whether the exit IP changed.
- **Codex's official ChatGPT sign-in** (Windows): this route does not go through the bridge, and
  Codex reads the Windows system timezone. While `excel-codex-desktop.cmd` is open it looks up the
  exit timezone of `chatgpt.com` (`api.openai.com` with an API key) the way Codex picks its proxy
  (`HTTPS_PROXY` / `ALL_PROXY`, else the Windows proxy settings; PAC scripts are not supported),
  and sets the system timezone to it with `tzutil` at start and every minute after, then puts the
  earlier one back when the window is closed (its close button or Ctrl+C). Nothing else has to
  run, and no scheduled task is created.
- **Cloudflare's country decides**: the exit IP lookup also gives the country Cloudflare places the
  exit in (which is what OpenAI sees). A lookup service that answers another country is not used
  and the next one is asked; when none agrees the timezone is left alone and what each answered is
  shown.

Notes:

- This changes the whole computer's timezone. A window that is killed (for example from Task
  Manager) cannot put it back; `excel-codex timezone restore` restores the timezone from before the
  first change.
- Only the exit IP is sent to these lookup services, through the same proxy; nothing else is sent.
  The answer for an IP is cached.
- `--timezone off` (or `EXCEL_BRIDGE_TIMEZONE=off`) turns both off.
- `excel-codex timezone` shows the exit timezone and the current state;
  `excel-codex timezone sync --probe` looks up without changing anything.
- A country you did not expect, such as `(Cloudflare: TW)`, means your proxy sends OpenAI's traffic
  through a node there, so that is what OpenAI sees; the proxy app's home page only shows the
  default node. Opening `https://chatgpt.com/cdn-cgi/trace` through the same proxy shows the
  country OpenAI sees on its `loc=` line.

## macOS / WSL (experimental)

**macOS, no install** (reads the WebKit local storage in Mac Excel's sandbox; no Windows PC needed):

1. In Mac Excel, click Home → Add-ins → ChatGPT and sign in once in the pane. There is no automatic
   sign-in on the Mac; the session lasts about 10 days, then open the pane again. The bridge does
   not need a restart.
2. Download `excel-codex-bridge-<version>-macos-arm64.tar.gz` (Intel Macs: `macos-x64`) from
   [Releases](https://github.com/Kaixxrua/excel-codex-bridge/releases/latest) and double-click it to extract.
3. The build is not signed by Apple. If you downloaded it with a browser, clear the quarantine flag once
   in Terminal, or macOS will say the developer cannot be verified:

   ```
   xattr -dr com.apple.quarantine ~/Downloads/excel-codex-bridge-<version>-macos-arm64
   ```

   (Downloads made with `curl` carry no quarantine flag; the release notes have the command.)
4. Then use it as on Windows:
   - Codex CLI: run `<folder>/excel-codex` from your project folder, with the same arguments; or add
     the folder to `PATH`.
   - Codex desktop app: double-click `excel-codex-desktop.command`, then quit the desktop app with
     `Cmd+Q` and open it again. Keep the Terminal window open; closing it or pressing Ctrl+C restores
     `config.toml` exactly.

The first time the session is read, macOS may ask whether Terminal may access data from other apps;
allow it. To run from source instead, use `./excel-codex.sh` (Python 3.10+), and
`./excel-codex.sh desktop` for desktop mode.

**WSL**: Excel lives on the Windows side, so point the bridge at its data folder:

  ```
  ./excel-codex.sh --webview-dir /mnt/c/Users/<you>/AppData/Local/Microsoft/Office
  ```

## Session expiry

The add-in's token lasts about 10 days. The bridge checks it before every request and re-reads
the local cache when it has expired or is about to; on Windows it also
[refreshes it through Excel](#automatic-sign-in-windows) when less than 24 hours are left. **No
restart** of the bridge or Codex is needed. `excel-codex status` shows the time left.

## Rate limits

Everyone using the add-in shares one tokens-per-minute budget per model. When it is used up, the
backend fails the request at once with `rate limit exceeded`, saying to try again in a few
milliseconds; Codex does, five times within a second, and the turn fails ("Reconnecting 5/5").

So the bridge waits first: when a request is rate limited before any of the answer has come, it
sends the same request again after 1, 2, 4, 8 and 15 seconds (then every 15 seconds), for up to 5
minutes. Meanwhile Codex just shows it is working (the bridge tells it every 10 seconds that the
response is still going, so its five-minute idle timeout does not fire), and the bridge window
(`bridge.log` for the CLI) says `the Excel backend is rate limited … trying again in N s`. If it is
still rate limited after 5 minutes, Codex shows `The Excel backend is still rate limited after 5
minutes …` and the turn ends; send the message again later. Codex does not retry that itself, or
each of its retries would wait another 5 minutes. You can interrupt in Codex at any time.

- A request that fails after its answer has begun is not sent again, so nothing is repeated; other
  errors reach Codex as they came.
- `EXCEL_BRIDGE_RATE_LIMIT_WAIT=<seconds>` sets how long to wait: `300` by default, at most `1800`;
  `0` for no wait, the error then reaching Codex as it came (it retries five times, quickly, its own
  way).

## Pictures

Screenshots pasted into Codex, `codex -i picture.png` and the model's `view_image` all work, with
nothing to set up. The bridge passes them on like this:

- **Inline where it can.** Pictures go with the request the way Codex sends them (inline
  `data:` URLs), without a separate upload.
- **Uploaded only where inline is refused.** Where the Excel backend will not take a picture inline
  (at present, in user messages), the bridge uploads it the way the Upload file button in the Excel
  add-in does: on the same session, to OpenAI's attachments endpoint
  (`bps.openai.com/basispoints/api/attachments`), and the request names it by the file id that comes
  back. The bridge finds out where by itself: the first refusal switches that kind of place to
  uploads, and later pictures there are uploaded straight away.
- **Each picture once.** Later requests in the same run reuse its file id (per account, kept in
  memory only).
- If an upload fails or the backend still refuses, the bridge replaces the picture with a short note
  and sends the request anyway; the reason is in the window or in `bridge.log`.

Pictures go to `bps.openai.com` only, like the rest of the request: no third party, no tunnel, no
image host and nothing to configure. Uploads use the same network settings as requests (including
`--proxy`). OpenAI keeps uploaded pictures as it keeps files you upload in the add-in.

**The desktop app says "This model does not support image inputs".** The desktop app blocks the
picture itself; no request is sent. It decides from the model catalog, and it reads the catalog only
at startup:

- Versions 0.3.1 and earlier wrote a catalog that marked the Excel models as text-only. After
  upgrading, start desktop mode again (`excel-codex-desktop.cmd` or `excel-codex desktop`), then
  fully quit the desktop app (quit from the tray, or `Cmd+Q` on macOS) and open it again.
- When a tool such as Cockpit Tools manages Codex, that tool writes the model catalog, not this
  bridge. Turn on image input for the `*-excel` models in its model provider settings (Cockpit 1.3.57
  and earlier leave it off by default).

## Image generation

From 0.4.6, Codex's own image tool works: ask Codex to "draw …" or "change this picture to …".
The picture shows up in the conversation and is saved under `~/.codex/generated_images/`.

- Codex offers the tool only when the provider sets an `x-openai-actor-authorization` header. The
  launcher's `-c` overrides, desktop mode and `print-config` all set it (to `excel-codex-bridge`;
  the bridge does not use the value or send it anywhere). If you added this header by hand earlier,
  you can remove it after upgrading.
- The bridge sends the request to the endpoints the add-in draws with: a new picture to
  `bps.openai.com/basispoints/api/images/generations`, a change to `/images/edits`, on the same
  session and with the add-in's choices: gpt-image-2, PNG, sizes `auto`, `1024x1024`, `1536x1024`,
  `1024x1536` and `1280x720`.
- Transparent backgrounds are not available (the add-in does not offer them either). When the
  model asks for one it is told so, and can draw on a plain background instead.
- Pictures count against your ChatGPT plan. When the backend refuses, Codex shows the reason the
  backend gave.
- **Choosing the image model**: Codex's image tool always asks for gpt-image-2 and does not tell the
  model in the conversation which model drew, so asking it "was this model X?" gets no sure answer.
  For another model (say `gpt-image-2.5`), start with `--image-model gpt-image-2.5` (or set
  `EXCEL_BRIDGE_IMAGE_MODEL`) and the bridge asks the backend for that one instead. The bridge window
  shows the image model at start and logs a line like
  `image generations with gpt-image-2.5: 1 picture(s) came back` for each picture; go by that.
  Whether the backend takes the name is up to the backend; when it does not, Codex shows its reason.
- When conversations are shared with Codex's own ChatGPT sign-in (see
  [Session sharing](#session-sharing)), Codex offers this tool for its own provider anyway; with an
  API key sign-in it does not.
- When a tool such as Cockpit Tools manages Codex, it writes the provider, so add
  `http_headers = { "x-openai-actor-authorization" = "excel-codex-bridge" }` to its provider
  settings yourself.

## Update check

From 0.4.4 on, the tool checks GitHub in the background for a newer release (at most every 12 hours)
and, if there is one, shows its version, what changed and the download link:

- In the desktop-mode and `serve` windows: after startup, and whenever a release comes out while the
  window stays open.
- When `excel-codex` starts Codex: after Codex exits. A window opened by double-click then waits for
  Enter before it closes.

The request goes only to `api.github.com`, carries nothing but the version in its User-Agent, and
uses the same proxy settings as the bridge. If GitHub cannot be reached, nothing is shown and nothing
else changes. Set `EXCEL_BRIDGE_UPDATE_CHECK=0` to turn it off.

To update, close any running bridge window and Codex, then extract the new release over the old folder
(or into a new one). Settings and state are not kept in the install folder, so nothing is lost. When
running from source, `git pull` is enough.

## Limitations

- Responses API only; there is no `/responses/compact` endpoint.
- The Excel backend adds a fixed prefix of about 22k tokens to every request (mostly served from
  cache); usage counts against your ChatGPT plan, and more sessions at once use it up faster.
- It relies on a private backend of the Excel add-in and may break whenever OpenAI changes it.

## Risks and disclaimer

- This is an **unofficial** project, not endorsed by OpenAI or Microsoft. Using the add-in's
  backend outside Excel may violate OpenAI's terms of use and could get your account limited or
  banned. **Use at your own risk.**
- Use it only with **your own account on your own computer**. Do not expose the bridge to a
  network, share it with others, or use it as a relay or resale service. The project deliberately
  does not support that: no multi-user mode, no API key management, no non-loopback listening.
- The tool reads a sign-in credential. Reading happens locally; the credential is only held in
  memory and only sent to `bps.openai.com`. Review the code before using it.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `EXCEL_BRIDGE_LOGIN` | Which sign-in to use: `auto` (default) / `codex` / `excel` (same as `--login`), see [Sign-in](#sign-in) |
| `EXCEL_BRIDGE_CODEX_AUTH` | Path to Codex's `auth.json`; default `$CODEX_HOME/auth.json`, then `~/.codex/auth.json` |
| `EXCEL_BRIDGE_PROXY` | Outbound proxy (same as `--proxy`) |
| `EXCEL_BRIDGE_HOME` | State folder for the model catalog JSON, `bridge.log`, the sign-in workbook, and `tool-calls.sqlite3`, which lets earlier tool calls replay exactly after a restart (kept 60 days). Default `%LOCALAPPDATA%\excel-codex-bridge` or `~/.excel-codex-bridge` |
| `EXCEL_BRIDGE_AUTO_SIGNIN` | `0` keeps the tool from opening Excel (same as `--no-auto-signin`) |
| `EXCEL_BRIDGE_UPDATE_CHECK` | `0` turns off the update check |
| `EXCEL_BRIDGE_AUTO_MIGRATE` | `0` keeps the bridge from moving its own conversations into the shared list at start, see [The bridge's own conversations](#the-bridges-own-conversations) |
| `EXCEL_BRIDGE_TIMEZONE` | `auto` (default) / `off`, same as `--timezone`, see [Exit timezone](#exit-timezone) |
| `EXCEL_BRIDGE_IMAGE_MODEL` | The model Codex's image tool asks the backend for, default `gpt-image-2` (same as `--image-model`), see [Image generation](#image-generation) |
| `EXCEL_BRIDGE_RATE_LIMIT_WAIT` | Seconds to wait out a rate limit before Codex gets the error: `300` (5 minutes) by default, `0` for none, at most `1800`; see [Rate limits](#rate-limits) |
| `CODEX_HOME` | Codex config folder whose `config.toml` `desktop` edits. Default `~/.codex` |
| `GHCP_EXCEL_WEBVIEW2_DATA_DIR` | Windows WebView2 data root (same as `--webview-dir`) |
| `GHCP_EXCEL_WEBKIT_WEBSITE_DATA_DIR` | macOS WebKit data folder |
| `GHCP_EXCEL_RESPONSES_URL` | Upstream URL (for testing) |
| `GHCP_EXCEL_UPSTREAM_MODEL` | Force the upstream model name |

The `GHCP_EXCEL_*` names match ghcp_proxy, so existing setups carry over.

## Development

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest     # Windows: .venv\Scripts\pip
PYTHONPATH=src .venv/bin/python -m pytest
```

## Credits and license

The Excel session reader and the Basispoints protocol adapter are extracted from
[Nonary/ghcp_proxy](https://github.com/Nonary/ghcp_proxy) (Unlicense, based on commit `ad23ce2`;
original license in [UPSTREAM-LICENSE](UPSTREAM-LICENSE)). This project is released under the
[Unlicense](LICENSE) as well.

**The idea of using Codex's own sign-in to reach the Excel backend (so Excel need not be
installed)** comes from [MIKUbiu/bps-local](https://github.com/MIKUbiu/bps-local) (Unlicense) —
thanks to MIKUbiu for it. This project is a separate implementation: it wires that sign-in in as an
optional source of the existing bridge and fallback, with our own code. bps-local's Basispoints
protocol code derives from [hloolx/codex2api](https://github.com/hloolx/codex2api) (MIT, ported via
[ranxi2001/sub2api](https://github.com/ranxi2001/sub2api)); our own such code comes from ghcp_proxy
above.

Thanks to the [LINUX DO](https://linux.do) community.
