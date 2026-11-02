"""Bounded read-only source inspections for accessible GitHub repositories.

This is source *research*, not permission to paste upstream code into unrelated
licensed products. Only project/repo identifiers and user-entered paths are sent
out; private workspace contents never enter requests.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from .research import _fetch

REPO = re.compile(r'^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$')
EXTENSIONS = ('.c','.cc','.cpp','.h','.hpp','.cs','.rs','.go','.py','.js','.ts','.glsl','.hlsl','.wgsl','.md','.txt','.json','.xml','.asm','.s')
MAX_BYTES = 4_000_000


def normalize_repo(value: str) -> str:
    value = str(value).strip().rstrip('/')
    if value.startswith('https://github.com/'):
        value = value[len('https://github.com/'):]
    if value.endswith('.git'):
        value = value[:-4]
    if not REPO.fullmatch(value) or '..' in value:
        raise ValueError('Enter a GitHub repository as owner/name, not an arbitrary URL.')
    return value


def normalize_path(value: str) -> str:
    value = str(value).strip().replace('\\','/')
    if len(value)>320 or not value or value.startswith('/') or ':' in value or '\x00' in value:
        raise ValueError('Choose a repository-relative source file')
    parts=value.split('/')
    if any(p in ('','.','..') for p in parts):
        raise ValueError('Unsafe source path')
    if not value.lower().endswith(EXTENSIONS):
        raise ValueError('Only text source files can be inspected')
    return '/'.join(parts)


def _api(path: str, *, fetch=None) -> dict:
    if not path.startswith('/repos/') or len(path)>700:
        raise ValueError('Unsupported GitHub request')
    if fetch is None:
        from .github_reader import GitHubReader
        reader = GitHubReader(budget=8)
        raw = reader.fetch('https://api.github.com'+path) if reader.executable else _fetch('https://api.github.com'+path, timeout=6)
    else:
        raw=fetch('https://api.github.com'+path)
    if len(raw)>MAX_BYTES:
        raise ValueError('GitHub reference exceeded the safe response limit')
    result=json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('Unexpected GitHub response')
    if result.get('message'):
        raise ValueError('GitHub reference unavailable: '+str(result['message'])[:100])
    return result


def map_repository(repo: str, query: str = '', *, fetch=None, ref: str | None = None) -> dict:
    repo=normalize_repo(repo)
    meta=_api('/repos/'+repo,fetch=fetch)
    branch=ref or str(meta.get('default_branch','main'))
    if not re.fullmatch(r'[\w./-]{1,120}',branch) or '..' in branch:
        raise ValueError('Unsafe upstream branch metadata')
    commit = _revision(repo, branch, fetch=fetch)
    tree=_api('/repos/'+repo+'/git/trees/'+commit+'?recursive=1',fetch=fetch)
    rows=tree.get('tree',[])
    if not isinstance(rows,list):
        raise ValueError('Repository tree is unavailable')
    query_tokens=[t.lower() for t in re.findall(r'[A-Za-z][A-Za-z0-9_]{2,}',str(query))][:12]
    candidates=[]
    for row in rows[:25000]:
        if not isinstance(row,dict) or row.get('type')!='blob':
            continue
        name=row.get('path','')
        if not isinstance(name,str) or not name.lower().endswith(EXTENSIONS) or any(x in name.lower().split('/') for x in ('node_modules','vendor','.git','obj','bin')):
            continue
        if row.get('size',0) > 100_000:
            continue
        low=name.lower();score=sum(4 if t in low.split('/')[-1] else 1 for t in query_tokens if t in low)
        candidates.append((score,len(name),name))
    candidates.sort(key=lambda item:(-item[0],item[1],item[2]))
    # Long implementation paths must not disappear behind shorter documentation.
    implementation = [row for row in candidates if not row[2].lower().endswith(('.md','.txt','.json','.xml'))]
    selected = candidates[:18]
    for row in implementation[:6] + candidates[18:24]:
        if row not in selected: selected.append(row)
        if len(selected) >= 24: break
    license_record=meta.get('license') or {}
    license_label=license_record.get('spdx_id','UNKNOWN') if isinstance(license_record,dict) else 'UNKNOWN'
    result={
      'repo':repo,'branch':branch,'commit':commit,'private':bool(meta.get('private')),'license':license_label,'license_unverified':license_label in ('UNKNOWN','NOASSERTION'),
      'url':meta.get('html_url','https://github.com/'+repo),'description':str(meta.get('description') or '')[:260],
      'tree_truncated':bool(tree.get('truncated')) or len(rows)>25000,
      'files':[{'path':name,'commit':commit,'url':f'https://github.com/{repo}/blob/{commit}/{quote(name,safe="/")}',
                'score':score} for score,_,name in selected],
      'notice':'Read-only source discovery. Authentication uses the existing local GitHub login when available. Source presence does not establish runtime behavior or permission to copy code.'
    }
    return result


def _revision(repo: str, ref: str, *, fetch=None) -> str:
    if re.fullmatch(r'[0-9a-fA-F]{40}', ref): return ref.lower()
    resolved = _api('/repos/' + repo + '/commits/' + quote(ref, safe=''), fetch=fetch)
    sha = resolved.get('sha', '')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
        raise ValueError('GitHub did not return an immutable commit identity')
    return sha.lower()


def inspect_file(repo: str, path: str, ref: str | None = None, *, fetch=None) -> dict:
    repo=normalize_repo(repo); path=normalize_path(path)
    branch=ref or 'HEAD'
    if not re.fullmatch(r'[\w./-]{1,120}',branch) or '..' in branch:
        raise ValueError('Unsafe source revision')
    commit = _revision(repo, branch, fetch=fetch)
    data=_api('/repos/'+repo+'/contents/'+quote(path,safe='/')+'?ref='+commit,fetch=fetch)
    if data.get('type')!='file' or data.get('encoding')!='base64' or data.get('size',0)>48000:
        raise ValueError('Reference must be a small source file (48 KB maximum)')
    raw=base64.b64decode(str(data.get('content','')).replace('\n',''),validate=True)
    if b'\x00' in raw or len(raw)>48000:
        raise ValueError('Reference contains binary data or exceeds the size limit')
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if data.get('sha') != blob: raise ValueError('Reference bytes do not match the declared Git blob')
    lines=raw.decode('utf-8','replace').splitlines()
    excerpt='\n'.join(f'{idx:4}: {line[:280]}' for idx,line in enumerate(lines[:110],1))[:14000]
    return {
      'repo':repo,'path':path,'ref':branch,'commit':commit,'git_blob_sha':blob,'sha256':hashlib.sha256(raw).hexdigest(),
      'url':f'https://github.com/{repo}/blob/{commit}/{quote(path,safe="/")}',
      'total_lines':len(lines),'excerpt':excerpt,'truncated':len(lines)>110 or len(excerpt)>=14000,
      'notice':'Source excerpt is an untrusted reference, not evidence of compatibility or permission to copy implementation.'
    }
