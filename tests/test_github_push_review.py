import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from takumi.plugins import Approvals


class GitHubPushReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.git('init','-b','feature/website')
        self.git('config','user.name','Fixture');self.git('config','user.email','fixture@example.invalid')
        (self.root/'index.html').write_text('<main>Website</main>')
        self.git('add','index.html');self.git('commit','-m','Fixture')
        self.git('remote','add','origin','https://github.com/example/website.git')
        self.approvals=Approvals()

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL,text=True).strip()

    def test_review_pins_object_and_destination_without_network(self):
        head=self.git('rev-parse','HEAD')
        prepared=self.approvals.prepare('github','push',self.root,{})
        self.assertIn(head+':refs/heads/feature/website',prepared['command_preview'])
        self.assertIn('example/website',prepared['title'])
        self.git('remote','set-url','origin','https://github.com/example/other.git')
        with patch('takumi.plugins._run',return_value={'ok':True}) as run:
            self.approvals.execute(prepared['approval_id'],local_only=True)
        self.assertIn('https://github.com/example/website.git',run.call_args.args[0])
        with self.assertRaises(ValueError):
            self.approvals.execute(prepared['approval_id'],local_only=True)

    def test_dirty_and_non_github_destinations_are_not_published(self):
        (self.root/'index.html').write_text('Uncommitted')
        with self.assertRaisesRegex(ValueError,'commit'):
            self.approvals.prepare('github','push',self.root,{})
        self.git('add','index.html');self.git('commit','-m','Update')
        self.git('remote','set-url','origin','https://example.invalid/other.git')
        with self.assertRaisesRegex(ValueError,'GitHub'):
            self.approvals.prepare('github','push',self.root,{})
