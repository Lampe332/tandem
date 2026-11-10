#!/bin/sh
# Link tandem onto PATH, hook it into Claude Code and Codex, install the agent skill, then check.
# Extra arguments go to `tandem install` (for example --accept-inbound).
set -eu
case "$0" in
    *install.sh) here=$(cd "$(dirname "$0")" && pwd) ;;
    *) here="" ;;   # piped: $0 is the shell, not this file
esac
# Piped from curl there is no checkout next to this script: fetch one, then run its installer.
if [ ! -x "$here/bin/tandem" ]; then
    dest="${TANDEM_HOME:-$HOME/.local/share/tandem}"
    if [ -d "$dest/.git" ]; then
        git -C "$dest" pull --ff-only --quiet
    else
        git clone --depth 1 --quiet https://github.com/Lampe332/tandem "$dest"
    fi
    exec sh "$dest/install.sh" "$@"
fi
mkdir -p "$HOME/.local/bin"
ln -sfn "$here/bin/tandem" "$HOME/.local/bin/tandem"
"$here/bin/tandem" install "$@"
for skills in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    if [ -d "$(dirname "$skills")" ]; then
        mkdir -p "$skills"
        ln -sfn "$here/skills/tandem" "$skills/tandem"
    fi
done
echo
"$here/bin/tandem" doctor || true
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo; echo "Add ~/.local/bin to PATH so agents can run tandem." ;;
esac
