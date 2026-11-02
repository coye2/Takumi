"""Prepare tiny diagnostic tokens; this does not train a model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from training.bonsai.samples import encode_completion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "training/smoke/diagnostic-v1.json")
    parser.add_argument("--max-length", type=int, default=96)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer), local_files_only=True)
    dataset = json.loads(args.data.read_text(encoding="utf-8"))
    rows = []
    for record in dataset["examples"]:
        row = encode_completion(tokenizer, record["messages"], max_length=args.max_length)
        row["id"] = record["id"]
        rows.append(row)
    report = {"dataset_version": dataset["version"], "source_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "purpose": dataset["purpose"], "license": dataset["license"], "training_performed": False,
              "example_count": len(rows), "tokens": sum(len(x["input_ids"]) for x in rows),
              "supervised_tokens": sum(x["supervised_tokens"] for x in rows),
              "max_length": args.max_length, "packing": False, "truncation": False,
              "tokenizer_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.tokenizer.iterdir()
                                  if p.name in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja")},
              "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
