import copy
from pathlib import Path
import struct
import tempfile
import unittest

from takumi.format_memory import FormatMemory, parse_layout, validate_schema


class FormatMemoryTests(unittest.TestCase):
    def schema(self):
        return {'name': 'FencePacket', 'revision': 1, 'endian': 'little', 'size': 32,
                'version': {'offset': 0, 'type': 'u16'}, 'fields': [
                    {'name': 'version', 'type': 'u16', 'offset': 0},
                    {'name': 'count', 'type': 'u16', 'offset': 2},
                    {'name': 'reserved', 'type': 'padding', 'offset': 4, 'count': 4, 'fill': 0},
                    {'name': 'fences', 'type': 'u32', 'offset': 8, 'count': 'count', 'align': 4},
                    {'name': 'old_flags', 'type': 'u32', 'offset': 24, 'versions': {'max': 1}},
                    {'name': 'new_flags', 'type': 'u64', 'offset': 24, 'versions': {'min': 2}}]}

    def test_versioned_endian_layout_and_independent_struct_oracle(self):
        for endian, prefix in [('little', '<'), ('big', '>')]:
            for version in (1, 2):
                spec = self.schema(); spec['endian'] = endian
                raw = struct.pack(prefix + 'HH4x4IQ', version, 4, 17, 42, 99, 123, 65537)
                report = parse_layout(spec, raw)
                fields = {f['name']: f for f in report['fields']}
                self.assertEqual(fields['fences']['value'], [17, 42, 99, 123])
                self.assertEqual(fields['fences']['offset'], 8)
                self.assertEqual(report['binary_version'], version)
                self.assertIn('old_flags' if version == 1 else 'new_flags', fields)

    def test_revisions_project_scope_restart_and_ten_consistent_followups(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d); project = base/'repo'; project.mkdir(); other = base/'other'; other.mkdir()
            memory = FormatMemory(base/'formats.sqlite'); spec = self.schema(); memory.save(project, spec)
            with self.assertRaises(ValueError): memory.save(project, spec)
            revised = copy.deepcopy(spec); revised['revision'] = 2; revised['fields'][3]['count'] = 2
            memory.save(project, revised)
            memory = FormatMemory(base/'formats.sqlite')
            self.assertEqual(memory.list(other), [])
            for _ in range(10):
                self.assertEqual(memory.get(project, 'FencePacket', 1)['fields'][3]['offset'], 8)
                self.assertEqual(memory.get(project, 'FencePacket')['revision'], 2)
            (base/'outside.bin').write_bytes(b'\0'*32)
            with self.assertRaises(ValueError): memory.inspect(project, '../outside.bin', 'FencePacket')

    def test_reject_overlap_truncation_bad_padding_and_count_overflow(self):
        spec = self.schema()
        raw = struct.pack('<HH4x4IQ', 2, 4, 17, 42, 99, 123, 65537)
        with self.assertRaises(ValueError): parse_layout(spec, raw[:25])
        with self.assertRaises(ValueError): parse_layout(spec, raw[:4] + b'\x01' + raw[5:])
        with self.assertRaises(ValueError): parse_layout(spec, raw[:2] + b'\xff\xff' + raw[4:])
        invalid = copy.deepcopy(spec); invalid['fields'][1]['offset'] = 1
        with self.assertRaises(ValueError): validate_schema(invalid)
        invalid = copy.deepcopy(spec); invalid['fields'][3]['align'] = 3
        with self.assertRaises(ValueError): validate_schema(invalid)
