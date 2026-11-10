# Setting up tandem

This page is for the coding agent (Claude Code, Codex, or another) that a person has asked to set up tandem, perhaps with no more than a link to this repository and "install this". tandem lets their Claude Code and Codex sessions message each other by name, through each engine's own input path, and start each other as helpers. A setup that only half works looks broken to them, so finish the steps below rather than stopping at the install.

Installing adds hooks to their Claude Code settings and a folder to their PATH. They asked for the install, so go ahead with it; the choices in step 3 are separate, and theirs.

## 1. Install

Windows, in PowerShell (your shell tool may be Git Bash: then run it as `powershell -ExecutionPolicy Bypass -Command "…"`):

```powershell
iex (irm https://raw.githubusercontent.com/Lampe332/tandem/main/install.ps1)
```

macOS and Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/Lampe332/tandem/main/install.sh | sh
```

This puts tandem in `~/.local/share/tandem`, puts the `tandem` command on PATH, adds its hooks to Claude Code, and installs the agent skill for Claude Code and Codex. It needs Python 3.9 or newer (`python` on Windows, `python3` elsewhere); if that is missing the installer says so, and installing Python is the person's call.

Your own session started before the install, so `tandem` is not on its PATH yet. Until it is restarted, call tandem by full path: `~\.local\share\tandem\win\tandem.cmd` on Windows (`~/.local/share/tandem/win/tandem` from Git Bash), `~/.local/bin/tandem` elsewhere.

## 2. Read `tandem doctor`

`✗` lines block and `·` lines are optional; each carries the command that fixes it. Fix any `✗` yourself. `✗ tandem on PATH` in your own old session is expected (see above) when the installer's own doctor run showed `✓`.

## 3. Ask the choices that are theirs

Each one changes how their own tools behave, so ask rather than decide. Ask in one short message, plainly, and wait for the answer.

- **Idle Claude sessions take messages at once** (`--accept-inbound`). This sets `"crossSessionInbound": "accept"` in `~/.claude/settings.json`, so Claude accepts cross-session messages from any process running as this user without asking. Any such process can already type into their terminals, so little new is opened. Without it, a message reaches a busy Claude at its next tool call and an idle one at its next turn. Everything in the skill works either way; this only makes follow-up messages to an idle Claude arrive at once.
- **Plain `codex` starts reachable** (`--codex-alias`, macOS and Linux only; skip it if Codex is not installed). This adds `alias codex='tandem run codex'` to their shell rc. A Codex started any other way cannot receive. On Windows there is no alias: Codex is started with `tandem run codex` or `tandem spawn NAME --kind codex`.

## 4. Apply

```sh
tandem install --accept-inbound --codex-alias   # only the flags they agreed to
```

On macOS and Linux with Codex installed, tell them to open `/hooks` once in a Codex session and trust the tandem hooks. Codex lets only the person do that.

## 5. Show it working

Run `tandem doctor` again, then `tandem me` (this session's name; it registers at your first tool call after the install) and `tandem` (who is nearby). Then show the real thing if they agree, because it opens a window on their screen and uses their quota: start a helper, ask it something, show the answer.

```sh
tandem spawn helper --kind codex
tandem ask helper "Reply through tandem with the word PONG."
```

Tell them the helper's window stays open until they close it, and what they can do now: type `/tandem` in Claude Code, or just ask in plain words, for example "start a Codex helper with GPT-6 Luna at medium effort and let it review this file" (`tandem models` lists the models and effort levels).

## Done

`tandem doctor` shows no `✗` in a new session, the person knows what each choice did, and they have either seen an answer arrive or know the one step left (a new session, `/hooks` in Codex).
