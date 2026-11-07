"""Every selectable theme has a stylesheet, a swatch, and a native title-bar color."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ThemeCatalogTests(unittest.TestCase):
    def test_settings_css_and_title_bar_list_the_same_themes(self):
        server = (ROOT / 'takumi' / 'server.py').read_text(encoding='utf-8')
        allowed = re.search(r'"theme": \[(.*?)\]', server, re.S)
        self.assertIsNotNone(allowed)
        server_ids = re.findall(r'"([a-z]{2,16})"', allowed.group(1))
        app = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
        block = re.search(r'const TAKUMI_THEMES=\{(.*?)\};', app, re.S)
        self.assertIsNotNone(block)
        js_ids = re.findall(r'([a-z]{2,16}):\[', block.group(1))
        css_ids = re.findall(r"body\[data-theme='([a-z]{2,16})']", (ROOT / 'web' / 'styles.css').read_text(encoding='utf-8'))
        title = (ROOT / 'desktop' / 'Takumi.Desktop' / 'TakumiTitleBar.cs').read_text(encoding='utf-8')
        title_ids = re.findall(r'"([a-z]{2,16})" =>', title)
        self.assertEqual(set(server_ids), set(js_ids))
        self.assertEqual(len(server_ids), len(set(server_ids)))
        self.assertGreaterEqual(len(server_ids), 60)
        self.assertEqual(set(css_ids), set(server_ids) - {'porcelain'})
        self.assertEqual(set(title_ids), set(server_ids) - {'porcelain'})
        for ident in server_ids:
            self.assertRegex(ident, r'^[a-z]{2,16}$')


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return .2126 * r + .7152 * g + .0722 * b


def _contrast(one, two):
    high, low = sorted((_luminance(one), _luminance(two)), reverse=True)
    return (high + .05) / (low + .05)


class ColorThemeTests(unittest.TestCase):
    def setUp(self):
        self.app = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
        self.css = (ROOT / 'web' / 'styles.css').read_text(encoding='utf-8')
        block = re.search(r'const COLOR_THEMES=\{(.*?)\};', self.app).group(1)
        self.names = re.findall(r"([a-z]{2,16}):'", block)

    def test_includes_the_ember_theme(self):
        self.assertIn('ember', self.names)
        self.assertGreaterEqual(len(self.names), 10)

    def test_text_stays_readable_on_every_color_theme(self):
        for name in self.names:
            rule = re.search(r"body\[data-theme='%s'\]\{(.*?)\}" % name, self.css).group(1)
            values = dict(re.findall(r'--([a-z]+):(#[0-9a-f]{6})', rule))
            with self.subTest(theme=name):
                self.assertGreaterEqual(_contrast(values['ink'], values['paper']), 7)
                self.assertGreaterEqual(_contrast(values['muted'], values['paper']), 4.5)
                self.assertGreaterEqual(_contrast(values['faded'], values['paper']), 3)
                self.assertGreaterEqual(_contrast(values['rose'], values['paper']), 3)
                self.assertGreaterEqual(_contrast(values['ink'], values['bubble']), 7)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.app = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
        self.body = self.app[self.app.index('function drawSettings(){'):self.app.index("function speechTag(sample='')")]

    def test_every_tab_is_reachable_and_has_a_body(self):
        tabs = re.findall(r"\['([a-z]+)','[A-Z][a-z]+'\]", re.search(r'const tabs=\[(.*?)\];', self.body).group(1))
        self.assertEqual(set(tabs), {'appearance', 'language', 'chat', 'reasoning', 'research', 'tools', 'privacy'})
        self.assertEqual(set(re.findall(r"activeSettingsTab==='([a-z]+)'", self.body)), set(tabs))

    def test_every_control_maps_to_a_setting_the_backend_accepts_and_uses(self):
        server = (ROOT / 'takumi' / 'server.py').read_text(encoding='utf-8')
        keys = re.findall(r"(?:toggle|select)\('[^']*','([a-z_]+)'", self.body)
        self.assertIn('repair_generated_html', keys)
        for key in keys:
            with self.subTest(key=key):
                self.assertIn(f'"{key}":', server[server.index('allowed = {'):server.index('with self.lock', server.index('allowed = {'))])
        # Stored by the backend but never read, so a control for it would do nothing.
        self.assertNotIn('autonomy_passes', self.body)


class LanguageNoteTests(unittest.TestCase):
    def test_menu_does_not_claim_translation_it_does_not_have(self):
        from takumi.language import language_menu, model_instruction
        menu = {row['id']: row for row in language_menu()}
        self.assertEqual(menu['en']['chrome'], 'full')
        self.assertEqual(menu['it']['chrome'], 'partial')
        self.assertEqual({row['chrome'] for code, row in menu.items() if code not in ('en', 'it')}, {'english'})
        self.assertEqual((menu['ar']['dir'], menu['he']['dir']), ('rtl', 'rtl'))
        self.assertIn('professional Italian', model_instruction('it'))


class SavedSettingsTests(unittest.TestCase):
    def test_browser_tools_stay_on_after_a_restart(self):
        server = (ROOT / 'takumi' / 'server.py').read_text(encoding='utf-8')
        defaults = server[server.index('self.settings = {'):server.index('"send_on_enter": True')]
        self.assertIn('"browser_enabled": False', defaults)


if __name__ == '__main__':
    unittest.main()
