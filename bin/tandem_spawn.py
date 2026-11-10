"""tandem spawn: start a named Claude or Codex in a detached tmux session and return when it is ready."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from tandem_core import config, host, launcher, public, read, root, sessions, valid_name
from tandem_transport import BIN, remote


def codex_models():
    """Codex's own catalog: [(slug, display name, [efforts], default effort)], or [] when it cannot be read."""
    try:
        out = subprocess.run(launcher('codex') + ['debug', 'models'], capture_output=True, timeout=30).stdout
        rows = json.loads(out.decode('utf-8', 'replace'))['models']
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return []
    return [(m['slug'], m.get('display_name') or m['slug'],
             [level.get('effort') for level in m.get('supported_reasoning_levels') or [] if isinstance(level, dict)],
             m.get('default_reasoning_level'))
            for m in rows if isinstance(m, dict) and m.get('slug') and m.get('visibility') != 'hide']


def words(name):
    return set(re.findall(r'[a-z]+|\d+(?:\.\d+)?', name.lower()))


def codex_model(name, effort):
    """The catalog's name for what a person called it ("Luna 6" is gpt-6-luna), with the effort checked.
    Returns the name unchanged when there is no catalog to check against."""
    catalog = codex_models()
    if not catalog:
        return name
    hits = [m for m in catalog if m[0] == name] or [m for m in catalog if name and words(name) <= words(m[0])]
    if name and len(hits) != 1:
        raise ValueError('%s Codex model "%s"; tandem models lists them: %s'
                         % ('ambiguous' if hits else 'unknown', name, ', '.join(m[0] for m in hits or catalog)))
    if effort and name and hits[0][2] and effort not in hits[0][2]:
        raise ValueError('%s takes effort %s, not "%s"' % (hits[0][0], ' | '.join(hits[0][2]), effort))
    return hits[0][0] if name else name


def models():
    lines = ['codex  (tandem spawn NAME --kind codex --model MODEL --effort EFFORT)']
    catalog = codex_models()
    for slug, title, efforts, default in catalog:
        lines.append('  %-16s %-16s effort: %s%s' % (slug, title, ' '.join(efforts), '  (default %s)' % default if default else ''))
    if not catalog:
        lines.append('  no catalog: Codex is not installed, or `codex debug models` failed')
    lines += ['claude  (tandem spawn NAME --kind claude --model MODEL --effort EFFORT)',
              '  sonnet | opus | haiku, or a full model name; effort: low medium high xhigh max',
              'A model may be given loosely ("luna 6"); tandem picks the one name that has all those words.']
    return {'text': '\n'.join(lines) + '\n'}


def spawn(argv):
    parser = argparse.ArgumentParser(prog='tandem spawn')
    parser.add_argument('name')
    parser.add_argument('--kind', choices=('claude', 'codex'), required=True)
    parser.add_argument('--cwd', default=os.getcwd())
    parser.add_argument('--host', default=host())
    parser.add_argument('--timeout', type=float, default=30)
    # The new agent's first task. From a file, because a shell mangles quotes and newlines in a long argument.
    parser.add_argument('--prompt-file')
    # Each engine spells these its own way; said here once, they work for both.
    parser.add_argument('--model')
    parser.add_argument('--effort')
    # REMAINDER would swallow --kind after name; split native arguments explicitly.
    extra = argv[argv.index('--') + 1:] if '--' in argv else []
    prefix = argv[:argv.index('--')] if '--' in argv else argv
    args = parser.parse_args(prefix)
    valid_name(args.name)
    if args.kind == 'codex' and (args.model or args.effort):
        model = codex_model(args.model, args.effort) if args.host == host() else args.model  # only this machine's catalog is at hand
        extra = (['-m', model] if model else []) + (['-c', 'model_reasoning_effort=' + args.effort] if args.effort else []) + extra
    elif args.model or args.effort:
        extra = (['--model', args.model] if args.model else []) + (['--effort', args.effort] if args.effort else []) + extra
    if args.prompt_file:
        # Ahead of the engine's own options: some of those take any number of values and would swallow it.
        extra = [Path(args.prompt_file).read_text(encoding='utf-8-sig').strip()] + extra
    if args.host != host():
        return remote(args.host, {'op': 'spawn', 'name': args.name, 'kind': args.kind,
                                  'cwd': args.cwd, 'args': extra, 'timeout': min(args.timeout, 5)})
    if any(r['name'] == args.name for r in sessions()):
        raise ValueError('name belongs to an existing session; resume it or choose another name')
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError('working directory does not exist: ' + str(cwd))
    engine = launcher(args.kind) if args.kind == 'claude' else [sys.executable, str(BIN / 'tandem-session'), 'codex']
    if not shutil.which(launcher(args.kind)[0]):
        raise ValueError(launcher(args.kind)[0] + ' is not on PATH (see launch.json in tandem doctor)')
    env = dict(os.environ, TANDEM_NAME=args.name, TANDEM_STATE=str(root()), TANDEM_CONFIG=str(config()))
    if os.name == 'nt':
        # No tmux: the engine gets a console window of its own, where its user can watch it and type.
        import tandem_win
        output = 'pid %d' % tandem_win.spawn_console(engine + extra, str(cwd), env).pid
        return ready(args, output)
    command = ['env', 'TANDEM_NAME=' + args.name, 'TANDEM_STATE=' + str(root()), 'TANDEM_CONFIG=' + str(config())] + engine + extra
    sock = os.environ.get('TMUX', '').split(',')[0]
    tmux = ['tmux'] + (['-S', sock] if sock else [])
    # Start a private detached session; no focus change or implicit mutation of
    # the user's current layout. Grouping/splitting can be added when requested.
    label = 'tandem-' + args.name + '-' + secrets.token_hex(2)
    output = subprocess.check_output(tmux + ['new-session', '-d', '-P', '-F', '#{pane_id}', '-s', label,
                                           '-c', str(cwd), shlex.join(command)], env=env, text=True).strip()
    return ready(args, output)


def ready(args, output):
    """Wait until the new session has reported in under its name; given a first task, until it has taken that up.
    A session is idle for a moment before it starts on its prompt, and `tandem wait --until idle` right after
    a spawn would otherwise return before the work began."""
    states = ('working', 'blocked', 'error') if args.prompt_file else ('idle', 'working', 'blocked', 'error')
    end = time.monotonic() + min(args.timeout, 60)
    while time.monotonic() < end:
        row = next((r for r in sessions() if r['name'] == args.name), None)
        if row:
            state = public(row)
            if state['state'] in states or row.get('turn_ok_at'):
                return dict(agent=state, ready=state['state'] in ('idle', 'working'))
        time.sleep(.1)
    return dict(name=args.name, ready=False, target=output, reason='startup is waiting; inspect tandem read after the hook registers')
