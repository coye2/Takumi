"""Experimental exact Q2_0 base forward probe. No optimizer or promotion."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", required=True)
    parser.add_argument("--gguf-python", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--smoke-steps", type=int, choices=range(1, 129))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--train-config", type=Path)
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--reload-from", type=Path)
    parser.add_argument("--continue-from", type=Path)
    args = parser.parse_args()
    if (args.smoke_steps or args.reload_from or args.continue_from) and not args.tokenizer:
        parser.error("The smoke test requires the exact local tokenizer")
    if args.smoke_steps and args.reload_from:
        parser.error("Reload verification and training must use separate processes")
    if args.continue_from and not args.smoke_steps:
        parser.error("Continuing a run requires a step count")
    if args.continue_from and args.reload_from:
        parser.error("Reload verification and continued training are separate")
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "pid.json").write_text(json.dumps({"pid": os.getpid()}))
    result = {"training_performed": False, "optimizer_steps": 0, "status": "starting"}
    start = time.monotonic()
    import psutil
    import mlx.core as mx
    proc = psutil.Process()
    stop = threading.Event()
    samples = []
    def monitor():
        with (args.out / "resources.jsonl").open("w") as log:
            while not stop.is_set():
                sample = {"seconds": time.monotonic() - start, "rss_bytes": proc.memory_info().rss,
                          "available_ram": psutil.virtual_memory().available,
                          "swap_used": psutil.swap_memory().used}
                log.write(json.dumps(sample) + "\n"); log.flush()
                samples.append(sample)
                # Bound this experiment's host footprint; no uncontrolled offload.
                if sample["rss_bytes"] > 6 * 1024**3 or sample["available_ram"] < 2 * 1024**3:
                    log.write(json.dumps({"abort": "host memory guard"}) + "\n"); log.flush()
                    os._exit(3)
                stop.wait(0.5)
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        if not mx.cuda.is_available():
            raise RuntimeError("CUDA is required")
        mx.set_default_device(mx.gpu)
        mx.set_memory_limit(12 * 1024**3)
        mx.set_cache_limit(128 * 1024**2)
        from training.bonsai.runtime import load
        print("Loading exact installed weights with a 12 GiB MLX allocation limit", flush=True)
        model, info, reader = load(args.gguf, args.gguf_python)
        result["load_seconds"] = time.monotonic() - start
        result["mapping"] = info
        print("Loaded; testing four-token forward", flush=True)
        forward = time.monotonic()
        logits = model(mx.array([[1, 2, 3, 4]]))
        mx.eval(logits)
        result.update(status="forward_passed", forward_seconds=time.monotonic() - forward,
                      logits_shape=list(logits.shape), finite=bool(mx.all(mx.isfinite(logits)).item()),
                      next_token=int(mx.argmax(logits[0, -1]).item()))
        mx.save(str(args.out / "last_logits.npy"), logits[0, -1].astype(mx.float32))
        result["peak_mlx_memory_bytes"] = mx.get_peak_memory()
        if not result["finite"]:
            raise RuntimeError("Nonfinite base logits")
        # Preserve forward evidence even if the memory guard exits during training.
        (args.out / "result.json").write_text(json.dumps(result, indent=2))
        del logits
        mx.clear_cache()
        if args.smoke_steps or args.reload_from or args.continue_from:
            from training.bonsai.smoke import run
            result["smoke"] = run(model, args.tokenizer, args.out, args.smoke_steps or 0, args.reload_from, args.dataset, args.train_config, args.continue_from)
            result["optimizer_steps"] = result["smoke"]["optimizer_steps"]
            result["training_performed"] = result["optimizer_steps"] > 0
            result["peak_mlx_memory_bytes"] = mx.get_peak_memory()
    except Exception:
        result.update(status="failed", traceback=traceback.format_exc())
    finally:
        stop.set(); monitor_thread.join()
        progress = args.out / "smoke-progress.json"
        if progress.exists():
            saved = json.loads(progress.read_text())
            result["optimizer_steps"] = saved["optimizer_steps"]
            result["training_performed"] = result["optimizer_steps"] > 0
        result["peak_rss_bytes"] = max((s["rss_bytes"] for s in samples), default=None)
        result["elapsed_seconds"] = time.monotonic() - start
        (args.out / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)
    raise SystemExit(0 if result["status"] == "forward_passed" else 2)


if __name__ == "__main__":
    main()
