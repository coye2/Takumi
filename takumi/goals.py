"""Bounded goal checkpoints. Waiting/restarts consume the original wall-time budget."""
import json
import sqlite3
import time
import uuid
from contextlib import closing


class Goals:
    def __init__(self, base, clock=time.time):
        self.path = base / 'goals.sqlite3'
        self.clock = clock
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, session_id TEXT, data TEXT)')

    def save(self, goal):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO goals VALUES (?,?,?)',
                       (goal['id'], goal['session_id'], json.dumps(goal, ensure_ascii=False)))
        return goal

    def create(self, session_id, project_id, prompt, seconds=43200, *, deadline=None):
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 1 <= seconds <= 43200:
            raise ValueError('Goal duration must be between 1 second and 12 hours.')
        now = self.clock()
        if deadline is not None and (not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not now < deadline <= now + 43200):
            raise ValueError('The original goal deadline has expired or is outside the twelve-hour limit.')
        return self.save(dict(id=uuid.uuid4().hex, session_id=session_id, project_id=project_id,
            prompt=prompt, created=now, deadline=min(now + seconds, deadline) if deadline is not None else now + seconds, state='running', run_id=None,
            checkpoints=[], budget_policy='Wall clock including waiting and backend downtime'))

    def get(self, goal_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT data FROM goals WHERE id=?', (goal_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def for_session(self, session_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute('SELECT data FROM goals WHERE session_id=?', (session_id,)).fetchall()
        return sorted([json.loads(row[0]) for row in rows], key=lambda x: x['created'], reverse=True)

    def remaining(self, goal):
        return max(0, goal['deadline'] - self.clock())

    def resume(self, goal_id, session_id, project_id):
        goal = self.get(goal_id)
        if not goal or goal['session_id'] != session_id or goal['project_id'] != project_id:
            raise ValueError('Goal does not belong to this conversation and project.')
        if goal['state'] == 'completed':
            raise ValueError('This goal is already marked complete.')
        if not self.remaining(goal):
            goal['state'] = 'budget_exhausted'
            self.save(goal)
            raise ValueError('The original goal deadline has expired. Start a new explicitly scoped goal to continue.')
        goal['state'] = 'running'
        return self.save(goal)

    def checkpoint(self, goal, *, state, run_id, native_session=None, report=''):
        goal.update(state=state, run_id=run_id)
        goal['checkpoints'].append(dict(at=self.clock(), state=state, run_id=run_id,
            native_session=native_session, report=report[-4000:]))
        goal['checkpoints'] = goal['checkpoints'][-100:]
        return self.save(goal)

    def delete_session(self, session_id):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM goals WHERE session_id=?', (session_id,))
