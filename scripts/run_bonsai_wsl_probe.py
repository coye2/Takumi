"""Windows host guard for the isolated WSL model probe; no WSL/global changes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from audit_sumi_training import memory


def wsl_path(path):
    path = Path(os.path.abspath(path))
    if not path.drive or len(path.drive) != 2:
        raise ValueError("Expected a Windows drive path")
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--gguf", required=True, type=Path)
    parser.add_argument("--gguf-python", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--smoke-steps", type=int, choices=range(1, 17))
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--reload-from", type=Path)
    parser.add_argument("--cancel-file", type=Path)
    args = parser.parse_args()
    if (args.smoke_steps or args.reload_from) and not args.tokenizer:
        parser.error("The smoke test requires the exact local tokenizer")
    if args.smoke_steps and args.reload_from:
        parser.error("Reload verification and training must use separate processes")
    if args.out.exists():
        raise ValueError("Output directory must not exist")
    if memory()["available_bytes"] < 12 * 1024**3:
        raise RuntimeError("Need 12 GiB available Windows RAM before retrying this 27B loading probe; sufficiency remains unverified")
    script = Path(__file__).with_name("probe_bonsai_model.py")
    cmd = ["wsl", "-d", "Ubuntu", "--", wsl_path(args.python), wsl_path(script),
           "--gguf", wsl_path(args.gguf), "--gguf-python", wsl_path(args.gguf_python),
           "--out", wsl_path(args.out)]
    if args.smoke_steps:
        cmd += ["--smoke-steps", str(args.smoke_steps)]
    if args.tokenizer:
        cmd += ["--tokenizer", wsl_path(args.tokenizer)]
    if args.reload_from:
        cmd += ["--reload-from", wsl_path(args.reload_from)]
    started = time.monotonic()
    report = {"command": cmd, "samples": [], "stop_reason": None}
    last_gpu_sample = -10.0
    proc = subprocess.Popen(cmd)
    def stop_owned_probe():
        pid_file = args.out / "pid.json"
        if not pid_file.exists():
            raise RuntimeError("Probe has not registered a PID; cannot safely target it")
        pid = int(json.loads(pid_file.read_text())["pid"])
        check = subprocess.check_output(["wsl", "-d", "Ubuntu", "--", "cat", f"/proc/{pid}/cmdline"])
        if wsl_path(script).encode() not in check or wsl_path(args.out).encode() not in check:
            raise RuntimeError("Probe PID identity changed; refusing to signal it")
        subprocess.run(["wsl", "-d", "Ubuntu", "--", "kill", "-TERM", str(pid)], check=True)
        proc.wait(timeout=30)
    try:
        while proc.poll() is None:
            available = memory()["available_bytes"]
            elapsed = time.monotonic() - started
            sample = {"seconds": elapsed, "windows_available_bytes": available}
            report["samples"].append(sample)
            cancelled = args.cancel_file and args.cancel_file.exists()
            if cancelled or available < 2 * 1024**3 or elapsed > 900:
                report["stop_reason"] = "user stop" if cancelled else "Windows RAM headroom" if available < 2 * 1024**3 else "15-minute bound"
                stop_owned_probe()
                break
            if elapsed - last_gpu_sample >= 2:
                gpu = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5)
                sample["gpu_memory_mib"] = gpu.stdout.strip()
                sample["gpu_query_exit_code"] = gpu.returncode
                last_gpu_sample = elapsed
            time.sleep(0.5)
    except KeyboardInterrupt:
        report["stop_reason"] = "user interruption"
        if proc.poll() is None:
            stop_owned_probe()
    finally:
        report["exit_code"] = proc.poll()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".host.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"exit_code": report["exit_code"], "stop_reason": report["stop_reason"],
                      "minimum_windows_available_bytes": min((s["windows_available_bytes"] for s in report["samples"]), default=None)}))
    raise SystemExit(0 if report["exit_code"] == 0 and report["stop_reason"] is None else 2)


if __name__ == "__main__":
    main()
