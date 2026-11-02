"""Read-only, bounded engineering tools for emulation, shader and decomp work.

No reverse-engineering executables, firmware, copyrighted samples, or binaries are
shipped. Project files are explicitly selected; external programs run only when
installed, with timeout and no shell. This is evidence collection, not proof of
successful emulation or matching decompilation.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from collections import Counter

from .plugins import in_project
from .compiler_checks import find_tool

EXTERNAL = {
    "ghidra": {"binary": "analyzeHeadless", "purpose": "Headless disassembly and cross-reference analysis", "task": "Configure a project-defined Ghidra headless task; no automation runs implicitly"},
    "renderdoc": {"binary": "renderdoccmd", "purpose": "Graphics frame capture and replay inspection", "task": "Capture or inspect a locally recorded frame in the approved project harness"},
    "objdiff": {"binary": "objdiff-cli", "purpose": "Matching-decompilation object comparison", "task": "Compare a build against your independently obtained reference object"},
    "spirv-val": {"binary": "spirv-val", "purpose": "Vulkan SPIR-V structural validation", "task": "Validate selected SPIR-V module"},
    "spirv-dis": {"binary": "spirv-dis", "purpose": "SPIR-V readable disassembly", "task": "Use a registered project task to save disassembly"},
    "clangd": {"binary": "clangd", "purpose": "C/C++ definitions, references and type navigation", "task": "Supply compile_commands.json for accurate indexing"},
    "clang": {"binary": "clang", "purpose": "C/C++ syntax checks and static source indexing", "task": "Bounded syntax/AST inspection; project builds remain separate evidence"},
    "glslang": {"binary": "glslangValidator", "purpose": "Stage-specific GLSL syntax validation", "task": "Label shader blocks vert, frag, comp, geom, tesc or tese"},
    "dotnet": {"binary": "dotnet", "purpose": "C# builds and diagnostics", "task": "Use the repository's exact pinned SDK"},
}
IGNORE = {'.git', '.vs', 'node_modules', '.venv', 'dist', 'bin', 'obj', 'build', '__pycache__'}
SUFFIXES = {'.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.rs', '.py', '.go', '.js', '.ts', '.wgsl', '.glsl', '.hlsl', '.vert', '.frag', '.md', '.txt'}


def inventory(*, which=shutil.which) -> list[dict]:
    """Advertise actual tools, never silently imply installed capabilities."""
    if which is shutil.which:
        which = find_tool
    return [{"id": name, "installed": bool(exe := which(cfg["binary"])),
             "path": exe or None, "purpose": cfg["purpose"], "usage": cfg["task"]}
            for name, cfg in EXTERNAL.items()]


def _file(project: Path, relative: str) -> Path:
    try:
        target = in_project(Path(project), relative)
    except (OSError, ValueError) as exc:
        raise ValueError("Choose an existing file inside the selected project") from exc
    if not target.is_file() or target.is_symlink():
        raise ValueError("Choose an ordinary file inside the selected project")
    return target


def inspect_binary(project: Path, relative: str, *, max_bytes: int = 64 * 1024 * 1024) -> dict:
    """Bounded format/hash/entropy inspection without exporting binary data."""
    target = _file(project, relative)
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError("Binary inspection is capped at 64 MiB; select a smaller input or use a project task")
    digest = hashlib.sha256()
    sample = bytearray()
    count = Counter()
    with target.open('rb') as fp:
        while block := fp.read(256 * 1024):
            digest.update(block)
            count.update(block)
            if len(sample) < 65536:
                sample.extend(block[:65536-len(sample)])
    head = bytes(sample[:32])
    kind = ('ELF executable or object' if head.startswith(b'\x7fELF') else
            'Windows PE executable or DLL' if head.startswith(b'MZ') else
            'SPIR-V shader module' if head[:4] == b'\x03\x02\x23\x07' else
            'WASM binary' if head.startswith(b'\0asm') else
            'Nintendo Switch NSO' if head.startswith(b'NSO0') else
            'Nintendo NPDM' if head.startswith(b'META') else
            'Mach-O binary' if head[:4] in (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe') else
            'Unknown or proprietary format')
    entropy = 0.0 if not size else -sum((n / size) * math.log2(n / size) for n in count.values())
    # Do not return identifiable strings or raw payload from proprietary assets.
    return {"path": target.relative_to(Path(project).resolve(strict=True)).as_posix(),
            "bytes": size, "sha256": digest.hexdigest(), "format_hint": kind,
            "entropy_bits_per_byte": round(entropy, 3),
            "signature_hex": head[:8].hex(),
            "note": "Format and entropy are clues. They do not identify the complete ABI or prove decompilation correctness."}


def symbol_search(project: Path, query: str, *, max_results: int = 35) -> dict:
    """Exact identifier search with a bounded Python fallback, never executes repo code."""
    root = Path(project).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project directory is unavailable")
    query = str(query).strip()
    if not 2 <= len(query) <= 120 or any(c in query for c in '\r\n\0'):
        raise ValueError("Search for a symbol or diagnostic between 2 and 120 characters")
    limit = min(max(1, max_results), 60)
    result = []
    scanned = 0
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE and not (Path(folder)/d).is_symlink())
        for name in sorted(names):
            if scanned >= 5000 or len(result) >= limit:
                break
            p = Path(folder)/name
            if p.suffix.lower() not in SUFFIXES or p.is_symlink():
                continue
            try:
                if p.stat().st_size > 1_000_000:
                    continue
                scanned += 1
                with p.open('r',encoding='utf-8',errors='replace') as fp:
                    for line_no, line in enumerate(fp, 1):
                        if query.casefold() in line.casefold():
                            result.append({"file":p.relative_to(root).as_posix(), "line":line_no,
                                           "excerpt":line.strip()[:240]})
                            if len(result)>=limit:break
            except (OSError,UnicodeError):
                continue
        if scanned >= 5000 or len(result) >= limit:
            break
    return {"query":query,"matches":result,"scanned_files":scanned,
            "limited": len(result)>=limit or scanned>=5000,
            "note":"Exact source matches. Results require version, caller and test verification."}


def validate_spirv(project: Path, relative: str, *, runner=subprocess.run, which=shutil.which) -> dict:
    target = _file(project, relative)
    if target.stat().st_size > 32_000_000:
        raise ValueError("SPIR-V module exceeds 32 MB limit")
    with target.open('rb') as fp:
        if fp.read(4)!=b'\x03\x02\x23\x07':
            raise ValueError('Selected file is not a little-endian SPIR-V module')
    exe = which('spirv-val')
    if not exe:
        return {"available":False,"verified":False,
                "message":"Install SPIRV-Tools and add spirv-val to PATH before validating."}
    try:
        res = runner([exe, str(target)],capture_output=True,text=True,encoding='utf-8',
                     errors='replace',timeout=25,shell=False,cwd=str(project))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available":True,"verified":False,"message":str(exc)[:250]}
    return {"available":True,"verified":res.returncode==0,
            "exit_code":res.returncode,"output":((res.stderr or '')+'\n'+(res.stdout or ''))[-4500:]}


def disassemble_spirv(project: Path, relative: str, *, runner=subprocess.run,
                      which=shutil.which) -> dict:
    """Run an installed SPIR-V disassembler on a selected project shader.

    No shader compiler, binary or GPU context is bundled or inferred. Full
    disassembly is bounded to protect the UI and conversations from megabytes
    of generated output.
    """
    target = _file(project, relative)
    if target.stat().st_size > 8_000_000:
        raise ValueError("Select a SPIR-V shader smaller than 8 MB")
    with target.open('rb') as fp:
        if fp.read(4) != b'\x03\x02\x23\x07':
            raise ValueError("Selected file is not a little-endian SPIR-V module")
    exe = which('spirv-dis')
    if not exe:
        return {"available": False, "disassembled": False,
                "message": "SPIRV-Tools spirv-dis is not installed or on PATH"}
    try:
        res = runner([exe, str(target)], cwd=str(Path(project).resolve(strict=True)),
                     capture_output=True, text=True, encoding='utf-8', errors='replace',
                     timeout=25, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "disassembled": False, "message": str(exc)[:230]}
    return {"available": True, "disassembled": res.returncode == 0,
            "exit_code": res.returncode, "assembly": (res.stdout or '')[:12000],
            "truncated": len(res.stdout or '') > 12000,
            "diagnostic": (res.stderr or '')[-1200:],
            "note": "Disassembly is evidence, not proof of shader equivalence or correctness."}


def analyze_ghidra(project: Path, relative: str, *, runner=subprocess.run,
                   which=shutil.which, temp_factory=None) -> dict:
    """Opt-in read-only Ghidra import and analysis; never downloads binaries.

    Limits Ghidra CPU, heap and duration. Runs a fixed, project-independent
    Takumi script for bounded function names and addresses, not arbitrary model
    generated scripts. Each analysis occurs in a disposable local project.
    """
    import tempfile
    target = _file(project, relative)
    if target.stat().st_size > 64_000_000:
        raise ValueError("Ghidra quick analysis is limited to 64 MB inputs")
    exe = which('analyzeHeadless') or which('analyzeHeadless.bat')
    if not exe:
        return {"available": False, "analyzed": False,
                "message": "Install Ghidra and add analyzeHeadless to PATH"}
    scripts = Path(__file__).resolve().parents[1] / 'scripts' / 'ghidra'
    fixed = scripts / 'TakumiFunctionSummary.py'
    if not fixed.is_file():
        raise ValueError("Takumi's fixed Ghidra function-summary script is missing")
    factory = temp_factory or tempfile.TemporaryDirectory
    with factory(prefix='takumi-ghidra-') as tmp:
        location = Path(tmp)
        args = [exe, str(location), 'TakumiAnalysis', '-import', str(target),
                '-readOnly', '-max-cpu', '2', '-analysisTimeoutPerFile', '160',
                '-scriptPath', str(scripts), '-postScript', fixed.name, '60']
        env = os.environ.copy()
        env['GHIDRA_HEADLESS_MAXMEM'] = '2G'
        env.pop('GHIDRA_JAVA_OPTIONS', None)
        env.pop('GHIDRA_HEADLESS_JAVA_OPTIONS', None)
        try:
            res = runner(args, cwd=str(Path(project).resolve(strict=True)), env=env,
                         capture_output=True, text=True, encoding='utf-8', errors='replace',
                         timeout=220, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": True, "analyzed": False, "reason": type(exc).__name__,
                    "message": "Ghidra quick analysis failed or exceeded its execution limit"}
    text = (res.stdout or '') + '\n' + (res.stderr or '')
    hits=[]
    for line in text.splitlines():
        marker = line.find('TAKUMI_FUNCTION|')
        if marker < 0 or len(hits) >= 60:
            continue
        fields = line[marker+len('TAKUMI_FUNCTION|'):].split('|', 2)
        if len(fields) == 2 and re.fullmatch(r'[0-9A-Fa-f]{1,18}', fields[0]):
            hits.append({"address": fields[0], "name": fields[1][:100]})
    ok = res.returncode == 0
    return {"available": True, "analyzed": ok, "exit_code": res.returncode,
            "functions": hits, "function_count_reported": len(hits),
            "diagnostic": ('Analysis completed (function summary limited to 60 entries)' if ok
                           else 'Ghidra reported a failure: ' + text[-800:]),
            "source_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "note": "Ghidra analysis is local, bounded and heuristic; it does not establish a matching decompilation."}
