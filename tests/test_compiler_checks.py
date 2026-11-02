import threading
import unittest
from unittest.mock import patch

from takumi.compiler_checks import check_block, find_tool
from takumi.code_quality import validate_generated_code, engineering_repair_prompt, accept_engineering_repair, requested_shader_stage


def qualification_samples():
    # Fixed valid/invalid corpus. These are compiler-oracle tests, not evidence
    # that the local model generated fifty correct programs.
    samples = []
    for n in range(13):
        samples.append(('c', f'int fence_{n}(unsigned done, unsigned target) {{ return done >= target; }}' if n % 2 == 0 else f'int fence_{n}(void) {{ return undeclared_{n}; }}', n % 2 == 0))
    for n in range(13):
        samples.append(('cpp', f'template<class T> T clamp_{n}(T x) {{ return x < 0 ? 0 : x; }} int use() {{ return clamp_{n}(3); }}' if n % 2 == 0 else f'struct Fence_{n} {{ int value; }}; int use() {{ Fence_{n} f{{}}; return f.missing; }}', n % 2 == 0))
    for n in range(12):
        samples.append(('python', f'def field_{n}(data):\n    return int.from_bytes(data[4:8], "little")\n' if n % 2 == 0 else f'def field_{n}(data)\n    return data[4:8]\n', n % 2 == 0))
    for n in range(12):
        samples.append(('frag', '#version 450\nlayout(location=0) out vec4 color;\nvoid main(){color=vec4(1.0);}\n' if n % 2 == 0 else '#version 450\nlayout(location=0) out vec4 color;\nvoid main(){color=unknown_value;}\n', n % 2 == 0))
    return samples


class CompilerCheckTests(unittest.TestCase):
    def test_fixed_fifty_sample_corpus_actual_available_tools(self):
        samples = qualification_samples()
        self.assertEqual(len(samples), 50)
        for i, (language, code, expected) in enumerate(samples):
            with self.subTest(sample=i, language=language):
                needed = 'clang' if language in ('c', 'cpp') else 'glslangValidator' if language == 'frag' else None
                if needed and not find_tool(needed):
                    self.skipTest(needed + ' unavailable; no compiler pass claimed')
                report = check_block(language, code)
                self.assertEqual(report['status'], 'passed' if expected else 'failed', report)

    def test_missing_tool_unknown_stage_unsafe_include_and_cancel_not_passes(self):
        with patch('takumi.compiler_checks.find_tool', return_value=None):
            self.assertEqual(check_block('cpp', 'int a;')['status'], 'unverified')
        self.assertEqual(check_block('glsl', 'void main(){}')['status'], 'unverified')
        self.assertEqual(check_block('cpp', '#include "../private.h"')['status'], 'unverified')
        self.assertEqual(check_block('cpp', '#define H "private.h"\n#include H')['status'], 'unverified')
        for directive in ('#/**/include "../private.h"', '%:include "../private.h"', '#\\\r\ninclude "../private.h"', '#define JOIN(a,b) a##b'):
            self.assertEqual(check_block('cpp', directive)['status'], 'unverified')
        event = threading.Event(); event.set()
        self.assertEqual(check_block('python', 'x=1', cancel=event)['status'], 'unverified')

    def test_mixed_blocks_never_promote_unchecked_block_to_verified(self):
        report = validate_generated_code('```python\nx=1\n```\n```glsl\nvoid main(){}\n```')
        self.assertEqual(report['status'], 'partially_checked')
        self.assertEqual([r['status'] for r in report['results']], ['passed', 'unverified'])
        bad = validate_generated_code('```python\ndef broken(\n```')
        self.assertEqual(bad['status'], 'needs_review')

    def test_correction_preserves_languages_and_does_not_rewrite_review_examples(self):
        original = '```python\ndef f(\n```\n```python\nx=1\n```'
        report = validate_generated_code(original)
        self.assertIsNotNone(engineering_repair_prompt('Generate two Python blocks', original, report))
        self.assertIsNone(engineering_repair_prompt('Review these blocks', original, report))
        self.assertIsNone(engineering_repair_prompt('Generate invalid examples', original, report))
        corrected = '```python\ndef f(): return 1\n```\n```python\nx=1\n```'
        self.assertTrue(accept_engineering_repair(original, corrected, validate_generated_code(corrected)))
        dropped = '```python\nx=1\n```'
        self.assertFalse(accept_engineering_repair(original, dropped, validate_generated_code(dropped)))
        disguised = '```text\ndef f(\n```\n```python\nx=1\n```'
        self.assertFalse(accept_engineering_repair(original, disguised, validate_generated_code(disguised)))

    def test_explicit_shader_request_covers_generic_fence_without_guessing_stage(self):
        shader = '```glsl\n#version 450\nlayout(location=0) out vec4 color;\nvoid main(){color=vec4(1.0);}\n```'
        self.assertIsNone(requested_shader_stage('Explain the fragment in this C++ code'))
        self.assertIsNone(requested_shader_stage('Generate vertex and fragment GLSL shaders'))
        self.assertIsNone(requested_shader_stage('Generate a GLSL shader'))
        self.assertEqual(validate_generated_code(shader)['results'][0]['status'], 'unverified')
        self.assertEqual(validate_generated_code(shader, prompt='Generate vertex and fragment GLSL shaders')['results'][0]['status'], 'unverified')
        if not find_tool('glslangValidator'):
            self.skipTest('glslang unavailable; no shader pass claimed')
        checked = validate_generated_code(shader, prompt='Generate a GLSL 450 fragment shader')
        self.assertEqual(checked['results'][0]['status'], 'passed', checked)
        self.assertEqual(checked['results'][0]['shader_stage'], 'frag')
        self.assertEqual(checked['results'][0]['stage_source'], 'explicit user request')
        broken = shader.replace('vec4(1.0)', 'undeclared_value')
        self.assertEqual(validate_generated_code(broken, prompt='Generate GLSL fragment shaders')['results'][0]['status'], 'failed')
