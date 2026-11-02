import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from takumi.server import resilient_chat_events


class ModelRecoveryTests(unittest.TestCase):
    def produce(self, streamer, *, available=None, allow=True):
        return list(resilient_chat_events(streamer,'http://127.0.0.1:11434',[],
                                          'bonsai-2:27b','default',threading.Event(),{},
                                          available or ['bonsai-2:27b','bonsai-2:9b'],
                                          allow_alternative=allow,pause=lambda _: None))

    def test_retries_one_transient_error_before_any_text(self):
        attempts=[]
        def stream(url, messages, model, level, cancel, **kwargs):
            attempts.append(model)
            if len(attempts)==1: raise URLError('temporary disconnection')
            yield {'type':'delta','text':'Recovered without repeating the answer.'}
        events=self.produce(stream)
        self.assertEqual(attempts,['bonsai-2:27b','bonsai-2:27b'])
        self.assertEqual([x['text'] for x in events if x['type']=='delta'],['Recovered without repeating the answer.'])
        self.assertFalse(next(x for x in events if x['type']=='model_used')['fallback'])

    def test_switches_to_installed_smaller_model_and_discloses_it(self):
        attempts=[]
        def stream(url,messages,model,level,cancel,**kwargs):
            attempts.append((model,level))
            if model=='bonsai-2:27b':raise HTTPError(url,503,'busy',{},None)
            yield {'type':'delta','text':'Alternative response'}
        events=self.produce(stream)
        self.assertEqual(attempts,[('bonsai-2:27b','default'),('bonsai-2:27b','default'),('bonsai-2:9b','default')])
        self.assertTrue(any(x['type']=='notice' and 'smaller installed' in x['message'] for x in events))
        self.assertEqual(next(x for x in events if x['type']=='model_used')['model'],'bonsai-2:9b')

    def test_does_not_restart_half_written_answer(self):
        attempts=[]
        def stream(url,messages,model,level,cancel,**kwargs):
            attempts.append(model)
            yield {'type':'delta','text':'An incomplete answer'}
            raise URLError('connection lost after partial response')
        with self.assertRaises(URLError):self.produce(stream)
        self.assertEqual(attempts,['bonsai-2:27b'])

    def test_invalid_request_is_not_retried(self):
        attempts=[]
        def stream(url,messages,model,level,cancel,**kwargs):
            attempts.append(model)
            raise HTTPError(url,400,'bad request',{},None)
            yield None
        with self.assertRaises(HTTPError):self.produce(stream)
        self.assertEqual(attempts,['bonsai-2:27b'])

    def test_without_alternative_still_retries_primary_once(self):
        def stream(url,messages,model,level,cancel,**kwargs):
            raise URLError('offline')
            yield None
        with self.assertRaises(URLError): self.produce(stream,allow=False)

    def test_no_alternative_larger_than_primary(self):
        tries=[]
        def stream(url,messages,model,level,cancel,**kwargs):
            tries.append(model)
            raise URLError('offline')
            yield None
        with self.assertRaises(URLError):
            self.produce(stream,available=['bonsai-2:27b','qwen3:72b'])
        self.assertEqual(tries,['bonsai-2:27b','bonsai-2:27b'])

    def test_empty_output_retries_instead_of_false_success(self):
        tries=[]
        def stream(url,messages,model,level,cancel,**kwargs):
            tries.append(model)
            if len(tries)<3:
                yield {'type':'metrics','tokens_per_second':0}
            else:
                yield {'type':'delta','text':'Valid answer'}
        events=self.produce(stream)
        self.assertEqual(len(tries),3)
        self.assertEqual(next(x for x in events if x['type']=='model_used')['model'],'bonsai-2:9b')

    def test_cancellation_prevents_any_attempt(self):
        canc=threading.Event();canc.set()
        def stream(*args,**kwargs):raise AssertionError('Must not execute')
        items=list(resilient_chat_events(stream,'http://127.0.0.1:11434',[],'bonsai-2:27b','default',
                                         canc,{},['bonsai-2:27b']))
        self.assertEqual(items,[{'type':'cancelled'}])

    def test_empty_thinking_budget_changes_strategy_instead_of_repeating(self):
        levels=[]
        def stream(url,messages,model,level,cancel,**kwargs):
            levels.append(level)
            if level == 'off': yield {'type':'delta','text':'int answer(void) { return 42; }'}
            else: yield {'type':'metrics','done_reason':'length','eval_count':2400,'thinking_request':None}
        with patch('takumi.server.reasoning_profile',return_value={'verified':True,'levels':['default','off','on']}):
            events=self.produce(stream,allow=False)
        self.assertEqual(levels,['default','off'])
        self.assertTrue(any(e.get('recovery')=='thinking_budget' for e in events))
        self.assertFalse(any('connection was interrupted' in e.get('message','') for e in events))

    def test_exhaustion_does_not_override_explicit_reasoning_or_invent_switch(self):
        calls=[]
        def stream(*args,**kwargs):
            calls.append(1)
            yield {'type':'metrics','done_reason':'length'}
        with patch('takumi.server.reasoning_profile',return_value={'verified':False,'levels':['default']}):
            with self.assertRaisesRegex(ValueError,'output limit'):self.produce(stream)
        self.assertEqual(len(calls),1)
        calls.clear()
        with patch('takumi.server.reasoning_profile',return_value={'verified':True,'levels':['default','on','off']}):
            with self.assertRaises(ValueError):
                list(resilient_chat_events(stream,'local',[],'model','on',threading.Event(),{},['model']))
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
