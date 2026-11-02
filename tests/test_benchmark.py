import json
import tempfile
import unittest
from pathlib import Path
from takumi.benchmark import suite, coverage, run_one, evaluate


class BenchmarkTests(unittest.TestCase):
    def test_case_catalog_and_guarded_word_rubric(self):
        self.assertEqual(len(suite()['cases']),10)
        self.assertEqual(coverage('DMA tag and a memory barrier.', [['tag'],['dma'],['barrier']])['matched'],3)
        self.assertEqual(coverage('an unrelated result', [['dma'],['barrier']])['matched'],0)

    def test_real_stream_timing_is_measured_not_fabricated(self):
        ticks=iter([1.,1.4,2.])
        class Feed:
            def __enter__(self): return self
            def __exit__(self,*args):return False
            def __iter__(self):
                return iter([b'{"message":{"content":"DMA tag"},"done":false}',
                             b'{"done":true,"eval_count":10,"eval_duration":1000000000,"prompt_eval_count":12}'])
        def opener(req,timeout):
            body=json.loads(req.data)
            self.assertEqual(body['model'],'sumi-1.0:latest')
            self.assertTrue(body['stream'])
            self.assertEqual(req.full_url,'http://127.0.0.1:11434/api/chat')
            return Feed()
        got=run_one('sumi-1.0:latest','GPU DMA',post=opener,clock=lambda:next(ticks))
        self.assertEqual(got['time_to_first_token_ms'],400)
        self.assertEqual(got['wall_ms'],1000)
        self.assertEqual(got['tokens_per_second'],10.)
        self.assertTrue(got['finished'])

    def test_report_is_not_claimed_to_prove_correctness(self):
        def runner(m,p):
            return {'model':m,'answer':'DMA, test, cache, GPU','time_to_first_token_ms':12,'wall_ms':30,
                    'tokens_per_second':25.2,'finished':True}
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'report.json'
            r=evaluate(['sumi-1.0:latest'],1,dest,runner=runner)
            self.assertTrue(dest.exists())
            self.assertIn('not correctness',r['disclaimer'])
            self.assertEqual(r['summaries'][0]['completed'],1)

if __name__=='__main__': unittest.main()
