"""Source-first OTA staging from a fixed official GitHub release channel.

There is no silent remote code execution. Updates are verified against the
release asset SHA-256, staged in user data, then *explicitly* installed on
Windows only after the Takumi desktop process is closed. No third-party engines
or model weights are downloaded by this updater.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import threading
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zipfile import ZipFile

REPO = 'coye2/Takumi'
API = 'https://api.github.com/repos/' + REPO + '/releases/latest'
ALLOWED_HOSTS = {'api.github.com', 'github.com', 'objects.githubusercontent.com',
                 'release-assets.githubusercontent.com', 'github-releases.githubusercontent.com'}
NAME = re.compile(r'^Takumi-v(\d+\.\d+\.\d+)-.*-Windows-Setup\.zip$')
VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')


def _version(value: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError('Invalid version string')
    return tuple(int(part) for part in match.groups())


def _url_allowed(raw: str) -> bool:
    url = urlsplit(raw)
    return url.scheme == 'https' and url.hostname in ALLOWED_HOSTS and not url.username and not url.password and url.port in (None, 443)


class VerifiedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_allowed(newurl):
            raise ValueError('Update redirect left the trusted GitHub domain set')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, max_bytes: int, opener=None) -> bytes:
    if not _url_allowed(url):
        raise ValueError('Update URL is not from the trusted GitHub release channel')
    op = opener or build_opener(VerifiedRedirects())
    with op.open(Request(url, headers={'User-Agent':'Takumi-OTA/1.4','Accept':'application/vnd.github+json'}), timeout=17) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('Update download exceeds the size limit')
    return data


def _asset(release: dict, current: str) -> dict:
    release_version = _version(str(release.get('tag_name','')))
    if release_version <= _version(current):
        raise ValueError('No newer release available')
    assets = [a for a in release.get('assets',[]) if isinstance(a,dict) and NAME.fullmatch(str(a.get('name','')))]
    assets = [a for a in assets if _version(NAME.fullmatch(a['name']).group(1)) == release_version]
    if len(assets) != 1:
        raise ValueError('Release does not contain exactly one matching Windows setup archive')
    a = assets[0]
    digest = str(a.get('digest') or '')
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('Release asset has no published SHA-256 digest; refusing unattended download')
    if not (isinstance(a.get('size'),int) and 1_000 < a['size'] <= 80_000_000):
        raise ValueError('Update archive has an unexpected size')
    if not _url_allowed(str(a.get('browser_download_url',''))):
        raise ValueError('Unexpected update archive host')
    return a


class UpdateManager:
    def __init__(self, base: Path, version: str, *, fetch=None):
        self.base = Path(base) / 'updates'
        self.base.mkdir(parents=True, exist_ok=True)
        self.version = version
        self.fetch = fetch or _get
        self.lock = threading.RLock()
        self._release = None
        self._staged = None

    def check(self) -> dict:
        try:
            release = json.loads(self.fetch(API, 1_000_000))
            if not isinstance(release,dict):
                raise ValueError('Invalid release response')
            try:
                a = _asset(release, self.version)
            except ValueError as exc:
                if str(exc)=='No newer release available':
                    return {'available':False,'version':self.version,'published':True}
                raise
            with self.lock:
                self._release = release
            return {'available':True,'version':str(release['tag_name']).removeprefix('v'),
                    'file':a['name'],'bytes':a['size'],
                    'notes':str(release.get('body',''))[:3000],
                    'channel':REPO,'verified_digest':True}
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            return {'available':False,'version':self.version,'published':False,
                    'reason':'Release feed unavailable or unverified: '+str(exc)[:240]}

    def prepare(self) -> dict:
        with self.lock:
            release = self._release
        if release is None:
            status = self.check()
            if not status.get('available'):
                raise ValueError(status.get('reason') or 'No verified update available')
            release = self._release
        a = _asset(release, self.version)
        version = NAME.fullmatch(a['name']).group(1)
        expected = a['digest'].split(':',1)[1].lower()
        folder = self.base / ('v'+version)
        archive = folder / a['name']
        extracted = folder / 'staged'
        folder.mkdir(parents=True,exist_ok=True)
        if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
            raw = self.fetch(a['browser_download_url'], 80_000_000)
            if len(raw)!=a['size'] or hashlib.sha256(raw).hexdigest()!=expected:
                raise ValueError('Downloaded update does not match published length and SHA-256')
            temp = archive.with_suffix('.part')
            temp.write_bytes(raw)
            os.replace(temp,archive)
        # Validate the entire archive before extracting; never follow symlinks,
        # absolute paths, traversal, or archives containing extra roots.
        with ZipFile(archive) as z:
            infos = z.infolist()
            if not 1 <= len(infos) <= 6000 or sum(x.file_size for x in infos) > 350_000_000:
                raise ValueError('Update archive exceeds safe extraction limits')
            for info in infos:
                name=info.filename
                part=PurePosixPath(name)
                if (part.is_absolute() or '..' in part.parts or not part.parts or
                    part.parts[0] != 'Takumi' or '\\' in name or ':' in name or
                    ((info.external_attr >> 16) & 0o170000) == 0o120000):
                    raise ValueError('Unsafe path in update archive')
            if z.testzip() is not None:
                raise ValueError('Update archive CRC validation failed')
            staging_tmp=folder/'unpacking'
            if staging_tmp.exists():
                shutil.rmtree(staging_tmp)
            staging_tmp.mkdir()
            try:
                z.extractall(staging_tmp)
                staged_root=staging_tmp/'Takumi'
                for must in ('scripts/Build-Windows-EXE.ps1','desktop/Takumi.Desktop/Takumi.Desktop.csproj','takumi/core.py'):
                    if not (staged_root / must).is_file():
                        raise ValueError('Update package missing required source: '+must)
                if extracted.exists():
                    shutil.rmtree(extracted)
                os.replace(staged_root,extracted)
            finally:
                shutil.rmtree(staging_tmp,ignore_errors=True)
        manifest={'version':version,'sha256':expected,'archive':str(archive),'staged':str(extracted),
                  'created_utc':datetime.now(timezone.utc).isoformat(),'source':REPO}
        (folder/'verified.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        with self.lock:
            self._staged=manifest
        return {'prepared':True,'version':version,'requires_restart':True,
                'message':'Verified update staged. Close Takumi after choosing Install to apply it.'}

    def install_on_close(self, install_root: Path, launcher=None) -> dict:
        if os.name != 'nt' and launcher is None:
            raise ValueError('Automatic native installation is available only on Windows')
        with self.lock:
            manifest=self._staged
        if not manifest:
            raise ValueError('Download and verify an update before installing it')
        folder=self.base/('v'+manifest['version'])
        if json.loads((folder/'verified.json').read_text(encoding='utf-8'))!=manifest:
            raise ValueError('Update manifest changed; download again')
        if hashlib.sha256(Path(manifest['archive']).read_bytes()).hexdigest()!=manifest['sha256']:
            raise ValueError('Downloaded update no longer matches published SHA-256')
        script=Path(install_root)/'scripts'/'Apply-Takumi-Update.ps1'
        if not script.is_file():
            raise ValueError('This Takumi build is missing its external update helper')
        args=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
              '-StagedDirectory',manifest['staged'],'-InstalledDirectory',str(install_root),
              '-ParentPid',str(os.getpid()),'-Version',manifest['version']]
        if launcher is None:
            launcher=lambda cmd: subprocess.Popen(cmd,stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0),close_fds=True)
        launcher(args)
        return {'scheduled':True,'version':manifest['version'],
                'message':'Updater launched. Close Takumi to allow the native installation to be replaced.'}
