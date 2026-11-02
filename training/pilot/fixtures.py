"""MIT-authored small engineering pilot. Validators never execute candidate code.

Partitions are fixed by synthetic project and bug family before generation.
These elementary tasks may be in pretraining; this is not a frontier benchmark.
"""
import ast
import ctypes
import hashlib
import itertools
import json
import operator
from pathlib import Path
import random


# id, split, project, contract, arguments, reference expression, independent oracle
TASKS = [
    ('carry32', 'train', 'cpu-flags', 'Return unsigned 32-bit addition carry (0 or 1). Inputs a,b are unsigned 32-bit.', 'a,b', '(a+b)>>32', lambda a,b: int(a > 0xffffffff-b)),
    ('overflow32', 'train', 'cpu-flags', 'Return signed 32-bit addition overflow (0 or 1). Inputs a,b are unsigned bit patterns.', 'a,b', '((~(a^b)&(a^((a+b)&4294967295)))>>31)&1', lambda a,b: int(not -2147483648 <= ctypes.c_int32(a).value+ctypes.c_int32(b).value <= 2147483647)),
    ('borrow32', 'train', 'cpu-flags', 'Return unsigned 32-bit subtraction borrow for a-b (0 or 1). Inputs a,b are unsigned 32-bit.', 'a,b', 'int(a<b)', lambda a,b: int(a-b < 0)),
    ('sign8', 'train', 'byte-cpu', 'Sign-extend unsigned byte x to a signed Python integer.', 'x', '(x&127)-(x&128)', lambda x: int.from_bytes(bytes([x]), 'little', signed=True)),
    ('swap16', 'train', 'byte-cpu', 'Swap the two bytes of unsigned 16-bit x.', 'x', '((x&255)<<8)|(x>>8)', lambda x: int.from_bytes(x.to_bytes(2,'little'),'big')),
    ('little16', 'train', 'byte-cpu', 'Decode unsigned little-endian 16-bit value from bytes lo,hi.', 'lo,hi', 'lo|(hi<<8)', lambda lo,hi: int.from_bytes(bytes([lo,hi]),'little')),
    ('rotate8', 'train', 'byte-cpu', 'Rotate unsigned byte x right by n bits. n is nonnegative; wrap the rotation.', 'x,n', '((x>>(n%8))|(x<<(8-n%8)))&255', lambda x,n: int((format(x,'08b')*2)[8-n%8:16-n%8],2)),
    ('shift8', 'train', 'byte-cpu', 'Arithmetic right shift an unsigned byte bit-pattern x by n (0..7), returning a signed integer.', 'x,n', '((x&127)-(x&128))>>n', lambda x,n: ctypes.c_int8(x).value//(2**n)),
    ('clamp', 'dev', 'bounds', 'Clamp integer x into inclusive bounds lo,hi; lo<=hi.', 'x,lo,hi', 'min(max(x,lo),hi)', lambda x,lo,hi: sorted([x,lo,hi])[1]),
    ('ceildiv', 'dev', 'division', 'Return ceil(n/d) for nonnegative integer n and positive d.', 'n,d', '(n+d-1)//d', lambda n,d: n//d+int(n%d!=0)),
    ('rgba-red', 'test', 'pixel-formats', 'Extract the red byte from unsigned packed RGBA8888 x, with red in bits 24..31.', 'x', '(x>>24)&255', lambda x: x.to_bytes(4,'big')[0]),
    ('rgb565-red', 'test', 'pixel-formats', 'Expand the red component of unsigned RGB565 x to 0..255, rounding to nearest.', 'x', '(((x>>11)&31)*255+15)//31', lambda x: round(int(format(x,'016b')[:5],2)*255/31)),
    ('blend8', 'test', 'pixel-blending', 'Blend 8-bit fg over bg with 8-bit alpha a; divide by 255 and round to nearest integer.', 'fg,bg,a', '(fg*a+bg*(255-a)+127)//255', lambda fg,bg,a: round((fg*a+bg*(255-a))/255)),
    ('tile8', 'test', 'texture-layout', 'Index pixel x,y in row-major 8x8 tiles. Image width w is positive; include partial edge tiles.', 'x,y,w', '((y//8)*((w+7)//8)+x//8)*64+(y%8)*8+x%8', lambda x,y,w: ((y//8)*len(range(0,w,8))+x//8)*64+list(itertools.product(range(8),range(8))).index((y%8,x%8))),
]

OPS = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul,
       ast.FloorDiv:operator.floordiv, ast.Mod:operator.mod, ast.BitAnd:operator.and_,
       ast.BitOr:operator.or_, ast.BitXor:operator.xor, ast.LShift:operator.lshift, ast.RShift:operator.rshift}
COMPARE = {ast.Lt:operator.lt, ast.LtE:operator.le, ast.Gt:operator.gt, ast.GtE:operator.ge, ast.Eq:operator.eq, ast.NotEq:operator.ne}


def compile_candidate(text, arity):
    if len(text) > 5000:
        raise ValueError('Candidate too large')
    if text.strip().startswith('```'):
        text = text.strip().split('\n',1)[1].rsplit('```',1)[0]
    tree = ast.parse(text)
    if len(list(ast.walk(tree))) > 256 or len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError('Return one function with a single return expression')
    f=tree.body[0]
    if f.name!='f' or f.decorator_list or len(f.body)!=1 or not isinstance(f.body[0],ast.Return):
        raise ValueError('Expected function f with a single return')
    if len(f.args.args)!=arity or f.args.defaults or f.args.kwonlyargs or f.args.vararg or f.args.kwarg or f.args.posonlyargs:
        raise ValueError('Unexpected signature')
    compile(tree,'candidate','exec')  # Syntax only: never exec/eval candidate Python.
    names=[a.arg for a in f.args.args]
    def visit(node, env):
        if isinstance(node,ast.Constant) and type(node.value) in (int,bool):r=node.value
        elif isinstance(node,ast.Name):r=env[node.id]
        elif isinstance(node,ast.BinOp) and type(node.op) in OPS:
            left,right=visit(node.left,env),visit(node.right,env)
            if isinstance(node.op,(ast.LShift,ast.RShift)) and not 0<=right<=64:raise ValueError('Unbounded shift')
            r=OPS[type(node.op)](left,right)
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd,ast.Invert)):
            v=visit(node.operand,env);r=-v if isinstance(node.op,ast.USub) else +v if isinstance(node.op,ast.UAdd) else ~v
        elif isinstance(node,ast.Compare) and len(node.ops)==1 and type(node.ops[0]) in COMPARE:
            r=COMPARE[type(node.ops[0])](visit(node.left,env),visit(node.comparators[0],env))
        elif isinstance(node,ast.IfExp):r=visit(node.body if visit(node.test,env) else node.orelse,env)
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('min','max','int','abs') and not node.keywords and 1<=len(node.args)<=3:
            r={'min':min,'max':max,'int':int,'abs':abs}[node.func.id](*[visit(a,env) for a in node.args])
        else:raise ValueError('Operation outside the published bounded-expression contract')
        if not isinstance(r,(int,bool)) or abs(r).bit_length()>128:raise ValueError('Result exceeds integer bound')
        return r
    return lambda *args:visit(f.body[0].value,dict(zip(names,args)))


def cases(task):
    uid=task[0];rng=random.Random(uid)
    for i in range(256):
        if uid in ('carry32','overflow32','borrow32'):
            edges=[0,1,2147483647,2147483648,4294967295]
            yield (edges[i//5],edges[i%5]) if i<25 else (rng.getrandbits(32),rng.getrandbits(32))
        elif uid=='sign8':yield (i,)
        elif uid in ('swap16','rgb565-red'):yield (i*257,)
        elif uid=='little16':yield (i,255-i)
        elif uid in ('rotate8','shift8'):yield (i,rng.randrange(8 if uid=='shift8' else 32))
        elif uid=='clamp':yield (i-128,-40,80)
        elif uid=='ceildiv':yield (i,rng.randrange(1,50))
        elif uid=='rgba-red':yield (rng.getrandbits(32),)
        elif uid=='blend8':yield (i,255-i,rng.randrange(256))
        elif uid=='tile8':
            w=rng.randrange(1,40);yield (rng.randrange(w),rng.randrange(40),w)


def prompt(task):
    return task[3]+' Return only Python def f('+task[4]+') with one return expression. Use integer operations; no imports.'


def verify(text, task):
    try:
        candidate=compile_candidate(text,len(task[4].split(',')))
        checked=0
        for args in cases(task):
            if candidate(*args)!=task[6](*args):return {'passed':False,'checked':checked,'reason':'semantic mismatch'}
            checked+=1
        return {'passed':True,'checked':checked}
    except Exception as exc:return {'passed':False,'checked':0,'reason':str(exc)[:150]}


def write_dataset(path):
    examples=[];audit=[];seen=set()
    for task in TASKS:
        code='def f('+task[4]+'):\n    return '+task[5]
        result=verify(code,task)
        if not result['passed']:raise ValueError((task[0],result))
        normalized=ast.dump(ast.parse(code),include_attributes=False)
        digest=hashlib.sha256(normalized.encode()).hexdigest()
        if digest in seen:raise ValueError('Duplicate fixture')
        seen.add(digest);audit.append({'id':task[0],'split':task[1],'project':task[2],**result})
        if task[1]=='train':examples.append({'id':task[0],'project':task[2],'task_family':task[2],'license':'MIT',
            'verification':result,'messages':[{'role':'user','content':prompt(task)},{'role':'assistant','content':code}]})
    report={'version':'verified-coding-pilot-v1','purpose':'Small synthetic engineering SFT pilot; not frontier qualification',
            'license':'MIT','source':'Codex-authored synthetic repository fixtures; no private project data',
            'generator':'Codex','validator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'examples':examples}
    Path(path).write_text(json.dumps(report,indent=2)+'\n')
    return audit


if __name__=='__main__':
    import sys
    print(json.dumps(write_dataset(sys.argv[1]),indent=2))
