"""Mailbox stress on Windows, no model involved: many senders and many hook calls hit one state folder at once.

Checks the parts the port replaced: file locks, atomic replace, unlink, the detached worker. Every message must be
delivered exactly once, nothing lost, nothing doubled, no hook error.

usage: python stress_mailbox.py BIN STATE_DIR [senders] [messages each]"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time

BIN, STATE = Path(sys.argv[1]), Path(sys.argv[2])
SENDERS = int(sys.argv[3]) if len(sys.argv) > 3 else 8
EACH = int(sys.argv[4]) if len(sys.argv) > 4 else 10
env = dict(os.environ, TANDEM_STATE=str(STATE / 'state'), TANDEM_CONFIG=str(STATE / 'config'),
           CLAUDE_CONFIG_DIR=str(STATE / 'claude'), CODEX_HOME=str(STATE / 'codex'), PYTHONIOENCODING='utf-8')
PY = sys.executable


def hook(event, sid='stress-a'):
    data = json.dumps({'hook_event_name': event, 'session_id': sid, 'cwd': str(STATE)})
    return subprocess.run([PY, str(BIN / 'tandem-hook'), '--kind', 'claude'], input=data, capture_output=True,
                          text=True, encoding='utf-8', env=env).stdout


def tandem(*args):
    return subprocess.run([PY, str(BIN / 'tandem')] + list(args), capture_output=True, text=True, encoding='utf-8', env=env)


def sender(n):
    out = []
    for i in range(EACH):
        result = tandem('say', 'stress', 'msg-%02d-%02d äöü ✓' % (n, i))
        out.append((result.returncode, result.stdout.strip(), result.stderr.strip()))
    return out


hook('SessionStart')
hook('UserPromptSubmit')  # working: mail waits for the hook route
print('me:', json.loads(tandem('--json', 'me').stdout)['name'] if tandem('name', 'stress').returncode == 0 else 'rename failed')

started = time.monotonic()
got, stop = [], False


def drain():
    """The recipient's tool calls: each PostToolUse hook may hand over up to 8 messages."""
    while not stop:
        text = hook('PostToolUse')
        if text.strip():
            got.extend(line for line in json.loads(text)['hookSpecificOutput']['additionalContext'].split('\n')
                       if line.startswith('[from '))


with concurrent.futures.ThreadPoolExecutor(SENDERS + 4) as pool:
    drains = [pool.submit(drain) for _ in range(4)]
    results = [r for f in [pool.submit(sender, n) for n in range(SENDERS)] for r in f.result()]
    deadline = time.monotonic() + 30
    while len(got) < SENDERS * EACH and time.monotonic() < deadline:
        time.sleep(.2)
    stop = True
    for d in drains:
        d.result()
elapsed = time.monotonic() - started

total = SENDERS * EACH
failed = [r for r in results if r[0]]
bodies = [line.split('] ', 1)[1] for line in got]
receipts = [json.loads(p.read_text(encoding='utf-8')) for p in (STATE / 'state' / 'receipts').glob('*.json')]
stages = {}
for rec in receipts:
    stages[rec.get('stage')] = stages.get(rec.get('stage'), 0) + 1
log = STATE / 'state' / 'hook-errors.log'
print('sent: %d  send failures: %d' % (total, len(failed)))
for r in failed[:3]:
    print('  failure:', r[2][:160])
print('delivered through hooks: %d  unique: %d  doubled: %d' % (len(bodies), len(set(bodies)), len(bodies) - len(set(bodies))))
print('missing: %d' % len({'msg-%02d-%02d äöü ✓' % (n, i) for n in range(SENDERS) for i in range(EACH)} - set(bodies)))
print('receipt stages:', stages)
print('mail files left:', len(list((STATE / 'state' / 'mail').glob('*/*.json'))))
print('hook errors:', log.read_text().strip().count('\n') + 1 if log.exists() else 0)
print('time: %.1fs' % elapsed)
ok = not failed and len(bodies) == len(set(bodies)) == total and not log.exists()
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
