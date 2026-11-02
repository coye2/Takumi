"""Deterministic Takumi source and Windows setup ZIPs; no third-party binaries.

The only allowed executable in the Windows setup ZIP is Takumi-Setup.exe,
which we build from installer/main.go and whose sole purpose is to compile
Takumi's own native WebView2 frontend on the user's Windows machine.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.5.0'
FORBIDDEN = {'.dll','.so','.dylib','.pyd','.whl','.bin','.gguf','.safetensors','.onnx','.zip','.exe'}
EXCLUDED = {'__pycache__','.git','.pytest_cache','node_modules','upstream-src',
            'third_party_src','.venv','dist','build','.vs','obj','bin'}


def selected(mode='source'):
    files=[]
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file():
            continue
        relative=p.relative_to(ROOT)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        if p.name.endswith(('.log','.pyc','.pyo','.tmp')) or p.name.startswith('.env'):
            continue
        if p.suffix.lower() in FORBIDDEN:
            if relative.as_posix()=='Takumi-Setup.exe':
                if mode=='windows-setup':
                    files.append(p)
                continue
            raise ValueError(f'Refusing to package an unexpected binary: {relative}')
        files.append(p)
    if mode=='windows-setup' and not any(p.name=='Takumi-Setup.exe' for p in files):
        raise ValueError('Compile Takumi-Setup.exe from installer/main.go first')
    return files


def package(destination: Path, mode='source'):
    files=selected(mode)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in files:
            item=zipfile.ZipInfo('Takumi/'+p.relative_to(ROOT).as_posix(),(2026,11,1,0,0,0))
            item.external_attr=0o644<<16
            item.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(item,p.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(destination) as z:
        if z.testzip() is not None:
            raise RuntimeError('ZIP integrity failure')
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix+'.sha256').write_text(
        f'{digest}  {destination.name}\n',encoding='ascii')
    print(f'{mode}: {len(files)} files, {destination.stat().st_size:,} bytes -> {destination}')
    print('SHA256:',digest)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output',nargs='?',type=Path,default=ROOT.parent/f'Takumi-v{VERSION}-Source.zip')
    parser.add_argument('--mode',choices=['source','windows-setup'],default='source')
    args=parser.parse_args()
    package(args.output,args.mode)
