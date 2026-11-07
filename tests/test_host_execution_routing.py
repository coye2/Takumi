"""Phone-to-PC command intent is routed to the real coding backend."""
import unittest
from takumi.routing import choose_mode, project_preflight, needs_host_execution


class HostExecutionRoutingTests(unittest.TestCase):
    def test_actual_owner_power_shell_diagnostic_is_not_text_only_chat(self):
        request = ('Diagnose your Windows backend and confirm whether you can '
                   'execute local PowerShell commands. Run whoami, Get-Location, '
                   'Get-Command gh, gh auth status, and dotnet --info.')
        self.assertTrue(needs_host_execution(request))
        self.assertEqual(choose_mode('auto', request, True), 'agent')
        self.assertTrue(project_preflight('auto', request, False)['needs_project'])

    def test_local_gh_and_build_requests_use_work(self):
        for request in ('Run PowerShell commands on my PC to check dotnet',
                        'Check Windows backend GitHub release permissions',
                        'Deploy a GitHub release using PowerShell on my PC'):
            with self.subTest(request=request):
                self.assertEqual(choose_mode('auto', request, True), 'agent')
                self.assertTrue(project_preflight('auto', request, False)['needs_project'])

    def test_explicit_ask_stays_read_only_and_generic_questions_are_chat(self):
        request = 'Diagnose Windows backend PowerShell connectivity'
        self.assertEqual(choose_mode('chat', request, True), 'chat')
        self.assertFalse(project_preflight('chat', request, False)['needs_project'])
        self.assertEqual(choose_mode('auto', 'Why does PowerShell work this way?', True), 'chat')
        self.assertEqual(choose_mode('auto', 'Review this GitHub pull request', True), 'chat')
        self.assertEqual(choose_mode('auto', 'What is a diagnosis?', True), 'chat')

    def test_project_never_implicitly_authorized_by_a_path_in_prompt(self):
        request = 'Run PowerShell on my PC from C:\\Takumi-Dev'
        self.assertTrue(project_preflight('auto', request, False)['needs_project'])


if __name__ == '__main__':
    unittest.main()
