"""Versioned binary layout hypotheses, persisted per registered project.

Explicit offsets are never guessed. Parsing observed bytes does not establish
the meaning of a reverse-engineered field or validate a format hypothesis.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import struct
import time

from .plugins import in_project

TYPES = {'u8': ('B', 1), 'i8': ('b', 1), 'u16': ('H', 2), 'i16': ('h', 2),
         'u32': ('I', 4), 'i32': ('i', 4), 'u64': ('Q', 8), 'i64': ('q', 8),
         'f32': ('f', 4), 'f64': ('d', 8), 'bytes': ('', 1), 'padding': ('', 1)}
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')


def integer(value, label, low=0, high=8_000_000):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}')
    return value


def validate_schema(spec: dict) -> dict:
    if not isinstance(spec, dict) or len(json.dumps(spec)) > 64000:
        raise ValueError('Use a layout object under 64 KB')
    spec = json.loads(json.dumps(spec))
    if not NAME.fullmatch(str(spec.get('name', ''))): raise ValueError('Invalid format name')
    integer(spec.get('revision'), 'Schema revision', 1, 1_000_000)
    if spec.get('endian') not in ('little', 'big'): raise ValueError('Specify little or big endian')
    fields = spec.get('fields')
    if not isinstance(fields, list) or not 1 <= len(fields) <= 256: raise ValueError('Use 1–256 fields')
    if 'size' in spec: integer(spec['size'], 'Record size', 1)
    selector = spec.get('version')
    if selector is not None:
        if not isinstance(selector, dict) or selector.get('type') not in ('u8', 'u16', 'u32'):
            raise ValueError('Version selector needs an unsigned integer type')
        integer(selector.get('offset'), 'Version offset')
    seen = set()
    for field in fields:
        if not isinstance(field, dict) or not NAME.fullmatch(str(field.get('name', ''))):
            raise ValueError('Each field needs a valid name')
        if field['name'] in seen: raise ValueError('Duplicate field name')
        if field.get('type') not in TYPES: raise ValueError('Unsupported field type')
        integer(field.get('offset'), 'Field offset')
        alignment = integer(field.get('align', 1), 'Field alignment', 1, 256)
        if alignment & (alignment - 1) or field['offset'] % alignment:
            raise ValueError('Alignment must be a power of two and divide the field offset')
        count = field.get('count', 1)
        if isinstance(count, str):
            if count not in seen: raise ValueError('A count must reference an earlier field')
        else: integer(count, 'Field count', 0, 65536)
        if 'fill' in field: integer(field['fill'], 'Padding byte', 0, 255)
        gate = field.get('versions')
        if gate is not None:
            if selector is None or not isinstance(gate, dict): raise ValueError('Version gates need a version selector')
            lo = integer(gate.get('min', 0), 'Minimum version', 0, 2**32 - 1)
            hi = integer(gate.get('max', 2**32 - 1), 'Maximum version', 0, 2**32 - 1)
            if lo > hi: raise ValueError('Version range is reversed')
        seen.add(field['name'])
    # Enumerate every region where gate activity can change; reject overlapping
    # fixed fields even if the first sample happens to exercise another version.
    versions = {0}
    for f in fields:
        gate = f.get('versions', {})
        versions.add(gate.get('min', 0)); versions.add(gate.get('max', 2**32 - 1))
    for version in versions:
        occupied = []
        for f in fields:
            gate = f.get('versions', {})
            if not gate.get('min', 0) <= version <= gate.get('max', 2**32 - 1): continue
            if type(f.get('count', 1)) is not int: continue
            end = f['offset'] + TYPES[f['type']][1] * f.get('count', 1)
            if 'size' in spec and end > spec['size']: raise ValueError('Field exceeds record size')
            if end == f['offset']: continue
            if any(f['offset'] < b and end > a for a, b in occupied): raise ValueError('Active fields overlap')
            occupied.append((f['offset'], end))
    return spec


def parse_layout(spec: dict, raw: bytes) -> dict:
    spec = validate_schema(spec)
    if len(raw) > 8_000_000: raise ValueError('Binary inspection is limited to 8 MB')
    if 'size' in spec and len(raw) < spec['size']: raise ValueError('Truncated record')
    prefix = '<' if spec['endian'] == 'little' else '>'
    version = None
    if spec.get('version'):
        selector = spec['version']; fmt, width = TYPES[selector['type']]
        if selector['offset'] + width > len(raw): raise ValueError('Truncated version selector')
        version = struct.unpack_from(prefix + fmt, raw, selector['offset'])[0]
    output, values, occupied = [], {}, []
    for field in spec['fields']:
        gate = field.get('versions')
        if gate and not gate.get('min', 0) <= version <= gate.get('max', 2**32 - 1): continue
        count = field.get('count', 1)
        if isinstance(count, str):
            count = values.get(count)
        count = integer(count, 'Resolved field count', 0, 65536)
        fmt, width = TYPES[field['type']]
        start = field['offset']; end = start + count * width
        if end > len(raw) or ('size' in spec and end > spec['size']): raise ValueError('Truncated or oversized field: ' + field['name'])
        if end > start and any(start < b and end > a for a, b in occupied): raise ValueError('Resolved fields overlap')
        if end > start: occupied.append((start, end))
        if field['type'] == 'padding':
            fill = field.get('fill')
            valid = fill is None or all(x == fill for x in raw[start:end])
            if not valid: raise ValueError('Padding rule failed: ' + field['name'])
            value = {'bytes': count, 'fill_checked': fill}
        elif field['type'] == 'bytes': value = raw[start:end].hex()
        else:
            unpacked = struct.unpack_from(prefix + str(count) + fmt, raw, start)
            value = unpacked[0] if count == 1 else list(unpacked)
            # JSON transport must not silently emit non-standard NaN/Infinity.
            import math
            if any(isinstance(v, float) and not math.isfinite(v) for v in unpacked):
                value = [str(v) if isinstance(v, float) and not math.isfinite(v) else v for v in unpacked]
                if count == 1: value = value[0]
        values[field['name']] = value
        output.append({'name': field['name'], 'offset': start, 'size': end - start, 'type': field['type'], 'value': value})
    return {'format': spec['name'], 'revision': spec['revision'], 'binary_version': version,
            'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'fields': output,
            'scope': 'Values decoded using the saved layout hypothesis; field meanings and format correctness remain unverified.'}


class FormatMemory:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS formats(root TEXT,name TEXT,revision INTEGER,spec TEXT,created REAL,PRIMARY KEY(root,name,revision))')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db: yield db
        finally:
            db.close()

    def save(self, project: Path, spec: dict) -> dict:
        spec = validate_schema(spec)
        with self._db() as db:
            try:
                db.execute('INSERT INTO formats VALUES(?,?,?,?,?)', (str(project.resolve()), spec['name'], spec['revision'], json.dumps(spec), time.time()))
            except sqlite3.IntegrityError:
                raise ValueError('This schema revision already exists; save a new revision to preserve history') from None
        return spec

    def list(self, project: Path) -> list[dict]:
        with self._db() as db:
            rows = db.execute('SELECT spec FROM formats WHERE root=? ORDER BY name,revision DESC', (str(project.resolve()),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, project: Path, name: str, revision: int | None = None) -> dict:
        specs = [s for s in self.list(project) if s['name'] == name and (revision is None or s['revision'] == revision)]
        if not specs: raise ValueError('No saved layout with that name/revision in this project')
        return specs[0]

    def inspect(self, project: Path, path: str, name: str, revision: int | None = None) -> dict:
        target = in_project(project, path)
        if not target.is_file() or target.stat().st_size > 8_000_000: raise ValueError('Choose a project binary under 8 MB')
        return parse_layout(self.get(project, name, revision), target.read_bytes())

    def context(self, project: Path, prompt: str) -> str:
        selected = {}
        for spec in self.list(project):
            if spec['name'] not in selected and re.search(r'(?<!\w)' + re.escape(spec['name']) + r'(?!\w)', prompt, re.I):
                selected[spec['name']] = spec
        bounded = []
        for spec in selected.values():
            if len(json.dumps(bounded + [spec])) <= 4300:
                bounded.append(spec)
        return ('SAVED FORMAT HYPOTHESES (project-scoped; preserve exact offsets/revision; not proven facts; oversized layouts omitted):\n' + json.dumps(bounded)) if bounded else ''
