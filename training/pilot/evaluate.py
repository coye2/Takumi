"""Fixed-budget, local, greedy model comparison against bounded AST validators."""
import hashlib
import json
from pathlib import Path
import time


def score(model, tokenizer, out, label):
    import mlx.core as mx
    from training.bonsai.smoke import evaluated_layers
    from .fixtures import TASKS, prompt, verify
    validator = Path(__file__).with_name('fixtures.py')
    validator_sha = hashlib.sha256(validator.read_bytes()).hexdigest()
    report = {'label':label, 'decoding':'greedy', 'max_generated_tokens':96,
              'validator_sha256':validator_sha, 'split':'test', 'tasks':[],
              'qualification':'Four synthetic held-out tasks; no frontier or whole-project claim'}
    for task in TASKS:
        if task[1] != 'test':
            continue
        started = time.monotonic()
        rendered = tokenizer.apply_chat_template([{'role':'user','content':prompt(task)}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False)
        ids = tokenizer.encode(rendered, add_special_tokens=False)
        if len(ids)>192:
            raise ValueError('Evaluation prompt exceeds the locked budget')
        cache = model.make_cache()
        generated=[]
        with evaluated_layers(model):
            logits = model(mx.array([ids]), cache=cache)
            mx.eval(logits, [c.state for c in cache])
        ended=False
        for _ in range(report['max_generated_tokens']):
            token=int(mx.argmax(logits[0,-1]).item())
            del logits
            if token==tokenizer.eos_token_id:
                ended=True
                break
            generated.append(token)
            logits=model(mx.array([[token]]),cache=cache)
            mx.eval(logits,[c.state for c in cache])
        text=tokenizer.decode(generated,skip_special_tokens=True)
        result=verify(text,task) if ended else {'passed':False,'checked':0,'reason':'Generation budget exhausted'}
        report['tasks'].append({'id':task[0],'project':task[2],'output':text,
            'generated_tokens':len(generated),'seconds':time.monotonic()-started,**result})
        print(f"Held-out {label}: {task[0]} {result['passed']}",flush=True)
        del cache
        mx.clear_cache()
        (Path(out)/f'heldout-{label}.json').write_text(json.dumps(report,indent=2))
    if hashlib.sha256(validator.read_bytes()).hexdigest()!=validator_sha:
        raise RuntimeError('Validator changed during evaluation')
    report['passed']=sum(t['passed'] for t in report['tasks'])
    report['total']=len(report['tasks'])
    (Path(out)/f'heldout-{label}.json').write_text(json.dumps(report,indent=2))
    return {'passed':report['passed'],'total':report['total'],'validator_sha256':validator_sha}
