"""Streaming local inference for the verified loop. Loopback Ollama only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .context import Budget

BASE_URL = 'http://127.0.0.1:11434'
DEFAULT_MODEL = 'sumi-1.0:latest'


MIN_BATCH_RAM_GIB = 6.0


class ModelUnavailable(RuntimeError):
    pass


def available_ram_gib() -> float | None:
    """Windows available physical memory, or None where this cannot be read."""
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong), ('available', ctypes.c_ulonglong),
                        ('page_total', ctypes.c_ulonglong), ('page_free', ctypes.c_ulonglong), ('virtual_total', ctypes.c_ulonglong),
                        ('virtual_free', ctypes.c_ulonglong), ('extended', ctypes.c_ulonglong)]
        status = Status(length=ctypes.sizeof(Status))
        return status.available / 1024**3 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    except (AttributeError, OSError):
        return None


def require_batch_memory(minimum: float = MIN_BATCH_RAM_GIB) -> None:
    """Long batch jobs load Sumi repeatedly. Refuse rather than push the owner's
    open applications and a running Takumi into paging."""
    available = available_ram_gib()
    if available is not None and available < minimum:
        raise SystemExit(f'Only {available:.1f} GiB of RAM is available; this needs {minimum:.0f} GiB. Nothing was run. Free some memory and try again.')


class OllamaClient:
    def __init__(self, model: str = DEFAULT_MODEL, *, budget: Budget | None = None, base_url: str = BASE_URL,
                 temperature: float = 0.1, keep_alive: str = '10m', think: bool | None = False, timeout: int = 900, options: dict | None = None):
        self.model, self.budget, self.base_url = model, budget or Budget(), base_url.rstrip('/')
        self.temperature, self.keep_alive, self.think, self.timeout = temperature, keep_alive, think, timeout
        self.options = options or {}

    def complete(self, messages: list[dict], *, cancel=None, on_token=None, think: bool | None = None, num_predict: int | None = None) -> dict:
        body = {'model': self.model, 'messages': messages, 'stream': True, 'keep_alive': self.keep_alive,
                'options': {'num_ctx': self.budget.num_ctx, 'num_predict': num_predict or self.budget.num_predict,
                            'temperature': self.temperature, **self.options}}
        chosen = self.think if think is None else think
        if chosen is not None:
            body['think'] = chosen
        request = urllib.request.Request(self.base_url + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
        started, first, text, thought, final = time.monotonic(), None, [], [], {}
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    if cancel and cancel.is_set():
                        break
                    event = json.loads(raw)
                    if event.get('error'):
                        raise ModelUnavailable(str(event['error'])[:400])
                    message = event.get('message') or {}
                    piece_thought = message.get('thinking') or ''
                    if isinstance(piece_thought, str) and piece_thought:
                        thought.append(piece_thought)
                    piece = message.get('content') or ''
                    if piece:
                        first = first if first is not None else time.monotonic() - started
                        text.append(piece)
                        if on_token:
                            on_token(piece)
                    if event.get('done'):
                        final = event
        except urllib.error.HTTPError as exc:
            raise ModelUnavailable(exc.read().decode('utf-8', 'replace')[:400]) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelUnavailable(f'The local model did not respond: {exc}') from exc
        eval_ns = final.get('eval_duration') or 0
        reasoning = ''.join(thought)
        if len(reasoning) > 12000:
            reasoning = reasoning[:12000] + '\n[reasoning truncated]'
        return {'text': ''.join(text), 'thinking': reasoning, 'done_reason': final.get('done_reason', ''), 'prompt_tokens': final.get('prompt_eval_count', 0),
                'output_tokens': final.get('eval_count', 0), 'prompt_ms': round((final.get('prompt_eval_duration') or 0) / 1e6),
                'output_ms': round(eval_ns / 1e6), 'tokens_per_second': round(final.get('eval_count', 0) / (eval_ns / 1e9), 1) if eval_ns else 0,
                'first_token_s': round(first, 2) if first is not None else None, 'seconds': round(time.monotonic() - started, 2)}
