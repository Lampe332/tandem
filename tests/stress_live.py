"""Live check on Windows: does tandem do, with real agents, what the README and the intro video say it does?

Run it from inside a Claude Code session that tandem knows (`tandem me` works), because the questions are
asked as that session. It starts one Codex in a pseudo-terminal of its own, sandboxed to a work folder plus
tandem's state folder, puts it through the paces, starts one Claude helper with a first task, and prints a
table. It uses real model turns: a few minutes and a little quota.

    pip install pywinpty
    python tests/stress_live.py [--image] [--helper-only]

--image also asks Codex for a generated picture (about a minute; needs image generation in that Codex).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from winpty import PtyProcess

BIN = Path(__file__).resolve().parent.parent / 'bin'
sys.path.insert(0, str(BIN))
from tandem_core import root  # noqa: E402

# Under the home folder, not the system temp folder: Claude Code keeps no history for sessions started in temp,
# and a helper started there did not report back in testing.
WORK = Path(tempfile.mkdtemp(prefix='tandem-live-', dir=str(Path.home())))
RUN = '%04x' % (int(time.time()) & 0xffff)  # a name taken in an earlier run stays taken, so each run brings its own
CODEX, CLAUDE = 'live-codex-' + RUN, 'live-claude-' + RUN
results = []


def tandem(*args, timeout=120):
    done = subprocess.run([sys.executable, str(BIN / 'tandem')] + list(args), capture_output=True, timeout=timeout, cwd=str(WORK))
    return done.returncode, done.stdout.decode('utf-8', 'replace').strip(), done.stderr.decode('utf-8', 'replace').strip()


def state_of(name):
    code, out, _ = tandem('--all', '--json')
    try:
        return next((row['state'] for row in json.loads(out)['agents'] if row['name'] == name and row['state'] != 'gone'), None)
    except (ValueError, KeyError):
        return None


def wait_for(name, wanted, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if state_of(name) in wanted:
            return True
        time.sleep(1)
    return False


def check(label, ok, detail=''):
    results.append((label, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + label + ('  | ' + detail if detail else ''), flush=True)


def helper(me):
    """The other direction's building block: start a Claude with a first task and hear back from it."""
    task = WORK / 'task.txt'
    task.write_text('Write haiku.txt in this folder: a haiku about bridges, three lines. Then run in your shell: tandem say %s "HAIKU done"' % me, encoding='utf-8')
    settings = WORK / 'helper-settings.json'
    import tandem_setup
    hooks = {event: [{'hooks': [{'type': 'command', 'command': tandem_setup.hook_command('claude'), 'timeout': 3}]}] for event in tandem_setup.CLAUDE_EVENTS}
    settings.write_text(json.dumps({'hooks': hooks, 'permissions': {'allow': ['Bash(tandem:*)', 'Bash(tandem *)', 'PowerShell(tandem:*)', 'PowerShell(tandem *)', 'Read', 'Edit', 'Write']}}))
    code, out, err = tandem('spawn', CLAUDE, '--kind', 'claude', '--model', 'sonnet', '--cwd', str(WORK), '--prompt-file', str(task), '--timeout', '45',
                            '--', '--setting-sources', 'project,local', '--settings', str(settings))
    started = code == 0 and '"ready": true' in out
    finished = started and wait_for(CLAUDE, ('idle',), 120)
    said = any(json.loads(p.read_text(encoding='utf-8')).get('sender', {}).get('name') == CLAUDE for p in (root() / 'receipts').glob('*.json'))
    check('A Claude helper starts with a first task, does it, and reports back', finished and (WORK / 'haiku.txt').exists() and said,
          'started %s, state %s, haiku.txt %s, told us %s %s' % (started, state_of(CLAUDE), (WORK / 'haiku.txt').exists(), said, err[:80]))


def main():
    me = json.loads(tandem('--json', 'me')[1] or '{}').get('name')
    if not me:
        sys.exit('run this from a Claude Code session tandem knows: `tandem me` must work')
    print('asking as', me, '| work folder', WORK, flush=True)
    if '--helper-only' in sys.argv:
        try:
            helper(me)
        finally:
            cleanup(None)
        return report()
    env = dict(os.environ, TANDEM_NAME=CODEX)
    env['PATH'] = str(BIN.parent / 'win') + os.pathsep + env['PATH']
    # Sandboxed like most people's Codex: it may write in its work folder, and tandem adds its own mailbox to that.
    codex = PtyProcess.spawn([sys.executable, str(BIN / 'tandem'), 'run', 'codex', '-m', 'gpt-6-luna', '-c', 'model_reasoning_effort=medium',
                              '-c', 'sandbox_mode=workspace-write', '-c', 'approval_policy=never',
                              '-c', 'developer_instructions=Answer in English and keep it short.'],
                             cwd=str(WORK), env=env, dimensions=(30, 100))
    screen = []

    def drain():
        try:
            while True:
                screen.append(codex.read(8192))
        except (EOFError, OSError):
            pass

    threading.Thread(target=drain, daemon=True).start()
    try:
        up = wait_for(CODEX, ('idle',), 45)
        check('Codex started through tandem is reachable', up, '' if up else 'its terminal said: ' + ' '.join(''.join(screen).split())[-300:])
        if not up:
            return

        times = []
        for i in range(1, 4):
            begun = time.monotonic()
            code, out, err = tandem('ask', CODEX, 'Round trip %d. Run in your shell: tandem say %s "R%d ok"' % (i, me, i), '--timeout', '90')
            times.append(time.monotonic() - begun)
            if out != 'R%d ok' % i:
                break
        check('3 questions, 3 answers back', len(times) == 3 and out == 'R3 ok', 'avg %.1f s, last answer %r %s' % (sum(times) / len(times), out[:40], err[:80]))

        note = WORK / 'note.txt'
        note.write_text('First line with "quotes", 100%% & <angles>, umlauts äöü.\nSecond line.\nRun in your shell: tandem say %s "LAST-WORD <the last word of the first line>"' % me, encoding='utf-8')
        code, out, err = tandem('ask', CODEX, '--file', str(note), '--timeout', '90')
        check('A message from a file arrives intact', 'äöü' in out, repr(out[:60]))

        tandem('say', CODEX, 'Run in your shell: Start-Sleep -Seconds 20. While you wait you get three words to remember. Do not answer them one by one.')
        time.sleep(5)
        routes = [tandem('say', CODEX, 'Word %d: %s' % (n, word))[1] for n, word in enumerate(('apple', 'bridge', 'quartz'), 1)]
        wait_for(CODEX, ('idle',), 90)
        code, out, err = tandem('ask', CODEX, 'Run in your shell: tandem say %s "WORDS <the three words, comma separated>"' % me, '--timeout', '90')
        check('Messages sent while it works all arrive', all(w in out for w in ('apple', 'bridge', 'quartz')), '%s | routes: %s' % (out[:50], ', '.join(r.split(' · ')[1] if ' · ' in r else r[:20] for r in routes)))

        code, out, err = tandem('ask', CODEX, 'This time do not use tandem say. Just answer in your own chat with the word SIDEWAYS.', '--timeout', '90')
        check('An answer given without tandem say still comes back', 'SIDEWAYS' in out and 'without `tandem say`' in out, repr(out[:70]))

        code, out, err = tandem('read', CODEX, '--last', '1')
        check('tandem read shows its last answer', 'SIDEWAYS' in out, repr(out[:40]))

        if '--image' in sys.argv:
            code, out, err = tandem('ask', CODEX, 'Generate one image with your image tool: a paper boat on a puddle, photo. Save it as boat.png in your current folder. '
                                    'When the file is there, run in your shell: tandem say %s "IMAGE boat.png". If you cannot, run: tandem say %s "NOIMAGE <why>"' % (me, me),
                                    '--timeout', '300', timeout=330)
            check('Codex generates a picture and says where it is', (WORK / 'boat.png').exists(), repr(out[:90]))

        helper(me)
    finally:
        cleanup(codex)
    report()


def cleanup(codex):
    if '--keep' in sys.argv:
        return print('kept:', CLAUDE, WORK)
    for name in (CODEX, CLAUDE):
        for file in (root() / 'sessions').glob('*.json'):
            row = json.loads(file.read_text(encoding='utf-8'))
            if row.get('name') == name and row.get('process'):
                pid = row['process'].get('parent') if name == CODEX else row['process'].get('pid')
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
    if codex:
        subprocess.run(['taskkill', '/PID', str(codex.pid), '/T', '/F'], capture_output=True)
    shutil.rmtree(WORK, ignore_errors=True)


def report():
    passed = sum(ok for _, ok, _ in results)
    print()
    print('%d of %d passed' % (passed, len(results)))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == '__main__':
    main()
