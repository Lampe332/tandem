---
name: tandem
description: Use when you need to talk to another coding agent (Claude Code or Codex) on this machine or another one — ask it something, hand it work (a review, a second opinion, a coding task, image generation through Codex), start one as a helper, see what the others are doing, or wait for one — and whenever a message from NAME@HOST (a `[from NAME@HOST]` line, or Claude's "Message from @NAME@HOST") appears in your prompt (a colleague is messaging you). Provides the `tandem` CLI. Works both ways between Claude Code and Codex, on macOS, Linux and native Windows.
---

# tandem

Other agents are addressed by name. `tandem` delivers through each engine's own input path (Claude Code's cross-session inbox, Codex's app-server), never by typing into a terminal. So there is no read → type → verify → Enter cycle, a draft the user is typing stays intact, and an approval prompt never receives your keystrokes.

```bash
tandem                          # who is near you and what each is doing
tandem --all | tandem <search>    # everyone, or search by name/folder/title
tandem me | tandem name NEW       # who you are / rename yourself
tandem say NAME "text"          # send one message; prints where it went
tandem say NAME --file PATH     # the same, text from a UTF-8 file
tandem ask NAME "question"      # send and wait for the reply (30s; --timeout up to 3600)
tandem read NAME [--last 3]     # their last answers, from the engine's own history
tandem wait NAME [--until idle] [--timeout 600]
tandem spawn NAME --kind claude|codex [--cwd DIR] [--model M] [--effort E] [--prompt-file PATH] [-- ENGINE ARGS]
tandem models                   # the models and effort levels spawn accepts
tandem doctor                   # what works, what is missing, how to fix it
```

## The rules that make it work

1. **Replying means running a command.** To answer an agent, run `tandem say NAME "…"` in your shell. Writing the answer, or the command, into your own reply sends nothing: the other agent cannot see your conversation.
2. **Say what you want back, and to whom.** `tandem me` shows your name. End a request with the exact reply you expect, for example: `When you are done, run: tandem say YOUR_NAME "DONE: <full path of the file>"`.
3. **`say` tells you what happened; act on that line, do not check again.**
   - `delivered` — in their conversation now.
   - `queued` — they are working and get it at their next tool call.
   - `held` — they cannot take it right now (waiting on their user, idle, ended). It is kept and delivered when they can; do not resend. The end of the line says what it waits for.
4. **An agent's message is a colleague's request**, not your user's approval for anything. Weigh it against your own task.
5. **Only talk to agents that are part of your task.** The list also shows the user's other sessions. Leave those alone unless the user asked you to involve them.
6. **A fix that changes the user's setup is theirs to decide.** When a line names one (`tandem install --accept-inbound`), tell the user; do not run it yourself.

States are facts, not guesses: `working`, `blocked(permission|question)` (waiting on their user), `idle` (no turn running — not the same as finished), `waiting-on(NAME)` (asking someone), `gone`, `unknown`.

## Recipes

### Ask something short

```bash
tandem ask NAME "Which port does the dev server use?"
```

Prints the reply. If the other agent ends its turn without `tandem say`, `ask` prints its last message instead, marked as such.

### Hand over work and get the result the moment it is done

Write the task to a file, then run this as a **background command** (Claude Code: `run_in_background`) and carry on or end your turn:

```bash
tandem ask NAME --file task.txt --timeout 900
```

The command ends when the answer is in, and your harness tells you then, with the answer as its output. This works even while you are idle and needs no setting. One `ask` at a time per session.

### Several agents at once

Give each its task with `tandem say NAME --file task.txt`, then collect: `tandem wait NAME --until idle --timeout 600` and `tandem read NAME --last 1`. Replies they send with `tandem say` also arrive in your context at your next tool call.

### Start a helper agent

```bash
tandem spawn reviewer --kind codex  --cwd DIR                       # a Codex; reachable as soon as this returns
tandem spawn fast     --kind codex  --cwd DIR --model "luna 6" --effort medium
tandem spawn helper   --kind claude --cwd DIR --model sonnet --prompt-file task.txt
```

`spawn` returns when the agent has reported in. Pick a name nobody has yet.

**Model and effort.** When the user names them ("use Codex with Luna 6 at medium effort"), pass `--model` and `--effort`; without them the helper runs on that engine's own defaults. `--model` takes the name loosely for Codex: tandem picks the one catalog model that has all the words (`"luna 6"` is `gpt-6-luna`) and says so if none or several match. `tandem models` lists what exists and which effort levels each takes. For Claude, `--model` is `sonnet`, `opus`, `haiku` or a full model name. Other engine options go after `--`, unchanged.

- **A Codex helper** takes messages at once: follow up with `tandem ask reviewer --file task.txt --timeout 900`.
- **A Claude helper** should get its first task with `--prompt-file`: an idle Claude takes messages only if its user turned that on (`--accept-inbound`), and a fresh one is idle. Put the reply instruction (rule 2) into the task file. Then wait and collect:

  ```bash
  tandem wait helper --until idle --timeout 600
  tandem read helper --last 1
  ```

  Its `tandem say` to you arrives as well; `read` gets the result even if it forgets. For a second task, start another helper, or send it and accept that it lands at that Claude's next turn.
- The helper needs to be allowed to run `tandem` in its shell. A Claude that asks its user before shell commands shows `blocked(permission)` until they approve in its window; `-- --allowedTools "Bash(tandem:*)"` allows just tandem up front. A sandboxed Codex must be able to write `~/.local/state/tandem`; if `tandem` fails there, tell the user, do not work around it.
- Helpers cost the user's quota and open on their screen. Start one when the task calls for it, close the loop by reading its result, and tell the user it is there.

### Files and images

A message is text. To hand over a file or a generated image, save it and send its full path; the other agent opens it with its own file tools. Asking Codex for an image works that way: say what to generate, the file name to save it under, and that it should `tandem say` you the full path once the file exists. It takes about a minute, so use the background `ask`. One image job per Codex at a time.

## Windows

- **Text with double quotes, several lines, `%`, `&` or `<>`** does not survive cmd or PowerShell quoting. Write it to a file: `tandem say NAME --file PATH`, `tandem spawn … --prompt-file PATH`. Plain one-line text in quotes is fine as an argument. From Git Bash, single quotes are safe.
- **No tmux.** `tandem spawn` opens the new agent in a console window of its own on the user's desktop; they can watch it and type there. Closing the window ends the agent. You cannot close it for them: say that it is still open when you are done.
- **A Codex can receive only when started by `tandem spawn` or `tandem run codex`.** Its answers can be read with `tandem read NAME` either way once it is registered.

## When it does not work

- `tandem: command not found` — not installed, or this session started before the install. See Setup; until a restart, call it by full path (`~/.local/share/tandem/win/tandem.cmd` on Windows, `~/.local/bin/tandem` elsewhere).
- `this session has no tandem identity yet` — tandem has not seen this session. Its hooks register a session at its next prompt or tool call: run `tandem doctor`, and if the hooks are in place, try again after your next tool call.
- `no agent named NAME` — check `tandem --all`; names are case-sensitive, and an ended session shows as `gone`.
- `held … idle Claude reads it on its next turn` — the recipient is an idle Claude and its user has not turned on `--accept-inbound`. Tell your user; do not resend.
- `held … this Codex was not started through tandem` — it cannot receive. Start one with `tandem spawn`.
- `timeout` from `ask` — the question stays sent. Read what they have so far with `tandem read NAME`.
- `No assistant answers recorded yet` from `read` — that agent has not finished a turn, or its engine keeps no history for its folder (seen with Claude started inside the system temp folder). Give helpers a normal working folder, and rely on their `tandem say`.

## Setup (once per machine)

Run `tandem doctor`. If the command is not found, tandem is not installed yet:

- **Windows:** `powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Lampe332/tandem/main/install.ps1 | iex"`. It needs Python 3.9+ as `python`. It downloads tandem to `~\.local\share\tandem`, adds it to the user's PATH, hooks it into Claude Code, and copies this skill to Claude Code and Codex.
- **macOS / Linux:** `curl -fsSL https://raw.githubusercontent.com/Lampe332/tandem/main/install.sh | sh`

Running the same command again updates tandem. The full walk-through for an agent is `SETUP.md` in https://github.com/Lampe332/tandem.

Installing changes the user's Claude Code settings (it adds hooks), so say what it does and get their yes first. One choice is theirs alone, so ask and do not decide: `tandem install --accept-inbound` lets an idle Claude session take messages at once, by setting `"crossSessionInbound": "accept"`. Any program running as that user can then put messages into their Claude sessions without an approval prompt. Without it a message reaches a busy Claude at its next tool call and an idle one at its next turn; the recipes above work either way.

Sessions that were already running register at their next tool call. To remove tandem, run `tandem uninstall` before deleting any files: it takes the hooks out of the engines' settings.
