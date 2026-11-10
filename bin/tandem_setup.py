"""tandem install / uninstall / doctor: wire the hooks into Claude Code and Codex, and explain state."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from tandem_core import config, launcher, patient, read, root
from tandem_transport import BIN, hosts, remote

MARKER = 'tandem-hook'
CLAUDE_EVENTS = ['SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest',
                 'Notification', 'Stop', 'StopFailure', 'SessionEnd']
CODEX_EVENTS = ['UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'SessionEnd', 'Interrupt']


def claude_dir():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')


def codex_dir():
    return Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')


WINDOWS = os.name == 'nt'


def hook_command(kind):
    args = [sys.executable, str(BIN / 'tandem-hook'), '--kind', kind]
    if WINDOWS:
        # Claude Code runs hooks through Git Bash or cmd, depending on the setup. Forward slashes and double
        # quotes read the same in both; backslashes would vanish in bash.
        # `|| exit 0`: if tandem's folder is ever deleted without `tandem uninstall`, python ends with code 2, which
        # Claude Code reads as "block this tool call". A missing tandem must never stop the agent from working.
        return ' '.join('"%s"' % a if ' ' in a else a for a in (a.replace('\\', '/') for a in args)) + ' || exit 0'
    return shlex.join(args)


def save(path, data):
    """Back up, then replace atomically; a half-written settings file would break the engine."""
    return save_text(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def save_text(path, text):
    if path.exists() and path.read_text(encoding='utf-8-sig') == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, str(path) + '.tandem-backup-' + time.strftime('%Y%m%d-%H%M%S'))
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.tandem-')
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
        stream.write(text)
    patient(os.replace, temp, path)
    return True


def set_hooks(path, kind, events, remove=False):
    data = read(path) if path.exists() else {}
    hooks = data.setdefault('hooks', {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [h for h in group.get('hooks', []) if MARKER not in h.get('command', '')]
            if kept:
                groups.append(dict(group, hooks=kept))
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event)
    if not remove:
        for event in events:
            hooks.setdefault(event, []).append({'hooks': [{'type': 'command', 'command': hook_command(kind), 'timeout': 3}]})
    if not hooks:
        data.pop('hooks')
    return save(path, data)


def hooks_installed(path, kind):
    return any(h.get('command') == hook_command(kind)
               for groups in read(path).get('hooks', {}).values() for g in groups for h in g.get('hooks', []))


ALIAS_START = '# >>> tandem: plain `codex` starts reachable by tandem (tandem uninstall removes this)'
ALIAS_END = '# <<< tandem'


def shell_rc():
    """The rc file the person's login shell reads, and the alias line in its syntax."""
    shell = Path(os.environ.get('SHELL', '')).name
    if shell == 'fish':
        return Path.home() / '.config' / 'fish' / 'conf.d' / 'tandem.fish', "alias codex 'tandem run codex'"
    if shell == 'bash':
        # macOS terminals open login shells, which read .bash_profile rather than .bashrc
        return Path.home() / ('.bash_profile' if sys.platform == 'darwin' else '.bashrc'), "alias codex='tandem run codex'"
    return Path(os.environ.get('ZDOTDIR') or Path.home()) / '.zshrc', "alias codex='tandem run codex'"


def alias_installed():
    if WINDOWS:
        return False  # no shell rc to put an alias in; Codex is started with `tandem run codex` there
    rc, _ = shell_rc()
    return rc.exists() and ALIAS_START in rc.read_text()


def set_alias(remove=False):
    """A plain `codex` keeps its app-server inside its own process, where tandem cannot reach it, so its
    mail waits forever. Aliasing codex to `tandem run codex` makes the session people already start
    the reachable one. tandem-session passes non-interactive subcommands (exec, login, ...) straight on."""
    rc, line = shell_rc()
    text = rc.read_text() if rc.exists() else ''
    if ALIAS_START in text:
        head, rest = text.split(ALIAS_START, 1)
        text = head.rstrip('\n') + ('\n' if head.strip() else '') + rest.split(ALIAS_END, 1)[-1].lstrip('\n')
    if not remove:
        text = text.rstrip('\n') + ('\n\n' if text.strip() else '') + '%s\n%s\n%s\n' % (ALIAS_START, line, ALIAS_END)
    return rc if save_text(rc, text) else None


def install(argv):
    remove = argv[:1] == ['uninstall']
    accept = '--accept-inbound' in argv
    alias = '--codex-alias' in argv
    if alias and WINDOWS:
        raise ValueError('--codex-alias is not available on Windows; start Codex with `tandem run codex`')
    changed = []
    if set_hooks(claude_dir() / 'settings.json', 'claude', CLAUDE_EVENTS, remove):
        changed.append(str(claude_dir() / 'settings.json'))
    # Windows: Codex cannot be reached yet, so its config is left alone (uninstall still cleans up).
    codex = codex_dir().exists() and (remove or not WINDOWS)
    if codex and (remove and not (codex_dir() / 'hooks.json').exists()):
        codex = False
    if codex and set_hooks(codex_dir() / 'hooks.json', 'codex', CODEX_EVENTS, remove):
        changed.append(str(codex_dir() / 'hooks.json'))
    settings = claude_dir() / 'settings.json'
    if accept and not remove:
        data = read(settings)
        data['crossSessionInbound'] = 'accept'
        if save(settings, data):
            changed.append(str(settings) + ' (crossSessionInbound)')
    if (alias and not remove) or (remove and alias_installed()):
        rc = set_alias(remove)
        if rc:
            changed.append(str(rc) + ' (codex alias)')
    for path in changed:
        print(('removed tandem from ' if remove else 'updated ') + path)
    if not changed:
        print('nothing to change')
    if not remove and codex:
        print('Codex: open /hooks in a Codex session and trust the new tandem hooks; until then Codex skips them.')
    if not remove and not accept and read(settings).get('crossSessionInbound') != 'accept':
        print(INBOUND_NOTE)
    if WINDOWS:
        return
    if not remove and shutil.which('codex') and not alias_installed():
        print(CODEX_NOTE)
    elif alias and not remove:
        print('Open a new shell (or `source %s`) so `codex` picks up the alias.' % shell_rc()[0])


INBOUND_NOTE = '''Idle Claude sessions are not reachable yet. To let them take messages at once:
  tandem install --accept-inbound
This sets "crossSessionInbound": "accept" in Claude's settings. Claude then accepts cross-session
messages from any process of your user without asking. Any such process can already type into your
terminals, so it opens little that was closed; without it, a message waits for the session's next turn.'''

CODEX_NOTE = '''Plain `codex` sessions cannot receive messages: only Codex started with `tandem run codex` can.
To make plain `codex` do that:
  tandem install --codex-alias
This adds `alias codex='tandem run codex'` to your shell rc; `codex exec`, `codex login` and the other
non-interactive commands still run Codex directly.'''


def doctor(argv):
    rows = []
    def line(mark, what, detail='', fix=''):
        rows.append((mark, what, detail, fix))
    line('✓' if shutil.which('tandem') else '✗', 'tandem on PATH', shutil.which('tandem') or '',
         '' if shutil.which('tandem') else ('add %s to PATH (install.ps1 does that)' % (BIN.parent / 'win') if WINDOWS
                                          else 'ln -s %s ~/.local/bin/tandem' % (BIN / 'tandem')))
    for kind in ('claude', 'codex'):
        exe = shutil.which(launcher(kind)[0])
        line('✓' if exe else '·', kind, exe or 'not found', '' if exe else 'set {"%s": [...]} in %s if you use a wrapper' % (kind, config() / 'launch.json'))
    claude_ok = hooks_installed(claude_dir() / 'settings.json', 'claude')
    line('✓' if claude_ok else '✗', 'Claude hooks', str(claude_dir() / 'settings.json'), '' if claude_ok else 'tandem install')
    if codex_dir().exists() and not WINDOWS:
        codex_ok = hooks_installed(codex_dir() / 'hooks.json', 'codex')
        line('✓' if codex_ok else '✗', 'Codex hooks', 'trust them once with /hooks in Codex', '' if codex_ok else 'tandem install')
    inbound = read(claude_dir() / 'settings.json').get('crossSessionInbound')
    line('✓' if inbound == 'accept' else '·', 'idle Claude reachable', 'crossSessionInbound=%s' % (inbound or 'unset'),
         '' if inbound == 'accept' else 'tandem install --accept-inbound  (otherwise messages wait for the next turn)')
    if WINDOWS:
        if shutil.which(launcher('codex')[0]):
            line('·', 'plain codex reachable', 'no alias on Windows: start Codex with `tandem run codex` or `tandem spawn NAME --kind codex`')
    elif shutil.which(launcher('codex')[0]):
        rc = shell_rc()[0]
        reachable = alias_installed()
        line('✓' if reachable else '·', 'plain codex reachable', 'alias in %s' % rc if reachable else 'only `tandem run codex` sessions',
             '' if reachable else 'tandem install --codex-alias  (otherwise a plain `codex` never gets messages)')
    if not WINDOWS:
        line('✓' if shutil.which('tmux') else '·', 'tmux', 'for tandem spawn and nearby grouping', '' if shutil.which('tmux') else 'install tmux')
    line('✓', 'state', str(root()))
    try:
        others = hosts()
    except ValueError as error:
        others = {}
        line('✗', 'hosts.json', str(error))
    for name in others:
        try:
            count = len(remote(name, {'op': 'list'})['agents'])
            line('✓', 'host ' + name, '%d sessions' % count)
        except Exception as error:  # noqa: BLE001 — report every host, whatever broke
            line('✗', 'host ' + name, str(error)[:120], 'check ssh %s and tandem there' % others[name]['ssh'])
    width = max(len(r[1]) for r in rows)
    for mark, what, detail, fix in rows:
        print('%s %-*s  %s' % (mark, width, what, detail))
        if fix:
            print('  ' + ' ' * width + '  → ' + fix)
    return 1 if any(r[0] == '✗' for r in rows) else 0
