"""Original MIT specialist kernel curriculum; no game binaries or copied answers.

Separate from v1 so its published result remains reproducible. Test families
never enter the exported training messages. These are arithmetic microtasks,
not matching-decompilation, whole-project, or frontier-model qualifications.
"""
import ast
import hashlib
import json
from pathlib import Path
import random
from .fixtures import TASKS as BASIC, compile_candidate

VERSION = 'verified-specialist-pilot-v1'
# id, split, family, contract, arguments, expression, independent oracle
TASKS = [t for t in BASIC if t[1] == 'train'] + [
    ('pc-relative', 'train', 'branch-displacement',
     'Signed 16-bit displacement d is encoded unsigned. Return pc+4+4*displacement.',
     'pc,d', 'pc+4+4*((d&32767)-(d&32768))',
     lambda pc,d: pc+4+4*int.from_bytes(d.to_bytes(2,'little'),'little',signed=True)),
    ('align-up', 'train', 'alignment',
     'Round nonnegative address x up to positive power-of-two alignment a.',
     'x,a', '(x+a-1)&-a', lambda x,a: ((x//a)+(x%a!=0))*a),
    ('page-offset', 'train', 'page-translation',
     'Return byte offset within a 4096-byte page for unsigned address x.',
     'x', 'x&4095', lambda x: x%4096),
    ('page-address', 'train', 'page-translation',
     'Translate unsigned virtual address x using physical page number p. Page size is 4096.',
     'x,p', '(p<<12)|(x&4095)', lambda x,p: p*4096+x%4096),
    ('saturate-s8', 'train', 'saturating-arithmetic',
     'Saturate signed integer sum a+b to signed 8-bit range.',
     'a,b', 'min(127,max(-128,a+b))', lambda a,b: sorted([-128,a+b,127])[1]),
    ('field5', 'train', 'instruction-field',
     'Extract five bits starting at bit n of unsigned 32-bit instruction x. n is 0..27.',
     'x,n', '(x>>n)&31', lambda x,n: (x//(2**n))%32),
    ('struct-offset', 'train', 'record-layout',
     'Records start at base with stride s. Return byte address of field offset o in record i.',
     'base,s,i,o', 'base+s*i+o', lambda base,s,i,o: sum([base]+[s]*i+[o])),
    ('relocation', 'train', 'relocation-addend',
     'Return low 32 bits of symbol address s plus signed addend a minus relocation place p.',
     's,a,p', '(s+a-p)&4294967295', lambda s,a,p: (s+a-p)%4294967296),
    ('ring-count', 'test', 'ring-buffer',
     'Unsigned 8-bit monotonic counters w,r encode a ring occupancy below 256. Return occupancy.',
     'w,r', '(w-r)&255', lambda w,r: (w+256-r)%256),
    ('interval-overlap', 'test', 'memory-intervals',
     'Return overlap byte count of half-open ranges [a,a+n) and [b,b+m). n,m are nonnegative.',
     'a,n,b,m', 'max(0,min(a+n,b+m)-max(a,b))',
     lambda a,n,b,m: len(set(range(a,a+n)) & set(range(b,b+m)))),
    ('mips-jump', 'test', 'jump-reconstruction',
     'Rebuild MIPS jump address from unsigned 32-bit pc and unsigned 26-bit target t.',
     'pc,t', '((pc+4)&4026531840)|(t<<2)',
     lambda pc,t: (((pc+4)%4294967296)//268435456)*268435456+t*4),
    ('packed-s24', 'test', 'audio-pcm',
     'Decode signed little-endian 24-bit PCM from unsigned bytes a,b,c.',
     'a,b,c', 'a+(b<<8)+((c&127)<<16)-((c&128)<<16)',
     lambda a,b,c: int.from_bytes(bytes([a,b,c]),'little',signed=True)),
    ('remaining-bytes', 'test', 'parser-bounds',
     'Given buffer length n and offset o, return remaining bytes, or -1 if o<0 or o>n.',
     'n,o', '-1 if o<0 else (-1 if o>n else n-o)',
     lambda n,o: n-o if o in range(n+1) else -1),
    ('stack-slot', 'test', 'stack-abi',
     'Return number of 16-byte stack slots needed for n nonnegative bytes.',
     'n', '(n+15)//16', lambda n: len(range(0,n,16))),
]


def cases(task):
    uid = task[0]
    if uid in {t[0] for t in BASIC}:
        from .fixtures import cases as basic_cases
        yield from basic_cases(task)
        return
    rng = random.Random(uid)
    edges = [0,1,127,128,255,256,32767,32768,65535]
    for i in range(512):
        x = edges[i] if i < len(edges) else rng.getrandbits(32)
        if uid == 'pc-relative': yield (rng.getrandbits(32), x%65536)
        elif uid == 'align-up': yield (x, 1 << (i%16))
        elif uid == 'page-offset': yield (x,)
        elif uid == 'page-address': yield (x, rng.randrange(65536))
        elif uid == 'saturate-s8': yield (i-256, rng.randrange(-256,256))
        elif uid == 'field5': yield (x, i%28)
        elif uid == 'struct-offset': yield (x, rng.randrange(1,128), i%32, i%8)
        elif uid == 'relocation': yield (x, rng.randrange(-65536,65536), rng.getrandbits(32))
        elif uid == 'ring-count': yield (i%256, (i//256)*255)
        elif uid == 'interval-overlap': yield (i%32, i%17, rng.randrange(32), rng.randrange(17))
        elif uid == 'mips-jump': yield ([0,0x0ffffffc,0xfffffffc,0xffffffff][i%4] if i<16 else x, rng.getrandbits(26))
        elif uid == 'packed-s24': yield (i%256, rng.randrange(256), i%256)
        elif uid == 'remaining-bytes': yield (i%32, i%36-2)
        elif uid == 'stack-slot': yield (i,)
        else: raise ValueError('Unknown task: '+uid)


def verify(text, task):
    checked = 0
    try:
        candidate = compile_candidate(text, len(task[4].split(',')))
        for args in cases(task):
            if candidate(*args) != task[6](*args):
                return {'passed': False, 'checked': checked, 'reason': 'semantic mismatch'}
            checked += 1
        if not checked: raise ValueError('Empty validation set')
        return {'passed': True, 'checked': checked}
    except Exception as exc:
        return {'passed': False, 'checked': checked, 'reason': str(exc)[:150]}


def validator_source_sha256() -> str:
    """Use canonical LF source bytes; Git for Windows may check out CRLF."""
    source = Path(__file__).read_bytes().replace(b'\r\n', b'\n')
    return hashlib.sha256(source).hexdigest()


def validate_dataset(dataset):
    """Fail closed before training if fixture provenance or split was changed."""
    from .fixtures import prompt
    expected = {t[0]:t for t in TASKS if t[1] == 'train'}
    if dataset.get('version') != VERSION or dataset.get('validator_sha256') != validator_source_sha256():
        raise ValueError('Specialist dataset version or validator hash mismatch; regenerate it')
    records = dataset.get('examples', [])
    if len(records) != len(expected) or {r.get('id') for r in records} != set(expected):
        raise ValueError('Specialist training split mismatch')
    for record in records:
        task = expected[record['id']]
        messages = record.get('messages', [])
        if (record.get('license') != 'MIT' or record.get('task_family') != task[2]
                or len(messages) != 2 or messages[0] != {'role':'user','content':prompt(task)}
                or messages[1].get('role') != 'assistant'
                or not verify(messages[1].get('content', ''), task)['passed']):
            raise ValueError('Unverified specialist example: '+task[0])


def write_dataset(path):
    from .fixtures import prompt
    examples, audit = [], []
    train_families = {t[2] for t in TASKS if t[1] == 'train'}
    if train_families & {t[2] for t in TASKS if t[1] != 'train'}:
        raise ValueError('Train/test family overlap')
    seen = set()
    for task in TASKS:
        code = 'def f('+task[4]+'):\n    return '+task[5]
        result = verify(code, task)
        if not result['passed']: raise ValueError((task[0], result))
        digest = hashlib.sha256(ast.dump(ast.parse(code)).encode()).hexdigest()
        if digest in seen: raise ValueError('Duplicate reference')
        seen.add(digest)
        audit.append({'id':task[0], 'split':task[1], **result})
        if task[1] == 'train':
            examples.append({'id':task[0], 'project':task[2], 'task_family':task[2],
                'license':'MIT', 'verification':result,
                'messages':[{'role':'user','content':prompt(task)}, {'role':'assistant','content':code}]})
    report = {'version':VERSION, 'purpose':'Specialist arithmetic pilot, not whole-project qualification',
        'license':'MIT', 'source':'Original synthetic fixtures; no private source or game data',
        'generator':'Codex', 'validator_sha256':validator_source_sha256(),
        'examples':examples}
    validate_dataset(report)
    Path(path).write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return audit


if __name__ == '__main__':
    import sys
    print(json.dumps(write_dataset(sys.argv[1]), indent=2))
