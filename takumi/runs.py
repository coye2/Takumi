"""Durable run journal. Replaying this data never invokes a provider or a tool."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

TERMINAL = frozenset(('completed', 'failed', 'stopped', 'blocked', 'outcome_unknown'))
EVENT_TYPES = {
    'start': 'run.started', 'delta': 'answer.delta', 'activity': 'item.updated',
    'reasoning_activity': 'activity.updated', 'reasoning': 'activity.updated', 'progress': 'activity.updated',
    'metrics': 'usage.updated', 'code_validation': 'test.result',
    'stop_requested': 'stop.acknowledged', 'input_queued': 'input.queued',
    'input_accepted': 'input.accepted', 'recovery': 'recovery.required',
}


@contextmanager
def data_directory_lease(base: Path):
    """One backend may recover/write a data directory, across source versions.

    The OS releases this lock on process death; no stale PID files to guess at.
    Keep the file in place so contenders always lock the same filesystem object.
    """
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with (base / 'backend.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another Takumi backend owns this data directory. Close that app before switching builds.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class RunJournal:
    def __init__(self, base: Path):
        self.path = Path(base) / 'runs.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    request TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, seq INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL,
                    PRIMARY KEY(run_id, seq));
                CREATE INDEX IF NOT EXISTS session_runs ON runs(session_id, created);
                CREATE TABLE IF NOT EXISTS inputs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    text TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        # Connections are short lived so native Windows cleanup can release the file.
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def fingerprint(request):
        return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def find_request(self, request_id, request):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE request_id=?', (request_id,)).fetchone()
        if row and row['fingerprint'] != self.fingerprint(request):
            raise ValueError('This request ID belongs to different input. Reconcile the existing run first.')
        return dict(row) if row else None

    def create(self, session_id, request_id, request):
        with self.lock:
            found = self.find_request(request_id, request)
            if found:
                return found, False
            rid, now = uuid.uuid4().hex, time.time()
            with self.connect() as db:
                db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',
                           (rid, session_id, request_id, self.fingerprint(request),
                            json.dumps(request, ensure_ascii=False), 'starting', now, now, 0))
            self.append(rid, {'type': 'accepted'}, event_type='run.accepted')
            return self.get(rid), True

    def get(self, run_id):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result.pop('fingerprint')
        result['request'] = json.loads(result['request'])
        return result

    def for_session(self, session_id):
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT id, session_id, state, seq, created, updated FROM runs '
                              'WHERE session_id=? ORDER BY created DESC LIMIT 40', (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def append(self, run_id, payload, *, state=None, event_type=None):
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode('utf-8')) > 2_000_000:
            raise ValueError('Run event exceeds the 2 MB journal limit.')
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise ValueError('Run not found')
            if row['state'] in TERMINAL:
                raise ValueError('Cannot append to a finished run')
            seq, now = row['seq'] + 1, time.time()
            kind = event_type or EVENT_TYPES.get(payload['type'], 'activity.updated')
            event = dict(schema_version=1, session_id=row['session_id'], run_id=run_id,
                         item_id=payload.get('item_id'), event_id=f'{run_id}:{seq}', seq=seq,
                         timestamp=now, type=kind, payload=payload)
            db.execute('INSERT INTO events VALUES (?,?,?)',
                       (run_id, seq, json.dumps(event, ensure_ascii=False)))
            db.execute('UPDATE runs SET seq=?, updated=?, state=? WHERE id=?',
                       (seq, now, state or row['state'], run_id))
        return event

    def events(self, run_id, after=0, limit=256):
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT event FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?',
                              (run_id, after, limit)).fetchall()
        return [json.loads(row['event']) for row in rows]

    def activity(self, run_id):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT event FROM events WHERE run_id=? AND "
                "json_extract(event, '$.payload.type') NOT IN ('delta','reasoning_activity','reasoning','done') "
                "ORDER BY seq DESC LIMIT 80", (run_id,)).fetchall()
        return [json.loads(row['event']) for row in reversed(rows)]

    def queue_input(self, session_id, run_id, text):
        with self.lock, self.connect() as db:
            count = db.execute("SELECT count(*) FROM inputs WHERE session_id=? AND state='queued'", (session_id,)).fetchone()[0]
            if count >= 8:
                raise ValueError('Too many pending instructions. Wait for the next step.')
            item = dict(id=uuid.uuid4().hex, session_id=session_id, run_id=run_id,
                        text=text, state='queued', created=time.time())
            db.execute('INSERT INTO inputs VALUES (?,?,?,?,?,?)', tuple(item.values()))
        return item

    def inputs(self, session_id):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT * FROM inputs WHERE session_id=? ORDER BY created DESC LIMIT 80", (session_id,)).fetchall()
        return [dict(row) for row in rows]

    def consume_inputs(self, session_id):
        with self.lock, self.connect() as db:
            rows = db.execute("SELECT * FROM inputs WHERE session_id=? AND state='queued' ORDER BY created", (session_id,)).fetchall()
            db.execute("UPDATE inputs SET state='accepted' WHERE session_id=? AND state='queued'", (session_id,))
        return [dict(row) for row in rows]

    def edit_input(self, session_id, input_id, text=None):
        with self.lock, self.connect() as db:
            if text is None:
                change = db.execute("UPDATE inputs SET state='cancelled' WHERE id=? AND session_id=? AND state='queued'", (input_id, session_id))
            else:
                change = db.execute("UPDATE inputs SET text=? WHERE id=? AND session_id=? AND state='queued'", (text, input_id, session_id))
            if not change.rowcount:
                raise ValueError('This instruction is no longer queued.')

    def recover(self):
        """A backend restart cannot establish whether its last side effect executed."""
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT id, state FROM runs').fetchall()
        for row in rows:
            if row['state'] not in TERMINAL:
                self.append(row['id'], {'type': 'recovery', 'message':
                    'The backend restarted. The previous outcome is unknown; inspect saved activity '
                    'and project changes before starting more work. No action was replayed.'},
                    state='outcome_unknown')

    def delete_session(self, session_id):
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE session_id=?)', (session_id,))
            db.execute('DELETE FROM runs WHERE session_id=?', (session_id,))
            db.execute('DELETE FROM inputs WHERE session_id=?', (session_id,))
