"""Read-only GitHub access through the user's existing gh login.

No credential is copied, no shell is used, and repository contents never go to
public search. Authentication failure is evidence, not a claim no tool exists.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse


class GitHubReader:
    def __init__(self, cancel=None, budget=40):
        self.executable = shutil.which('gh')
        self.cancel = cancel
        self.deadline = time.monotonic() + budget

    def get(self, path):
        if not self.executable:
            raise ValueError('GitHub CLI is not installed. Connect GitHub on this PC.')
        if (not isinstance(path, str) or len(path) > 900 or not path.startswith(('/repos/', '/user'))
                or any(c in path for c in ('\r', '\n', '\x00', '#')) or '..' in path):
            raise ValueError('Unsupported GitHub read path')
        if self.cancel is not None and self.cancel.is_set():
            raise ValueError('GitHub read cancelled')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise ValueError('GitHub review time budget reached; inspect the partial evidence')
        env = {**os.environ, 'GH_HOST': 'github.com', 'GH_PROMPT_DISABLED': '1'}
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
            proc = subprocess.Popen([self.executable, 'api', '--hostname', 'github.com', '--method', 'GET', path],
                stdin=subprocess.DEVNULL, stdout=output, stderr=error, env=env,
                creationflags=0x08000000 if os.name == 'nt' else 0)
            deadline = min(self.deadline, time.monotonic() + 8)
            while proc.poll() is None:
                if (time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > 4_000_000
                        or (self.cancel is not None and self.cancel.is_set())):
                    proc.kill(); proc.wait()
                    raise ValueError('GitHub read cancelled, timed out or exceeded its response limit')
                time.sleep(.04)
            if proc.returncode:
                error.seek(0); detail = error.read(2000).decode('utf-8', 'replace')
                status = next((code for code in ('401','403','404','429') if code in detail), 'unavailable')
                raise ValueError('GitHub read ' + status + '. Check this PC’s gh login, repository access and rate limit.')
            output.seek(0); raw = output.read(4_000_001)
        if len(raw) > 4_000_000: raise ValueError('GitHub response exceeds the 4 MB limit')
        return json.loads(raw)

    def fetch(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'api.github.com':
            raise ValueError('GitHub reader only accesses api.github.com')
        return json.dumps(self.get(parsed.path + ('?' + parsed.query if parsed.query else ''))).encode('utf-8')


def requested_repository(prompt, history=()):
    """Distinguish an owner's repository request from general public research."""
    text = str(prompt)
    direct = bool(re.search(r'https://github\.com/[\w.-]+/[\w.-]+', text, re.I))
    repo_words = bool(re.search(r'\b(?:repo(?:sitory|sitories)?|github)\b', text, re.I))
    owned = bool(re.search(r'\b(?:my|our)\b', text, re.I))
    named = bool(re.search(r'\b[\w.-]+/[\w.-]+\b', text)) and 'github' in text.lower()
    if direct or repo_words and (owned or named): return text
    capability = bool(re.search(r'\bgithub\b', text, re.I) and re.search(r'\b(?:access|connect(?:ed|ion)?|tools?|login|available)\b', text, re.I))
    if capability:
        for message in reversed(list(history)[-12:]):
            if message.get('role') == 'user' and message.get('content') != text:
                previous = requested_repository(message.get('content', ''))
                if previous and not re.search(r'\b(?:access|login)\b', previous, re.I): return previous
        return text
    return None
