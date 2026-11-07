import unittest
from takumi.routing import project_preflight, choose_mode


class ProjectPreflightTests(unittest.TestCase):
    request = ('Add this GitHub repository to my website: '
               'https://github.com/ildenteproibito/PC-Uptime, using the official GitHub logo. '
               r'Use this directory: C:\Users\Admin\Downloads\uptime website backup')

    def test_screenshot_request_needs_project_then_uses_editor(self):
        self.assertTrue(project_preflight('auto', self.request, False)['needs_project'])
        self.assertEqual(project_preflight('auto', self.request, True),
                         {'mode': 'agent', 'needs_project': False})

    def test_explicit_chat_and_questions_are_preserved(self):
        for text in [self.request, 'How do I add my GitHub repo to my website?']:
            self.assertEqual(project_preflight('chat', text, False),
                             {'mode': 'chat', 'needs_project': False})
        self.assertFalse(project_preflight('auto', 'Write a poem', False)['needs_project'])
        self.assertEqual(choose_mode('auto', 'Review my GitHub repository', True), 'chat')
        self.assertEqual(choose_mode('auto', 'How do I update this website?', True), 'chat')

    def test_edit_verbs_and_invalid_requests(self):
        for text in ['Add a link to my website', 'Edit my project files', 'Update my emulator']:
            self.assertEqual(choose_mode('auto', text, True), 'agent')
        with self.assertRaises(ValueError):
            project_preflight('invalid', self.request, True)
        with self.assertRaises(ValueError):
            project_preflight('auto', ['not text'], False)
