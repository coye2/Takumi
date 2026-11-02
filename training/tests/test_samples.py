"""Actual-tokenizer mask tests; protocol fixtures are not curriculum data."""
import json
import os
import unittest

from training.bonsai.samples import IGNORE_INDEX, encode_completion, pad_rows


class SampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("TAKUMI_TEST_TOKENIZER")
        if not path:
            raise unittest.SkipTest("Set TAKUMI_TEST_TOKENIZER to the exact exported local tokenizer")
        from transformers import AutoTokenizer
        cls.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)

    def sample(self, answer="TAKUMI_TRAIN_OK", max_length=96):
        return encode_completion(self.tokenizer, [
            {"role": "user", "content": "Reply exactly with TAKUMI_TRAIN_OK."},
            {"role": "assistant", "content": answer}], max_length=max_length)

    def test_only_answer_and_eos_are_supervised(self):
        row = self.sample()
        boundary = row["prompt_tokens"]
        self.assertTrue(all(v == IGNORE_INDEX for v in row["labels"][:boundary]))
        self.assertEqual(row["labels"][boundary:], row["input_ids"][boundary:])
        self.assertEqual(self.tokenizer.decode(row["labels"][boundary:], skip_special_tokens=False),
                         "TAKUMI_TRAIN_OK<|im_end|>")
        self.assertEqual(row["input_ids"][-1], self.tokenizer.eos_token_id)
        self.assertGreater(row["supervised_tokens"], 1)

    def test_repeated_target_in_user_is_masked(self):
        row = self.sample()
        self.assertIn("TAKUMI_TRAIN_OK", self.tokenizer.decode(row["input_ids"][:row["prompt_tokens"]]))
        self.assertEqual(sum(v != IGNORE_INDEX for v in row["labels"]), row["supervised_tokens"])

    def test_overlong_sample_rejected_not_truncated(self):
        with self.assertRaisesRegex(ValueError, "truncation is disabled"):
            self.sample(max_length=8)

    def test_embedded_eos_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one assistant EOS"):
            self.sample(answer="before<|im_end|>after")

    def test_padding_preserves_independent_rows(self):
        rows = [self.sample(), self.sample(answer="OK")]
        batch = pad_rows(rows, self.tokenizer.pad_token_id)
        for i, row in enumerate(rows):
            length = len(row["input_ids"])
            self.assertEqual(batch["input_ids"][i][:length], row["input_ids"])
            self.assertEqual(batch["labels"][i][:length], row["labels"])
            self.assertTrue(all(x == IGNORE_INDEX for x in batch["labels"][i][length:]))
            self.assertEqual(batch["attention_mask"][i], [1] * length + [0] * (len(batch["input_ids"][i]) - length))
            self.assertFalse(row["packing"])

    def test_tool_history_is_context_not_supervision(self):
        # An executed local fixture supplies the observed result; no external tool claim.
        def fixture_echo(text):
            return {"echo": text}
        observed = fixture_echo("observed")
        row = encode_completion(self.tokenizer, [
            {"role": "user", "content": "Echo observed with fixture_echo."},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "fixture_echo", "arguments": {"text": "observed"}}}]},
            {"role": "tool", "content": json.dumps(observed)},
            {"role": "assistant", "content": "observed"}], max_length=256)
        self.assertIn("<function=fixture_echo>", row["rendered"])
        self.assertIn("<tool_response>", row["rendered"])
        self.assertEqual(self.tokenizer.decode(row["labels"][row["prompt_tokens"]:], skip_special_tokens=False),
                         "observed<|im_end|>")

    def test_nonassistant_final_role_rejected(self):
        with self.assertRaisesRegex(ValueError, "final assistant"):
            encode_completion(self.tokenizer, [{"role": "user", "content": "Hi"}], max_length=96)


if __name__ == "__main__":
    unittest.main()
