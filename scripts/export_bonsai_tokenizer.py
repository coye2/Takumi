"""Export the installed GGUF tokenizer/template without loading model weights."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    from transformers import PreTrainedTokenizerFast
    from transformers.integrations.gguf.gguf_tokenizer_mapping import get_gguf_tokenizer, convert_gguf_tokenizer
    architecture, metadata, config = get_gguf_tokenizer(args.gguf)
    backend, _ = convert_gguf_tokenizer(architecture, metadata)
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=backend, **config)
    vocab = tokenizer.get_vocab()
    for token_id, token in enumerate(metadata["tokens"]):
        if vocab.get(token) != token_id:
            raise AssertionError(f"Vocabulary identity mismatch at {token_id}")
    if len(vocab) != len(metadata["tokens"]):
        raise AssertionError("Vocabulary size changed")
    tokenizer.save_pretrained(str(args.out))
    samples = ["AArch64: x0 = 0xFFFFFFFF;\n", "日本語とItaliano: caffè", "<|im_start|>assistant\n<think>\n"]
    encodings = []
    for text in samples:
        tokens = tokenizer.encode(text, add_special_tokens=False)
        if tokenizer.decode(tokens, skip_special_tokens=False) != text:
            raise AssertionError("Tokenizer round trip failed")
        encodings.append({"text": text, "tokens": tokens})
    report = {"vocab_size": len(vocab), "all_vocabulary_ids_identical": True,
              "eos_token_id": tokenizer.eos_token_id, "bos_token_id": tokenizer.bos_token_id,
              "pad_token_id": tokenizer.pad_token_id, "round_trips": encodings,
              "ollama_tokenization_equivalence": "not_yet_tested",
              "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.out.iterdir() if p.is_file()}}
    (args.out / "export-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
