"""Read-only exact-base inventory. No model generation, training or promotion.

Run with Python 3.12 on Windows. Outputs may contain local paths/templates;
keep the output directory outside Git. Hashing streams files in 8 MiB blocks.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import urllib.request

MODEL_ID = "prism-ml/Ternary-Bonsai-2-27B-gguf"
REVISION = "94dd51cdbbec5309ed8b76a12f3268bc4fdceb93"
TAGS = ("bonsai-2:27b", "sumi-1.0:latest", "bonsai-2:9b", "rei-1.0:latest")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=60)
    return {"command": args, "exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def memory():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in
            ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "avail_extended")]
    if platform.system() != "Windows":
        return {"status": "not_measured", "reason": "Windows inventory only"}
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError()
    return {"total_bytes": status.total_phys, "available_bytes": status.avail_phys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {"schema_version": 1, "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "python": platform.python_version(), "training_performed": False,
              "optimizer_steps": 0, "trainable_parameters": None,
              "memory_before": memory(), "disk_free_bytes": shutil.disk_usage(args.out).free,
              "git": command(["git", "rev-parse", "HEAD"]),
              "gpu": command(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,memory.free", "--format=csv"])}
    api = "http://127.0.0.1:11434/api/"
    report["ollama_version"] = fetch(api + "version")
    report["resident_models"] = fetch(api + "ps")
    tags = {m["name"]: m for m in fetch(api + "tags")["models"]}
    upstream = fetch(f"https://huggingface.co/api/models/{MODEL_ID}/revision/{REVISION}?blobs=true")
    upstream_files = {s.get("lfs", {}).get("sha256"): s["rfilename"] for s in upstream["siblings"] if "lfs" in s}
    report["upstream"] = {"id": MODEL_ID, "revision": upstream["sha"], "files": upstream["siblings"],
                          "license": upstream.get("cardData", {}).get("license")}
    hashes = {}
    report["models"] = {}
    for tag in TAGS:
        if tag not in tags:
            raise RuntimeError(f"Required installed model missing: {tag}")
        show = fetch(api + "show", {"model": tag, "verbose": True})
        filename = tag.replace(":", "-") + ".json"
        (args.out / filename).write_text(json.dumps(show, indent=2), encoding="utf-8")
        blobs = []
        for raw in re.findall(r"^FROM (.+)$", show["modelfile"], re.MULTILINE):
            path = Path(raw.strip().strip('"'))
            if not path.is_file():
                raise RuntimeError(f"FROM path does not resolve to a local file: {path}")
            key = str(path.resolve())
            if key not in hashes:
                hashes[key] = sha256(path)
            digest = hashes[key]
            blobs.append({"path": key, "bytes": path.stat().st_size, "sha256": digest,
                          "upstream_filename": upstream_files.get(digest)})
        report["models"][tag] = {"digest": tags[tag]["digest"], "details": show["details"], "blobs": blobs,
                                "template_sha256": hashlib.sha256(show.get("template", "").encode()).hexdigest(),
                                "show_sha256": sha256(args.out / filename)}
    report["memory_after"] = memory()
    report["elapsed_seconds"] = time.monotonic() - started
    (args.out / "inventory.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out.resolve()), "elapsed_seconds": report["elapsed_seconds"],
                      "training_performed": False, "optimizer_steps": 0}))


if __name__ == "__main__":
    main()
