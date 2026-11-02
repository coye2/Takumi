"""Completion-only samples for the exact exported Bonsai chat template.

No sequence packing or silent truncation. Earlier user/assistant/tool history
is context only; each row supervises one final assistant completion and EOS.
"""

IGNORE_INDEX = -100


def encode_completion(tokenizer, messages, *, max_length, tools=None):
    if len(messages) < 2 or messages[-1].get("role") != "assistant":
        raise ValueError("A final assistant completion and preceding context are required")
    if max_length < 2:
        raise ValueError("max_length must be at least two")
    options = dict(tokenize=False, enable_thinking=False, tools=tools)
    prefix = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, **options)
    complete = tokenizer.apply_chat_template(messages, add_generation_prompt=False, **options)
    if not complete.startswith(prefix):
        raise ValueError("Template completion does not preserve its generation prefix")
    prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
    full_ids = tokenizer.encode(complete, add_special_tokens=False)
    if full_ids[:len(prefix_ids)] != prefix_ids:
        raise ValueError("Tokenization crosses the completion boundary")
    suffix = full_ids[len(prefix_ids):]
    eos = tokenizer.eos_token_id
    if eos is None or suffix.count(eos) != 1:
        raise ValueError("Expected exactly one assistant EOS")
    stop = len(prefix_ids) + suffix.index(eos) + 1
    ids = full_ids[:stop]
    if len(ids) > max_length:
        raise ValueError(f"Sample has {len(ids)} tokens; limit {max_length}; truncation is disabled")
    if stop - len(prefix_ids) < 2:
        raise ValueError("Assistant completion is empty")
    labels = [IGNORE_INDEX] * len(prefix_ids) + ids[len(prefix_ids):]
    return {"input_ids": ids, "labels": labels, "prompt_tokens": len(prefix_ids),
            "supervised_tokens": len(ids) - len(prefix_ids), "rendered": complete,
            "packing": False, "truncated": False}


def pad_rows(rows, pad_token_id):
    """Independent right-padded rows, never concatenated sequences."""
    if not rows:
        raise ValueError("At least one row is required")
    width = max(len(row["input_ids"]) for row in rows)
    result = {"input_ids": [], "labels": [], "attention_mask": []}
    for row in rows:
        size = len(row["input_ids"])
        if len(row["labels"]) != size:
            raise ValueError("Input/label length mismatch")
        result["input_ids"].append(row["input_ids"] + [pad_token_id] * (width - size))
        result["labels"].append(row["labels"] + [IGNORE_INDEX] * (width - size))
        result["attention_mask"].append([1] * size + [0] * (width - size))
    return result
