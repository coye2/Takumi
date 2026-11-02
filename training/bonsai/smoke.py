"""Experimental exact-base LoRA diagnostic, never capability evidence."""
import hashlib
import json
import math
from pathlib import Path
import time

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten
import numpy as np

from .samples import encode_completion


class DiagnosticLoRA(nn.Module):
    """Add LoRA in the original activation basis, outside the packed transform."""
    def __init__(self, base, rank=4):
        super().__init__()
        self.base = base
        width = base.weight.shape[1] * 16
        outputs = base.weight.shape[0]
        self.lora_a = mx.random.uniform(-1 / math.sqrt(width), 1 / math.sqrt(width), (width, rank))
        self.lora_b = mx.zeros((rank, outputs))
        self.scale = 4.0
        self.enabled = True
        self.base.freeze()

    def __call__(self, x):
        original = self.base(x)
        if not self.enabled:
            return original
        return original + (self.scale * ((x @ self.lora_a) @ self.lora_b)).astype(original.dtype)


def tensor_hashes(items):
    result = {}
    for name, value in items:
        mx.eval(value)
        # Hash in storage order without staging a multi-GiB embedding on the host.
        flat = value.reshape(-1)
        digest = hashlib.sha256()
        for start in range(0, flat.size, 262144):
            # A slice aliases the original allocation: NumPy would migrate the
            # whole backing tensor and retain a host mirror. Materialize a small
            # independent CUDA result before asking NumPy for its bytes.
            chunk = mx.multiply(flat[start:start + 262144], mx.array(1, dtype=value.dtype))
            mx.eval(chunk)
            host = np.ascontiguousarray(np.asarray(chunk))
            digest.update(memoryview(host).cast('B'))
            del host, chunk
        result[name] = digest.hexdigest()
    return result


def run(model, tokenizer_path, out, steps, reload_from=None):
    from transformers import AutoTokenizer
    from mlx_lm.tuner.trainer import grad_checkpoint
    out = Path(out)
    fixture = Path(__file__).resolve().parents[1] / 'smoke/diagnostic-v1.json'
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path), local_files_only=True)
    records = json.loads(fixture.read_text())['examples']
    rows = [encode_completion(tokenizer, r['messages'], max_length=96) for r in records]
    batches = [(mx.array([r['input_ids'][:-1]]), mx.array([r['labels'][1:]])) for r in rows]
    report = {'diagnostic_only': True, 'dataset_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'optimizer_steps': 0, 'rank': 4, 'scale': 4.0, 'seed': 27,
              'target_layers': [0, 63], 'target_module': 'mlp.down_proj', 'logs': []}

    def evaluate():
        losses, selected = [], []
        for inputs, labels in batches:
            logits = model(inputs)
            mask = labels != -100
            targets = mx.maximum(labels, 0)
            loss = (nn.losses.cross_entropy(logits, targets, reduction='none') * mask).sum() / mask.sum()
            mx.eval(loss, logits)
            losses.append(float(loss.item()))
            selected.append(np.asarray(logits[0]).copy())
        return losses, selected

    model.freeze()
    base_items = list(tree_flatten(model.parameters()))
    report['frozen_array_elements'] = sum(v.size for _, v in base_items)
    # Packed elements are not the logical dense parameter count.
    print('Smoke: hashing frozen base in bounded chunks', flush=True)
    report['frozen_hashes_before'] = tensor_hashes(base_items)
    print('Smoke: evaluating exact base on diagnostic tokens', flush=True)
    base_losses, base_logits = evaluate()
    report['base_losses'] = base_losses
    mx.random.seed(27)
    adapters = []
    for index in report['target_layers']:
        layer = model.model.layers[index]
        adapter = DiagnosticLoRA(layer.mlp.down_proj)
        layer.mlp.down_proj = adapter
        adapters.append(adapter)
    trainable = list(tree_flatten(model.trainable_parameters()))
    if len(trainable) != 4 or not all(name.endswith(('lora_a', 'lora_b')) for name, _ in trainable):
        raise RuntimeError('Unexpected trainable parameter set')
    report['trainable_parameters'] = sum(v.size for _, v in trainable)
    report['trainable_shapes'] = {k: list(v.shape) for k, v in trainable}
    initial = {k: np.asarray(v).copy() for k, v in trainable}
    report['adapter_hashes_before'] = tensor_hashes(trainable)
    if reload_from:
        model.load_weights(str(Path(reload_from) / 'adapter.safetensors'), strict=False)
    else:
        initial_losses, initial_logits = evaluate()
        report['zero_adapter_max_abs_error'] = max(float(np.max(np.abs(a-b))) for a,b in zip(base_logits, initial_logits))
        if report['zero_adapter_max_abs_error'] > 1e-5:
            raise RuntimeError('Zero adapter changes base outputs')
        del initial_logits
        grad_checkpoint(model.model.layers[0])
        model.train()
        optimizer = optim.Adam(learning_rate=0.001)

        def loss_fn(current, inputs, labels):
            logits = current(inputs)
            mask = labels != -100
            losses = nn.losses.cross_entropy(logits, mx.maximum(labels, 0), reduction='none')
            return (losses * mask).sum() / mask.sum()

        value_grad = nn.value_and_grad(model, loss_fn)
        for step in range(steps):
            start = time.monotonic()
            loss, gradients = value_grad(model, *batches[step % len(batches)])
            mx.eval(loss, gradients)
            flat_grad = list(tree_flatten(gradients))
            norms = {k: float(mx.linalg.norm(v).item()) for k, v in flat_grad}
            if not math.isfinite(loss.item()) or not all(math.isfinite(v) for v in norms.values()):
                raise RuntimeError('Nonfinite loss or gradient')
            if step == 0 and not all(norms[k] > 0 for k in norms if k.endswith('lora_b')):
                raise RuntimeError('Gradient did not reach both early and late adapters')
            optimizer.update(model, gradients)
            mx.eval(model.trainable_parameters(), optimizer.state)
            report['optimizer_steps'] = step + 1
            report['logs'].append({'step': step + 1, 'loss': float(loss.item()), 'gradient_norms': norms,
                                   'seconds': time.monotonic() - start})
            mx.save_safetensors(str(out / 'adapter.safetensors'), dict(tree_flatten(model.trainable_parameters())))
            report['adapter_sha256'] = hashlib.sha256((out / 'adapter.safetensors').read_bytes()).hexdigest()
            (out / 'smoke-progress.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report['logs'][-1]), flush=True)
            mx.clear_cache()
    model.eval()
    final_losses, final_logits = evaluate()
    report['final_losses'] = final_losses
    report['adapter_hashes_after'] = tensor_hashes(tree_flatten(model.trainable_parameters()))
    report['parameter_delta_norms'] = {k: float(np.linalg.norm(np.asarray(v)-initial[k]))
                                       for k, v in tree_flatten(model.trainable_parameters())}
    for index, logits in enumerate(final_logits):
        np.save(out / f'diagnostic-logits-{index}.npy', logits)
    if reload_from:
        report['reload_max_abs_error'] = max(float(np.max(np.abs(logits-np.load(Path(reload_from) / f'diagnostic-logits-{i}.npy'))))
                                           for i, logits in enumerate(final_logits))
        if report['reload_max_abs_error'] > 1e-5:
            raise RuntimeError('Fresh-process adapter reload differs')
    for adapter in adapters:
        adapter.enabled = False
    _, disabled_logits = evaluate()
    report['disabled_max_abs_error'] = max(float(np.max(np.abs(a-b))) for a,b in zip(base_logits, disabled_logits))
    current_frozen = [(k.replace('.down_proj.base.', '.down_proj.'), v)
                      for k, v in tree_flatten(model.parameters())
                      if not k.endswith(('lora_a', 'lora_b'))]
    report['frozen_hashes_after'] = tensor_hashes(current_frozen)
    if report['frozen_hashes_before'] != report['frozen_hashes_after'] or report['disabled_max_abs_error'] > 1e-5:
        raise RuntimeError('Frozen base changed')
    report['tiny_overfit_passed'] = all(loss < 0.1 for loss in final_losses)
    report['training_performed'] = report['optimizer_steps'] > 0
    (out / 'smoke-result.json').write_text(json.dumps(report, indent=2))
    return {k: v for k, v in report.items() if 'hashes' not in k}
