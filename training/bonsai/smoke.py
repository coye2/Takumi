"""Experimental exact-base LoRA diagnostic, never capability evidence."""
import hashlib
from contextlib import contextmanager
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


@contextmanager
def evaluated_layers(model):
    """Inference-only barriers: release each layer's lazy graph before the next."""
    layer_type = type(model.model.layers[0])
    original = layer_type.__call__
    def call(layer, *args, **kwargs):
        result = original(layer, *args, **kwargs)
        mx.eval(result)
        return result
    layer_type.__call__ = call
    try:
        yield
    finally:
        layer_type.__call__ = original


def run(model, tokenizer_path, out, steps, reload_from=None, dataset_path=None):
    from transformers import AutoTokenizer
    from mlx_lm.tuner.trainer import grad_checkpoint
    out = Path(out)
    fixtures = Path(__file__).resolve().parents[1] / 'smoke'
    fixture = Path(dataset_path) if dataset_path else fixtures / 'diagnostic-v2.json'
    if reload_from:
        prior = json.loads((Path(reload_from) / 'smoke-result.json').read_text())
        candidates = [Path(reload_from) / 'diagnostic-dataset.json', *fixtures.glob('diagnostic-*.json')]
        fixture = next((p for p in candidates if p.is_file() and
                        hashlib.sha256(p.read_bytes()).hexdigest() == prior['dataset_sha256']), None)
        if fixture is None:
            raise RuntimeError('The saved diagnostic dataset is unavailable or changed')
    (out / 'diagnostic-dataset.json').write_bytes(fixture.read_bytes())
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path), local_files_only=True)
    dataset = json.loads(fixture.read_text())
    records = dataset['examples']
    pilot = dataset.get('version') == 'verified-coding-pilot-v1'
    rows = [encode_completion(tokenizer, r['messages'], max_length=96) for r in records]
    batches = [(mx.array([r['input_ids'][:-1]]), mx.array([r['labels'][1:]])) for r in rows]
    report = {'diagnostic_only': not pilot, 'engineering_pilot':pilot, 'dataset_version':dataset['version'],
              'dataset_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'optimizer_steps': 0, 'rank': 4, 'scale': 4.0, 'seed': 27, 'learning_rate': 0.0003,
              'target_layers': [62, 63], 'target_module': 'mlp.down_proj', 'logs': []}

    def evaluate():
        losses, selected = [], []
        for inputs, labels in batches:
            with evaluated_layers(model):
                logits = model(inputs)
                mx.eval(logits)
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
    print('Smoke: checking layer evaluation barriers against saved four-token logits', flush=True)
    with evaluated_layers(model):
        check = model(mx.array([[1, 2, 3, 4]]))
        mx.eval(check)
    report['evaluation_barrier_max_abs_error'] = float(np.max(np.abs(
        np.asarray(check[0, -1]).astype(np.float32) - np.load(out / 'last_logits.npy'))))
    del check
    if report['evaluation_barrier_max_abs_error'] > 1e-5:
        raise RuntimeError('Layer evaluation barriers changed base logits')
    print('Smoke: evaluating exact base on diagnostic tokens', flush=True)
    base_losses, base_logits = evaluate()
    report['base_losses'] = base_losses
    if pilot and not reload_from:
        from training.pilot.evaluate import score
        report['heldout_base'] = score(model, tokenizer, out, 'base')
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
        report['reloaded_adapter_sha256'] = hashlib.sha256((Path(reload_from) / 'adapter.safetensors').read_bytes()).hexdigest()
        if report['reloaded_adapter_sha256'] != prior['adapter_sha256']:
            raise RuntimeError('Saved adapter bytes changed')
        model.load_weights(str(Path(reload_from) / 'adapter.safetensors'), strict=False)
    else:
        print('Smoke: checking zero-adapter parity', flush=True)
        initial_losses, initial_logits = evaluate()
        report['zero_adapter_max_abs_error'] = max(float(np.max(np.abs(a-b))) for a,b in zip(base_logits, initial_logits))
        if report['zero_adapter_max_abs_error'] > 1e-5:
            raise RuntimeError('Zero adapter changes base outputs')
        del initial_logits
        layer_type = type(model.model.layers[0])
        original_layer_call = layer_type.__call__
        grad_checkpoint(model.model.layers[0])
        model.train()
        optimizer = optim.Adam(learning_rate=report['learning_rate'])

        def loss_fn(current, inputs, labels):
            logits = current(inputs)
            mask = labels != -100
            losses = nn.losses.cross_entropy(logits, mx.maximum(labels, 0), reduction='none')
            return (losses * mask).sum() / mask.sum()

        value_grad = nn.value_and_grad(model, loss_fn)
        for step in range(steps):
            print(f'Smoke: backward/optimizer step {step + 1}', flush=True)
            start = time.monotonic()
            loss, gradients = value_grad(model, *batches[step % len(batches)])
            mx.eval(loss, gradients)
            flat_grad = list(tree_flatten(gradients))
            norms = {k: float(mx.linalg.norm(v).item()) for k, v in flat_grad}
            if not math.isfinite(loss.item()) or not all(math.isfinite(v) for v in norms.values()):
                raise RuntimeError('Nonfinite loss or gradient')
            if step == 0 and not all(norms[k] > 0 for k in norms if k.endswith('lora_b')):
                raise RuntimeError('Gradient did not reach both selected adapters')
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
        layer_type.__call__ = original_layer_call
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
    report['diagnostic_fit_threshold_passed'] = all(loss < 0.1 for loss in final_losses)
    report['tiny_overfit_passed'] = (report['diagnostic_fit_threshold_passed'] and
                                   max(base_losses) >= 0.1 and sum(final_losses) < sum(base_losses))
    report['training_performed'] = report['optimizer_steps'] > 0
    if pilot:
        from training.pilot.evaluate import score
        for adapter in adapters:
            adapter.enabled = True
        report['heldout_candidate'] = score(model, tokenizer, out, 'reloaded' if reload_from else 'candidate')
        report['promotion_eligible'] = False
        report['promotion_reason'] = 'Small pilot only; broader regression and actual serving qualification required'
    (out / 'smoke-result.json').write_text(json.dumps(report, indent=2))
    return {k: v for k, v in report.items() if 'hashes' not in k}
