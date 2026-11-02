"""Bounded 128-512 MiB CUDA placement diagnostic; does not load or train a model."""
import argparse
import gc
import json
import os
from pathlib import Path
import subprocess

import mlx.core as mx
import numpy as np
import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--chunks', type=int, choices=(8, 16, 24, 32), default=8,
                        help='16 MiB chunks; bounded to 512 MiB total')
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    mx.set_default_device(mx.gpu)
    mx.set_memory_limit(1024**3)
    mx.set_cache_limit(0)
    records = []

    def record(stage):
        sample = {
            'stage': stage, 'pid': os.getpid(),
            'rss_bytes': psutil.Process().memory_info().rss,
            'mlx_active_bytes': mx.get_active_memory(),
            'gpu_used_free_mib': subprocess.check_output([
                'nvidia-smi', '--query-gpu=memory.used,memory.free',
                '--format=csv,noheader,nounits'], text=True).strip(),
        }
        records.append(sample)
        print(json.dumps(sample), flush=True)

    record('before')
    held = []
    for i in range(args.chunks):
        source = mx.array(np.full((4 * 1024 * 1024,), i, dtype=np.uint32))
        result = mx.multiply(source, mx.array(1, dtype=source.dtype), stream=mx.gpu)
        mx.eval(result)
        held.append(result)
        del source, result
        gc.collect()
        mx.clear_cache()
        if (i + 1) % 8 == 0:
            record(f'after_{(i + 1) * 16}_mib')
    check = mx.sum(held[-1].astype(mx.float32))
    mx.eval(check)
    checksum = check.item()
    assert checksum == (args.chunks - 1) * 4 * 1024 * 1024
    record('after_checksum')
    held.clear()
    del check
    gc.collect()
    mx.clear_cache()
    record('released')
    report = {'payload_bytes': args.chunks * 16 * 1024**2, 'checksum': checksum,
              'training_performed': False, 'records': records,
              'limitation': 'Process RSS and shared GPU snapshots are not total Windows memory attribution.'}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)


if __name__ == '__main__':
    main()
