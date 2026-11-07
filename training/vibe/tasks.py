"""Curated coding tasks for Sumi: original fixtures, MIT, no copied source.

Each task is a tiny real project with visible tests the model can see and hidden
tests it never does. Families do not overlap across splits, so a held-out score
measures transfer rather than recall. A reference edit enters the training set
only after real toolchains show that the hidden tests fail before it and pass
after it (see dataset.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

CSPROJ = '''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable><ImplicitUsings>enable</ImplicitUsings><TreatWarningsAsErrors>false</TreatWarningsAsErrors></PropertyGroup>
</Project>
'''
CS_MAIN = '''foreach (var type in typeof(App.Marker).Assembly.GetTypes())
    if (type.Name.EndsWith("Tests")) type.GetMethod("Run")!.Invoke(null, null);
namespace App { public class Marker { } }
'''
CPP_MAIN = 'int main() { return 0; }\n'
CPP_CHECK = '#include <cstdio>\n#include <cstdlib>\n#define CHECK(x) do { if (!(x)) { std::printf("%s:%d: error: check failed: %s\\n", __FILE__, __LINE__, #x); std::exit(1); } } while (0)\n'


@dataclass(frozen=True)
class Task:
    id: str
    split: str
    family: str
    platform: str
    runner: str  # python | node | dotnet | cpp | java
    prompt: str
    files: dict
    visible: dict
    hidden: dict
    solution: str
    support: dict = field(default_factory=dict)


def _support(runner: str) -> dict:
    return {'dotnet': {'App.csproj': CSPROJ, 'Program.cs': CS_MAIN}, 'cpp': {'main.cpp': CPP_MAIN, 'check.h': CPP_CHECK}}.get(runner, {})


def edit(path: str, search: str, replace: str) -> str:
    return f'{path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE'


def py(tid, split, family, prompt, source, visible, hidden, solution, *, path='app.py', platform='python'):
    return Task(tid, split, family, platform, 'python', prompt, {path: source} if source is not None else {}, {'test_visible.py': visible}, {'test_hidden.py': hidden}, solution)


def js(tid, split, family, prompt, source, visible, hidden, solution, *, path='app.mjs', platform='web', extra=None):
    files = {path: source, **(extra or {})}
    return Task(tid, split, family, platform, 'node', prompt, files, {'visible.test.mjs': visible}, {'hidden.test.mjs': hidden}, solution)


NODE_T = "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
READ = "import { readFileSync } from 'node:fs';\n"


def pytest(body):
    return 'import unittest\nfrom app import *\nclass T(unittest.TestCase):\n' + ''.join('    ' + line + '\n' for line in body.split('\n'))


def cs(tid, split, family, prompt, source, visible, hidden, solution, *, path='Lib.cs'):
    def wrap(name, body):
        return 'using System;\nusing App;\nstatic class ' + name + ' {\n  static void Eq<T>(T a, T b) { if (!Equals(a, b)) throw new Exception($"expected {b}, got {a}"); }\n  public static void Run() {\n    ' + body + '\n  }\n}\n'
    return Task(tid, split, family, 'dotnet', 'dotnet', prompt, {path: source}, {'VisibleTests.cs': wrap('VisibleTests', visible)}, {'HiddenTests.cs': wrap('HiddenTests', hidden)}, solution, _support('dotnet'))


def cpp(tid, split, family, prompt, source, visible, hidden, solution, *, path='lib.h'):
    def wrap(body):
        return '#include "check.h"\n#include "lib.h"\nstatic int run = [] {\n  ' + body + '\n  return 0;\n}();\n'
    return Task(tid, split, family, 'cpp', 'cpp', prompt, {path: source}, {'visible_test.cpp': wrap(visible)}, {'hidden_test.cpp': wrap(hidden)}, solution, _support('cpp'))


def java(tid, split, family, prompt, source, visible, hidden, solution, *, path='Lib.java'):
    def wrap(name, body):
        return 'public class ' + name + ' {\n  static void eq(Object a, Object b) { if (!java.util.Objects.equals(a, b)) throw new AssertionError("expected " + b + ", got " + a); }\n  public static void main(String[] args) {\n    ' + body + '\n  }\n}\n'
    return Task(tid, split, family, 'android', 'java', prompt, {path: source}, {'Tests.java': wrap('Tests', visible)}, {'HiddenTests.java': wrap('HiddenTests', hidden)}, solution)


MANIFEST_READ = "import unittest\nimport xml.etree.ElementTree as ET\nA='{http://schemas.android.com/apk/res/android}'\nroot=ET.parse('AndroidManifest.xml').getroot()\nclass T(unittest.TestCase):\n"
PLIST_READ = "import unittest, plistlib\ndata=plistlib.load(open('Info.plist','rb'))\nclass T(unittest.TestCase):\n"
PLIST = '''<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key>
  <string>dev.takumi.sample</string>
  <key>CFBundleName</key>
  <string>Sample</string>
</dict>
</plist>
'''
SERVICE_MANIFEST = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android">
  <application android:label="Sample">
    <activity android:name=".MainActivity" />
  </application>
</manifest>
'''

TASKS: list[Task] = [
    # ---- train: python ----
    py('py-window-sum', 'train', 'sliding-window', 'window_sum drops the last window. Fix it.',
       'def window_sum(items, size):\n    return [sum(items[i:i + size]) for i in range(len(items) - size)]\n',
       pytest("def test_basic(self): self.assertEqual(window_sum([1, 2, 3], 2), [3, 5])"),
       pytest("def test_full(self): self.assertEqual(window_sum([4, 5], 2), [9])\ndef test_large(self): self.assertEqual(window_sum([1], 3), [])"),
       edit('app.py', '    return [sum(items[i:i + size]) for i in range(len(items) - size)]', '    return [sum(items[i:i + size]) for i in range(len(items) - size + 1)]')),
    py('py-dedupe', 'train', 'order-preserving', 'Implement dedupe so it removes repeats and keeps first-seen order.',
       'def dedupe(items):\n    raise NotImplementedError\n',
       pytest("def test_basic(self): self.assertEqual(dedupe([2, 1, 2]), [2, 1])"),
       pytest("def test_empty(self): self.assertEqual(dedupe([]), [])\ndef test_strings(self): self.assertEqual(dedupe(['b', 'a', 'b', 'c', 'a']), ['b', 'a', 'c'])"),
       edit('app.py', '    raise NotImplementedError', '    return list(dict.fromkeys(items))')),
    py('py-import-error', 'train', 'missing-import', 'The tests fail with NameError: name \'math\' is not defined. Fix the cause.',
       'def circle_area(r):\n    return math.pi * r * r\n',
       pytest("def test_unit(self): self.assertAlmostEqual(circle_area(1), 3.14159, places=4)"),
       pytest("def test_two(self): self.assertAlmostEqual(circle_area(2), 12.56637, places=4)"),
       edit('app.py', 'def circle_area(r):', 'import math\n\n\ndef circle_area(r):')),
    py('py-backoff', 'train', 'retry-policy', 'Implement backoff(attempt, base, cap): base doubled per attempt starting at attempt 0, never above cap.',
       'def backoff(attempt, base, cap):\n    return base\n',
       pytest("def test_grows(self): self.assertEqual(backoff(2, 1, 100), 4)"),
       pytest("def test_first(self): self.assertEqual(backoff(0, 3, 100), 3)\ndef test_cap(self): self.assertEqual(backoff(9, 1, 30), 30)"),
       edit('app.py', '    return base', '    return min(cap, base * 2 ** attempt)')),
    # ---- train: web ----
    js('js-query', 'train', 'query-string', 'parseQuery must decode percent escapes and plus signs in keys and values.',
       "export function parseQuery(text) {\n  const out = {};\n  for (const part of text.replace(/^\\?/, '').split('&')) {\n    if (!part) continue;\n    const [key, value = ''] = part.split('=');\n    out[key] = value;\n  }\n  return out;\n}\n",
       NODE_T + "import { parseQuery } from './app.mjs';\ntest('value', () => assert.deepEqual(parseQuery('?a=1%202'), { a: '1 2' }));\n",
       NODE_T + "import { parseQuery } from './app.mjs';\ntest('plus', () => assert.deepEqual(parseQuery('q=a+b'), { q: 'a b' }));\ntest('key', () => assert.deepEqual(parseQuery('x%20y=1'), { 'x y': '1' }));\n",
       edit('app.mjs', "    out[key] = value;", "    const decode = (s) => decodeURIComponent(s.replace(/\\+/g, ' '));\n    out[decode(key)] = decode(value);")),
    js('js-bytes', 'train', 'unit-format', 'formatBytes should use KB and MB (1024 based) with one decimal once the value reaches them.',
       "export function formatBytes(n) {\n  return `${n} B`;\n}\n",
       NODE_T + "import { formatBytes } from './app.mjs';\ntest('kb', () => assert.equal(formatBytes(2048), '2.0 KB'));\n",
       NODE_T + "import { formatBytes } from './app.mjs';\ntest('b', () => assert.equal(formatBytes(1023), '1023 B'));\ntest('mb', () => assert.equal(formatBytes(1572864), '1.5 MB'));\n",
       edit('app.mjs', "  return `${n} B`;", "  if (n < 1024) return `${n} B`;\n  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;\n  return `${(n / 1048576).toFixed(1)} MB`;")),
    js('js-escape', 'train', 'html-escape', 'escapeHtml misses single and double quotes, and it escapes & last, which corrupts the output. Fix it.',
       "export function escapeHtml(text) {\n  return text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/&/g, '&amp;');\n}\n",
       NODE_T + "import { escapeHtml } from './app.mjs';\ntest('tag', () => assert.equal(escapeHtml('<b>'), '&lt;b&gt;'));\n",
       NODE_T + "import { escapeHtml } from './app.mjs';\ntest('amp', () => assert.equal(escapeHtml('a&b'), 'a&amp;b'));\ntest('quotes', () => assert.equal(escapeHtml(`\"'`), '&quot;&#39;'));\n",
       edit('app.mjs', "  return text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/&/g, '&amp;');", "  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');")),
    # ---- train: pwa ----
    js('pwa-manifest-icons', 'train', 'install-manifest', 'The app cannot be installed. Add the 192x192 and 512x512 PNG icons (icon-192.png, icon-512.png) to the manifest.',
       '{\n  "name": "Notes",\n  "start_url": "/",\n  "display": "standalone",\n  "icons": []\n}\n',
       NODE_T + READ + "const m = JSON.parse(readFileSync('manifest.webmanifest', 'utf8'));\ntest('has icons', () => assert.ok(m.icons.length >= 2));\n",
       NODE_T + READ + "const m = JSON.parse(readFileSync('manifest.webmanifest', 'utf8'));\ntest('sizes', () => { const s = m.icons.map(i => i.sizes); assert.ok(s.includes('192x192') && s.includes('512x512')); });\ntest('files', () => assert.deepEqual(m.icons.map(i => i.src).sort(), ['icon-192.png', 'icon-512.png']));\ntest('kept', () => assert.equal(m.display, 'standalone'));\n",
       edit('manifest.webmanifest', '  "icons": []', '  "icons": [\n    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png" },\n    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png" }\n  ]'),
       path='manifest.webmanifest', platform='pwa'),
    js('pwa-register', 'train', 'sw-registration', 'Make index.html link manifest.webmanifest and register /sw.js when the browser supports service workers.',
       '<!doctype html>\n<html>\n<head>\n  <title>Notes</title>\n</head>\n<body>\n  <main id="app"></main>\n</body>\n</html>\n',
       NODE_T + READ + "const h = readFileSync('index.html', 'utf8');\ntest('manifest', () => assert.match(h, /rel=\"manifest\"/));\n",
       NODE_T + READ + "const h = readFileSync('index.html', 'utf8');\ntest('register', () => assert.match(h, /serviceWorker\\.register\\(['\"]\\/sw\\.js['\"]\\)/));\ntest('guard', () => assert.match(h, /['\"]serviceWorker['\"] in navigator/));\ntest('href', () => assert.match(h, /href=\"(\\/)?manifest\\.webmanifest\"/));\n",
       edit('index.html', '  <title>Notes</title>', '  <title>Notes</title>\n  <link rel="manifest" href="manifest.webmanifest">') + '\n\n' +
       edit('index.html', '  <main id="app"></main>', "  <main id=\"app\"></main>\n  <script>\n    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');\n  </script>"),
       path='index.html', platform='pwa'),
    js('pwa-cache-key', 'train', 'cache-versioning', 'Old caches are never deleted. staleCaches must return every cache name except the current one.',
       "export const CURRENT = 'app-v3';\nexport function staleCaches(names) {\n  return [];\n}\n",
       NODE_T + "import { staleCaches } from './app.mjs';\ntest('old', () => assert.deepEqual(staleCaches(['app-v2', 'app-v3']), ['app-v2']));\n",
       NODE_T + "import { staleCaches } from './app.mjs';\ntest('none', () => assert.deepEqual(staleCaches(['app-v3']), []));\ntest('many', () => assert.deepEqual(staleCaches(['a', 'app-v3', 'b']), ['a', 'b']));\n",
       edit('app.mjs', '  return [];', '  return names.filter((name) => name !== CURRENT);'), platform='pwa'),
    # ---- train: .NET ----
    cs('cs-undefined', 'train', 'undefined-name', "The build fails with CS0103: The name 'total' does not exist in the current context. Fix the cause.",
       'namespace App;\npublic static class Lib {\n  public static int Sum(int[] items) {\n    foreach (var item in items) total += item;\n    return total;\n  }\n}\n',
       'Eq(Lib.Sum(new[] { 1, 2 }), 3);', 'Eq(Lib.Sum(new int[0]), 0);\n    Eq(Lib.Sum(new[] { -1, 5, 7 }), 11);',
       edit('Lib.cs', '    foreach (var item in items) total += item;', '    var total = 0;\n    foreach (var item in items) total += item;')),
    cs('cs-top', 'train', 'linq-ranking', 'TopN must return the n largest values, largest first.',
       'using System.Linq;\nnamespace App;\npublic static class Lib {\n  public static int[] TopN(int[] items, int n) => items.Take(n).ToArray();\n}\n',
       'Eq(string.Join(",", Lib.TopN(new[] { 1, 3, 2 }, 2)), "3,2");', 'Eq(Lib.TopN(new[] { 1 }, 5).Length, 1);\n    Eq(Lib.TopN(new[] { 5, 9, 9 }, 1)[0], 9);',
       edit('Lib.cs', '=> items.Take(n).ToArray();', '=> items.OrderByDescending(x => x).Take(n).ToArray();')),
    cs('cs-null', 'train', 'null-guard', 'Initials throws on null or blank names. Return an empty string for those, and uppercase initials of each word otherwise.',
       'namespace App;\npublic static class Lib {\n  public static string Initials(string? name) {\n    return string.Concat(name!.Split(" ").Select(p => p[0]));\n  }\n}\n',
       'Eq(Lib.Initials("ada lovelace"), "AL");', 'Eq(Lib.Initials(null), "");\n    Eq(Lib.Initials("   "), "");\n    Eq(Lib.Initials("  grace   hopper "), "GH");',
       edit('Lib.cs', '    return string.Concat(name!.Split(" ").Select(p => p[0]));', '    if (string.IsNullOrWhiteSpace(name)) return "";\n    return string.Concat(name.Split(\' \', StringSplitOptions.RemoveEmptyEntries).Select(p => char.ToUpperInvariant(p[0])));')),
    # ---- train: C++ ----
    cpp('cpp-include', 'train', 'missing-header', "The build fails with: error: 'string' in namespace 'std' does not name a type. Fix the cause.",
       '#pragma once\ninline std::string greet(const std::string& name) { return "hi " + name; }\n',
       'CHECK(greet("a") == "hi a");', 'CHECK(greet("") == "hi ");',
       edit('lib.h', '#pragma once', '#pragma once\n#include <string>')),
    cpp('cpp-ring', 'train', 'ring-index', 'next_index must wrap to 0 at capacity instead of running off the end.',
       '#pragma once\n#include <cstddef>\ninline std::size_t next_index(std::size_t i, std::size_t capacity) { return i + 1; }\n',
       'CHECK(next_index(0, 4) == 1);\n  CHECK(next_index(3, 4) == 0);', 'CHECK(next_index(0, 1) == 0);\n  CHECK(next_index(6, 8) == 7);',
       edit('lib.h', '{ return i + 1; }', '{ return (i + 1) % capacity; }')),
    cpp('cpp-trim', 'train', 'string-trim', 'Implement trim: remove leading and trailing spaces and tabs.',
       '#pragma once\n#include <string>\ninline std::string trim(const std::string& s) { return s; }\n',
       'CHECK(trim("  a ") == "a");', 'CHECK(trim("") == "");\n  CHECK(trim(" \\t ") == "");\n  CHECK(trim("a b") == "a b");',
       edit('lib.h', '{ return s; }', '{\n  const auto first = s.find_first_not_of(" \\t");\n  if (first == std::string::npos) return "";\n  const auto last = s.find_last_not_of(" \\t");\n  return s.substr(first, last - first + 1);\n}')),
    # ---- train: Android ----
    java('java-page', 'train', 'pagination', 'pageCount must round up: 10 items at 3 per page is 4 pages.',
       'public class Lib {\n  public static int pageCount(int items, int perPage) {\n    return items / perPage;\n  }\n}\n',
       'eq(Lib.pageCount(10, 3), 4);', 'eq(Lib.pageCount(0, 3), 0);\n    eq(Lib.pageCount(9, 3), 3);',
       edit('Lib.java', '    return items / perPage;', '    return (items + perPage - 1) / perPage;')),
    java('java-state', 'train', 'ui-state-machine', 'Implement next so Loading goes to Ready on success and Error on failure, and every other state stays put.',
       'public class Lib {\n  public static String next(String state, boolean ok) {\n    return state;\n  }\n}\n',
       'eq(Lib.next("Loading", true), "Ready");', 'eq(Lib.next("Loading", false), "Error");\n    eq(Lib.next("Ready", false), "Ready");',
       edit('Lib.java', '    return state;', '    if (!state.equals("Loading")) return state;\n    return ok ? "Ready" : "Error";')),
    py('android-exported', 'train', 'manifest-exported', 'The app fails to install on Android 12 because MainActivity has an intent-filter without android:exported. Make it launchable.',
       '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n  <application android:label="Sample">\n    <activity android:name=".MainActivity">\n      <intent-filter>\n        <action android:name="android.intent.action.MAIN" />\n        <category android:name="android.intent.category.LAUNCHER" />\n      </intent-filter>\n    </activity>\n  </application>\n</manifest>\n',
       MANIFEST_READ + "    def test_parse(self): self.assertEqual(root.tag, 'manifest')\n",
       MANIFEST_READ + "    def test_exported(self): self.assertEqual(root.find('.//activity').get(A+'exported'), 'true')\n    def test_filter(self): self.assertIsNotNone(root.find('.//intent-filter'))\n",
       edit('AndroidManifest.xml', '    <activity android:name=".MainActivity">', '    <activity android:name=".MainActivity" android:exported="true">'), path='AndroidManifest.xml', platform='android'),
    py('android-permission', 'train', 'manifest-permission', 'The app crashes on network calls. Declare the INTERNET permission.',
       SERVICE_MANIFEST,
       MANIFEST_READ + "    def test_parse(self): self.assertEqual(root.tag, 'manifest')\n",
       MANIFEST_READ + "    def test_permission(self): self.assertIn('android.permission.INTERNET', [p.get(A+'name') for p in root.findall('uses-permission')])\n    def test_app(self): self.assertIsNotNone(root.find('application'))\n",
       edit('AndroidManifest.xml', '  <application android:label="Sample">', '  <uses-permission android:name="android.permission.INTERNET" />\n  <application android:label="Sample">'), path='AndroidManifest.xml', platform='android'),
    # ---- train: iOS ----
    py('ios-camera', 'train', 'plist-privacy', 'The app is killed when it opens the camera. Add a camera usage description: "Scan documents".',
       PLIST, PLIST_READ + "    def test_parse(self): self.assertTrue(data['CFBundleName'])\n",
       PLIST_READ + "    def test_key(self): self.assertEqual(data['NSCameraUsageDescription'], 'Scan documents')\n    def test_kept(self): self.assertEqual(data['CFBundleIdentifier'], 'dev.takumi.sample')\n",
       edit('Info.plist', '  <key>CFBundleName</key>', '  <key>NSCameraUsageDescription</key>\n  <string>Scan documents</string>\n  <key>CFBundleName</key>'), path='Info.plist', platform='ios'),
    py('ios-orientation', 'train', 'plist-orientation', 'Lock the app to portrait: add UISupportedInterfaceOrientations with only UIInterfaceOrientationPortrait.',
       PLIST, PLIST_READ + "    def test_parse(self): self.assertTrue(data['CFBundleName'])\n",
       PLIST_READ + "    def test_key(self): self.assertEqual(data['UISupportedInterfaceOrientations'], ['UIInterfaceOrientationPortrait'])\n",
       edit('Info.plist', '  <key>CFBundleName</key>', '  <key>UISupportedInterfaceOrientations</key>\n  <array>\n    <string>UIInterfaceOrientationPortrait</string>\n  </array>\n  <key>CFBundleName</key>'), path='Info.plist', platform='ios'),
    # The held-out path lookup fails when `in` is used on a number. This task
    # teaches the safe form on a different function, so the test split stays unseen.
    js('js-has', 'train', 'membership-guard', 'has throws on numbers and strings. Return whether key is a member, and return false for null.',
       "export function has(value, key) {\n  return key in value;\n}\n",
       NODE_T + "import { has } from './app.mjs';\ntest('object', () => { assert.equal(has({ a: 1 }, 'a'), true); assert.equal(has({ a: 1 }, 'b'), false); });\n",
       NODE_T + "import { has } from './app.mjs';\ntest('number', () => assert.equal(has(1, 'a'), false));\ntest('index', () => assert.equal(has('ab', '0'), true));\ntest('null', () => assert.equal(has(null, 'a'), false));\n",
       edit('app.mjs', '  return key in value;', '  if (value == null) return false;\n  return key in Object(value);')),
    # Visible checks only see that a value was written. The name itself is only
    # in the task, which is the held-out plist failure in a different family.
    py('py-channel', 'train', 'declared-identifier', 'Set CHANNEL to the reverse-DNS id app.takumi.inbox.',
       'CHANNEL = ""\n',
       pytest("def test_set(self): self.assertTrue(CHANNEL)"),
       pytest("def test_exact(self): self.assertEqual(CHANNEL, 'app.takumi.inbox')"),
       edit('app.py', 'CHANNEL = ""', 'CHANNEL = "app.takumi.inbox"')),
    py('ios-app-group', 'train', 'declared-identifier', 'Add the app group id group.dev.takumi.notes under the key AppGroupId.',
       PLIST, PLIST_READ + "    def test_key(self): self.assertIn('AppGroupId', data)\n",
       PLIST_READ + "    def test_value(self): self.assertEqual(data['AppGroupId'], 'group.dev.takumi.notes')\n    def test_kept(self): self.assertEqual(data['CFBundleIdentifier'], 'dev.takumi.sample')\n",
       edit('Info.plist', '  <key>CFBundleName</key>', '  <key>AppGroupId</key>\n  <string>group.dev.takumi.notes</string>\n  <key>CFBundleName</key>'), path='Info.plist', platform='ios'),

    # ---- dev: tuning only, never trained on ----
    py('py-chunk', 'dev', 'chunking', 'Implement chunks(items, n): split into consecutive lists of at most n items.',
       'def chunks(items, n):\n    return [items]\n', pytest("def test_basic(self): self.assertEqual(chunks([1, 2, 3], 2), [[1, 2], [3]])"),
       pytest("def test_empty(self): self.assertEqual(chunks([], 2), [])\ndef test_exact(self): self.assertEqual(chunks([1, 2], 1), [[1], [2]])"),
       edit('app.py', '    return [items]', '    return [items[i:i + n] for i in range(0, len(items), n)]')),
    js('js-semver', 'dev', 'version-compare', 'compareVersions must compare dotted versions numerically: 1.10.0 is newer than 1.9.0. Return -1, 0 or 1.',
       "export function compareVersions(a, b) {\n  return a < b ? -1 : a > b ? 1 : 0;\n}\n",
       NODE_T + "import { compareVersions } from './app.mjs';\ntest('numeric', () => assert.equal(compareVersions('1.10.0', '1.9.0'), 1));\n",
       NODE_T + "import { compareVersions } from './app.mjs';\ntest('equal', () => assert.equal(compareVersions('1.0', '1.0.0'), 0));\ntest('less', () => assert.equal(compareVersions('2.1', '10.0'), -1));\n",
       edit('app.mjs', "  return a < b ? -1 : a > b ? 1 : 0;", "  const x = a.split('.').map(Number), y = b.split('.').map(Number);\n  for (let i = 0; i < Math.max(x.length, y.length); i++) {\n    const d = (x[i] ?? 0) - (y[i] ?? 0);\n    if (d) return Math.sign(d);\n  }\n  return 0;")),
    cs('cs-clamp-page', 'dev', 'bounds-clamp', 'ClampPage must keep page within 1..last, and treat last below 1 as 1.',
       'namespace App;\npublic static class Lib {\n  public static int ClampPage(int page, int last) => page;\n}\n',
       'Eq(Lib.ClampPage(9, 3), 3);', 'Eq(Lib.ClampPage(0, 3), 1);\n    Eq(Lib.ClampPage(5, 0), 1);\n    Eq(Lib.ClampPage(2, 3), 2);',
       edit('Lib.cs', '=> page;', '=> Math.Clamp(page, 1, Math.Max(1, last));')),
    cpp('cpp-popcount', 'dev', 'bit-count', 'Implement count_bits: the number of set bits in x.',
       '#pragma once\n#include <cstdint>\ninline int count_bits(std::uint32_t x) { return 0; }\n',
       'CHECK(count_bits(7) == 3);', 'CHECK(count_bits(0) == 0);\n  CHECK(count_bits(0xFFFFFFFFu) == 32);',
       edit('lib.h', '{ return 0; }', '{\n  int n = 0;\n  for (; x; x &= x - 1) ++n;\n  return n;\n}')),

    # ---- test: held out, disjoint families, never shown in training ----
    py('py-merge-ranges', 'test', 'interval-merge', 'Implement merge_ranges: merge overlapping or touching [start, end] pairs and return them sorted.',
       'def merge_ranges(ranges):\n    return ranges\n', pytest("def test_basic(self): self.assertEqual(merge_ranges([[1, 3], [2, 4]]), [[1, 4]])"),
       pytest("def test_unsorted(self): self.assertEqual(merge_ranges([[5, 6], [1, 2]]), [[1, 2], [5, 6]])\ndef test_touch(self): self.assertEqual(merge_ranges([[1, 2], [2, 3]]), [[1, 3]])\ndef test_empty(self): self.assertEqual(merge_ranges([]), [])\ndef test_inside(self): self.assertEqual(merge_ranges([[1, 9], [2, 3]]), [[1, 9]])"),
       edit('app.py', '    return ranges', '    out = []\n    for start, end in sorted(ranges):\n        if out and start <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], end)\n        else:\n            out.append([start, end])\n    return out')),
    js('js-deep-get', 'test', 'path-lookup', 'Implement get(obj, path, fallback): follow a dotted path, returning fallback when any step is missing. Falsy values that exist must be returned.',
       "export function get(obj, path, fallback) {\n  return obj[path] ?? fallback;\n}\n",
       NODE_T + "import { get } from './app.mjs';\ntest('nested', () => assert.equal(get({ a: { b: 2 } }, 'a.b'), 2));\n",
       NODE_T + "import { get } from './app.mjs';\ntest('missing', () => assert.equal(get({ a: 1 }, 'a.b.c', 'x'), 'x'));\ntest('falsy', () => assert.equal(get({ a: { b: 0 } }, 'a.b', 9), 0));\ntest('null step', () => assert.equal(get({ a: null }, 'a.b', 'x'), 'x'));\n",
       edit('app.mjs', "  return obj[path] ?? fallback;", "  let current = obj;\n  for (const key of path.split('.')) {\n    if (current == null || !(key in Object(current))) return fallback;\n    current = current[key];\n  }\n  return current;")),
    js('pwa-offline', 'test', 'offline-routing', 'Implement chooseResponse for the service worker: return the network response if it is ok, else the cached response, else the offline page.',
       "export function chooseResponse(network, cached, offline) {\n  return network;\n}\n",
       NODE_T + "import { chooseResponse } from './app.mjs';\ntest('cache', () => assert.equal(chooseResponse(null, 'c', 'o'), 'c'));\n",
       NODE_T + "import { chooseResponse } from './app.mjs';\ntest('ok', () => { const n = { ok: true }; assert.equal(chooseResponse(n, 'c', 'o'), n); });\ntest('bad', () => assert.equal(chooseResponse({ ok: false }, 'c', 'o'), 'c'));\ntest('offline', () => assert.equal(chooseResponse(null, null, 'o'), 'o'));\n",
       edit('app.mjs', '  return network;', "  if (network && network.ok) return network;\n  return cached || offline;"), platform='pwa'),
    cs('cs-rate', 'test', 'rate-limit', 'Allow must permit at most limit calls per rolling window of windowSeconds. Timestamps arrive in order; a call exactly windowSeconds after an old one no longer counts that old one.',
       'using System.Collections.Generic;\nnamespace App;\npublic class Limiter {\n  private readonly int limit; private readonly int window;\n  private readonly Queue<int> hits = new();\n  public Limiter(int limit, int windowSeconds) { this.limit = limit; window = windowSeconds; }\n  public bool Allow(int now) { return true; }\n}\n',
       'var a = new Limiter(1, 10); Eq(a.Allow(0), true); Eq(a.Allow(1), false);',
       'var b = new Limiter(2, 10); Eq(b.Allow(0), true); Eq(b.Allow(1), true); Eq(b.Allow(2), false); Eq(b.Allow(10), true); Eq(b.Allow(10), false); Eq(b.Allow(11), true);',
       edit('Lib.cs', '{ return true; }', '{\n    while (hits.Count > 0 && now - hits.Peek() >= window) hits.Dequeue();\n    if (hits.Count >= limit) return false;\n    hits.Enqueue(now);\n    return true;\n  }')),
    cpp('cpp-transpose', 'test', 'matrix-layout', 'Implement transpose for a rows*cols matrix stored row-major in a vector; return the row-major transposed vector.',
       '#pragma once\n#include <vector>\ninline std::vector<int> transpose(const std::vector<int>& m, int rows, int cols) { return m; }\n',
       'CHECK((transpose({1, 2, 3, 4}, 2, 2) == std::vector<int>{1, 3, 2, 4}));',
       'CHECK((transpose({1, 2, 3, 4, 5, 6}, 2, 3) == std::vector<int>{1, 4, 2, 5, 3, 6}));\n  CHECK((transpose({1, 2, 3}, 3, 1) == std::vector<int>{1, 2, 3}));',
       edit('lib.h', '{ return m; }', '{\n  std::vector<int> out(m.size());\n  for (int r = 0; r < rows; ++r)\n    for (int c = 0; c < cols; ++c) out[c * rows + r] = m[r * cols + c];\n  return out;\n}')),
    java('java-undo', 'test', 'undo-stack', 'Implement the undo stack: add records a value, undo returns the current value after going back one step, and undoing past the start stays at the initial value.',
       'import java.util.ArrayDeque;\npublic class Lib {\n  private final ArrayDeque<String> past = new ArrayDeque<>();\n  private String current;\n  public Lib(String initial) { current = initial; }\n  public void add(String value) { current = value; }\n  public String undo() { return current; }\n}\n',
       'Lib a = new Lib("x"); a.add("y"); eq(a.undo(), "x");',
       'Lib b = new Lib("x"); eq(b.undo(), "x"); b.add("y"); b.add("z"); eq(b.undo(), "y"); eq(b.undo(), "x"); eq(b.undo(), "x");',
       edit('Lib.java', '  public void add(String value) { current = value; }', '  public void add(String value) { past.push(current); current = value; }') + '\n\n' +
       edit('Lib.java', '  public String undo() { return current; }', '  public String undo() {\n    if (!past.isEmpty()) current = past.pop();\n    return current;\n  }')),
    py('android-deeplink', 'test', 'manifest-deeplink', 'Add a VIEW intent-filter to MainActivity so https://takumi.dev links open it. It must be exported, with the DEFAULT and BROWSABLE categories.',
       SERVICE_MANIFEST,
       MANIFEST_READ + "    def test_parse(self): self.assertEqual(root.tag, 'manifest')\n",
       MANIFEST_READ + "    def test_all(self):\n        a = root.find('.//activity'); self.assertEqual(a.get(A+'exported'), 'true')\n        f = a.find('intent-filter'); self.assertEqual(f.find('action').get(A+'name'), 'android.intent.action.VIEW')\n        self.assertEqual({c.get(A+'name') for c in f.findall('category')}, {'android.intent.category.DEFAULT', 'android.intent.category.BROWSABLE'})\n        d = f.find('data'); self.assertEqual((d.get(A+'scheme'), d.get(A+'host')), ('https', 'takumi.dev'))\n",
       edit('AndroidManifest.xml', '    <activity android:name=".MainActivity" />', '    <activity android:name=".MainActivity" android:exported="true">\n      <intent-filter>\n        <action android:name="android.intent.action.VIEW" />\n        <category android:name="android.intent.category.DEFAULT" />\n        <category android:name="android.intent.category.BROWSABLE" />\n        <data android:scheme="https" android:host="takumi.dev" />\n      </intent-filter>\n    </activity>'), path='AndroidManifest.xml', platform='android'),
    py('ios-url-scheme', 'test', 'plist-url-scheme', 'Register the takumi:// URL scheme with CFBundleURLTypes, using the name dev.takumi.link.',
       PLIST, PLIST_READ + "    def test_parse(self): self.assertTrue(data['CFBundleName'])\n",
       PLIST_READ + "    def test_scheme(self):\n        t = data['CFBundleURLTypes'][0]; self.assertEqual(t['CFBundleURLSchemes'], ['takumi']); self.assertEqual(t['CFBundleURLName'], 'dev.takumi.link')\n",
       edit('Info.plist', '  <key>CFBundleName</key>', '  <key>CFBundleURLTypes</key>\n  <array>\n    <dict>\n      <key>CFBundleURLName</key>\n      <string>dev.takumi.link</string>\n      <key>CFBundleURLSchemes</key>\n      <array>\n        <string>takumi</string>\n      </array>\n    </dict>\n  </array>\n  <key>CFBundleName</key>'), path='Info.plist', platform='ios'),
]


# ---- Harder tasks added 2026-11-05 after the original test split informed new
# checks and repair rows. Their families appear nowhere else. `dev` selects
# training settings. `holdout2` scores a selected adapter once.
def more(task: Task, files: dict) -> Task:
    return replace(task, files={**task.files, **files})


APP_T = "import { %s } from './app.mjs';\n"

TASKS += [
    # ---- dev ----
    py('py-csv-row', 'dev', 'csv-parse', 'parse_row must follow CSV quoting: commas inside double quotes do not split, and "" inside quotes is one quote character.',
       'def parse_row(line):\n    return line.split(",")\n',
       pytest("def test_quoted(self): self.assertEqual(parse_row('a,\"b,c\"'), ['a', 'b,c'])"),
       pytest("def test_escaped(self): self.assertEqual(parse_row('\"x\"\"y\",z'), ['x\"y', 'z'])\ndef test_empty(self): self.assertEqual(parse_row(''), [''])\ndef test_blank_cell(self): self.assertEqual(parse_row('a,,b'), ['a', '', 'b'])"),
       edit('app.py', '    return line.split(",")', '    cells, cell, quoted, i = [], "", False, 0\n    while i < len(line):\n        ch = line[i]\n        if quoted and ch == \'"\' and line[i + 1:i + 2] == \'"\':\n            cell += \'"\'\n            i += 1\n        elif ch == \'"\':\n            quoted = not quoted\n        elif ch == "," and not quoted:\n            cells.append(cell)\n            cell = ""\n        else:\n            cell += ch\n        i += 1\n    cells.append(cell)\n    return cells')),
    js('js-lru', 'dev', 'eviction-cache', 'Fix LRU so reading a key makes it most recently used, overwriting a key does not evict anything else, and only the least recently used key is evicted past capacity.',
       "export class LRU {\n  constructor(capacity) {\n    this.capacity = capacity;\n    this.items = new Map();\n  }\n  get(key) {\n    return this.items.get(key);\n  }\n  set(key, value) {\n    if (this.items.size >= this.capacity) this.items.delete(this.items.keys().next().value);\n    this.items.set(key, value);\n  }\n}\n",
       NODE_T + APP_T % 'LRU' + "test('evicts', () => { const c = new LRU(1); c.set('a', 1); c.set('b', 2); assert.equal(c.get('a'), undefined); });\n",
       NODE_T + APP_T % 'LRU' + "test('refresh', () => { const c = new LRU(2); c.set('a', 1); c.set('b', 2); c.get('a'); c.set('c', 3); assert.equal(c.get('a'), 1); assert.equal(c.get('b'), undefined); });\ntest('overwrite', () => { const c = new LRU(2); c.set('a', 1); c.set('b', 2); c.set('a', 9); assert.equal(c.get('b'), 2); assert.equal(c.get('a'), 9); });\n",
       edit('app.mjs', "    return this.items.get(key);", "    if (!this.items.has(key)) return undefined;\n    const value = this.items.get(key);\n    this.items.delete(key);\n    this.items.set(key, value);\n    return value;") + '\n\n' +
       edit('app.mjs', "    if (this.items.size >= this.capacity) this.items.delete(this.items.keys().next().value);", "    if (this.items.has(key)) this.items.delete(key);\n    else if (this.items.size >= this.capacity) this.items.delete(this.items.keys().next().value);")),
    js('pwa-route', 'dev', 'sw-route-policy', "Implement route(path) for the service worker: anything under /api/ is 'network', paths ending in .js, .css or .png are 'cache-first', and everything else is 'network-first'. Ignore query strings.",
       "export function route(path) {\n  return 'network-first';\n}\n",
       NODE_T + APP_T % 'route' + "test('api', () => assert.equal(route('/api/items'), 'network'));\n",
       NODE_T + APP_T % 'route' + "test('asset', () => assert.equal(route('/app.js?v=2'), 'cache-first'));\ntest('api first', () => assert.equal(route('/api/logo.png'), 'network'));\ntest('page', () => assert.equal(route('/settings'), 'network-first'));\ntest('not suffix', () => assert.equal(route('/js'), 'network-first'));\n",
       edit('app.mjs', "  return 'network-first';", "  const clean = path.split('?')[0];\n  if (clean.startsWith('/api/')) return 'network';\n  return /\\.(js|css|png)$/.test(clean) ? 'cache-first' : 'network-first';"), platform='pwa'),
    cs('cs-bus', 'dev', 'pubsub', 'Subscribe must return an Action that removes that handler. A handler that unsubscribes itself during Publish must not break delivery to the others.',
       'using System.Collections.Generic;\nnamespace App;\npublic class Bus {\n  private readonly List<Action<int>> handlers = new();\n  public Action Subscribe(Action<int> handler) {\n    handlers.Add(handler);\n    return () => { };\n  }\n  public void Publish(int value) {\n    foreach (var handler in handlers) handler(value);\n  }\n}\n',
       'var b = new Bus(); var n = 0; var off = b.Subscribe(v => n += v); b.Publish(2); off(); b.Publish(5); Eq(n, 2);',
       'var b = new Bus(); var seen = 0; Action off = () => { }; off = b.Subscribe(v => off()); b.Subscribe(v => seen += v); b.Publish(3); b.Publish(4); Eq(seen, 7);',
       edit('Lib.cs', '    return () => { };', '    return () => handlers.Remove(handler);') + '\n\n' +
       edit('Lib.cs', '    foreach (var handler in handlers) handler(value);', '    foreach (var handler in handlers.ToArray()) handler(value);')),
    cpp('cpp-parse', 'dev', 'checked-parse', 'Implement parse_int: return the value only when the whole string is a valid int. Empty text, trailing characters and overflow return nullopt.',
       '#pragma once\n#include <optional>\n#include <string>\ninline std::optional<int> parse_int(const std::string& text) { return std::stoi(text); }\n',
       'CHECK(parse_int("42") == 42);\n  CHECK(!parse_int("4x"));', 'CHECK(!parse_int(""));\n  CHECK(parse_int("-7") == -7);\n  CHECK(!parse_int("99999999999"));\n  CHECK(!parse_int(" 1"));',
       edit('lib.h', '#include <string>', '#include <charconv>\n#include <string>') + '\n\n' +
       edit('lib.h', '{ return std::stoi(text); }', '{\n  int value = 0;\n  const char* end = text.data() + text.size();\n  const auto [ptr, error] = std::from_chars(text.data(), end, value);\n  if (error != std::errc() || ptr != end || text.empty()) return std::nullopt;\n  return value;\n}')),
    java('java-throttle', 'dev', 'time-throttle', 'allow must be true on the first call whatever the time is, then only once intervalMs has passed since the last allowed call. Denied calls must not extend the wait.',
       'public class Lib {\n  private final long intervalMs;\n  private long last = 0;\n  public Lib(long intervalMs) { this.intervalMs = intervalMs; }\n  public boolean allow(long nowMs) {\n    boolean ok = nowMs - last >= intervalMs;\n    last = nowMs;\n    return ok;\n  }\n}\n',
       'Lib a = new Lib(10); eq(a.allow(100), true); eq(a.allow(105), false);',
       'Lib b = new Lib(10); eq(b.allow(1), true); eq(b.allow(5), false); eq(b.allow(9), false); eq(b.allow(11), true);',
       edit('Lib.java', '  private long last = 0;', '  private Long last = null;') + '\n\n' +
       edit('Lib.java', '    boolean ok = nowMs - last >= intervalMs;\n    last = nowMs;\n    return ok;', '    if (last != null && nowMs - last < intervalMs) return false;\n    last = nowMs;\n    return true;')),
    py('ios-background', 'dev', 'plist-background', 'Let the app play audio and refresh in the background: add UIBackgroundModes with audio and fetch, in that order.',
       PLIST, PLIST_READ + "    def test_parse(self): self.assertTrue(data['CFBundleName'])\n",
       PLIST_READ + "    def test_modes(self): self.assertEqual(data['UIBackgroundModes'], ['audio', 'fetch'])\n    def test_kept(self): self.assertEqual(data['CFBundleName'], 'Sample')\n",
       edit('Info.plist', '  <key>CFBundleName</key>', '  <key>UIBackgroundModes</key>\n  <array>\n    <string>audio</string>\n    <string>fetch</string>\n  </array>\n  <key>CFBundleName</key>'), path='Info.plist', platform='ios'),

    # ---- holdout2: scored once, never used for selection or training ----
    more(py('py-reserve', 'holdout2', 'stock-reservation', 'reserve_all must be all or nothing: if any item in the order is short, raise ValueError and leave every stock count as it was.',
       'from inventory import Inventory\n\n\ndef reserve_all(inventory, order):\n    for item, count in order.items():\n        inventory.reserve(item, count)\n',
       pytest("def test_ok(self):\n    inv = Inventory({'a': 2})\n    reserve_all(inv, {'a': 2})\n    self.assertEqual(inv.stock['a'], 0)"),
       pytest("def test_atomic(self):\n    inv = Inventory({'a': 2, 'b': 0})\n    with self.assertRaises(ValueError):\n        reserve_all(inv, {'a': 1, 'b': 1})\n    self.assertEqual(inv.stock, {'a': 2, 'b': 0})\ndef test_unknown(self):\n    inv = Inventory({'a': 2})\n    with self.assertRaises(ValueError):\n        reserve_all(inv, {'a': 1, 'z': 1})\n    self.assertEqual(inv.stock, {'a': 2})"),
       edit('app.py', '    for item, count in order.items():\n        inventory.reserve(item, count)', '    if any(inventory.stock.get(item, 0) < count for item, count in order.items()):\n        raise ValueError("short stock")\n    for item, count in order.items():\n        inventory.reserve(item, count)')),
       {'inventory.py': 'class Inventory:\n    def __init__(self, stock):\n        self.stock = dict(stock)\n\n    def reserve(self, item, count):\n        if self.stock.get(item, 0) < count:\n            raise ValueError(item)\n        self.stock[item] -= count\n'}),
    py('py-build-order', 'holdout2', 'dependency-order', 'build_order takes {name: [dependencies]}. Return an order where dependencies come first, breaking ties alphabetically. Raise ValueError on a cycle. A dependency with no entry of its own still counts as a name.',
       'def build_order(deps):\n    return sorted(deps)\n',
       pytest("def test_chain(self): self.assertEqual(build_order({'a': ['b'], 'b': []}), ['b', 'a'])"),
       pytest("def test_ties(self): self.assertEqual(build_order({'c': [], 'b': [], 'a': ['c']}), ['b', 'c', 'a'])\ndef test_cycle(self):\n    with self.assertRaises(ValueError):\n        build_order({'a': ['b'], 'b': ['a']})\ndef test_implicit(self): self.assertEqual(build_order({'a': ['z']}), ['z', 'a'])"),
       edit('app.py', '    return sorted(deps)', '    pending = {name: set(needs) for name, needs in deps.items()}\n    for needs in deps.values():\n        for name in needs:\n            pending.setdefault(name, set())\n    order = []\n    while pending:\n        ready = sorted(name for name, needs in pending.items() if not needs)\n        if not ready:\n            raise ValueError("cycle")\n        name = ready[0]\n        order.append(name)\n        del pending[name]\n        for needs in pending.values():\n            needs.discard(name)\n    return order')),
    js('js-retry', 'holdout2', 'async-retry', 'retry(fn, attempts) must call fn up to attempts times, resolve with the first success, and reject with the last error. fn may throw synchronously or reject.',
       "export async function retry(fn, attempts) {\n  return fn();\n}\n",
       NODE_T + APP_T % 'retry' + "test('second try', async () => { let n = 0; assert.equal(await retry(async () => { if (++n < 2) throw new Error('no'); return n; }, 3), 2); });\n",
       NODE_T + APP_T % 'retry' + "test('last error', async () => { let n = 0; await assert.rejects(retry(async () => { throw new Error('e' + ++n); }, 2), /e2/); assert.equal(n, 2); });\ntest('sync throw', async () => { let n = 0; assert.equal(await retry(() => { if (++n < 2) throw new Error('x'); return 'ok'; }, 2), 'ok'); });\ntest('stops', async () => { let n = 0; await retry(() => ++n, 5); assert.equal(n, 1); });\n",
       edit('app.mjs', '  return fn();', '  let last;\n  for (let i = 0; i < attempts; i++) {\n    try {\n      return await fn();\n    } catch (error) {\n      last = error;\n    }\n  }\n  throw last;')),
    js('js-template', 'holdout2', 'template-render', 'render replaces {{name}} with the escaped value and {{{name}}} with the raw value. A missing value becomes empty text. Use escapeHtml from escape.mjs.',
       "import { escapeHtml } from './escape.mjs';\n\nexport function render(template, values) {\n  return template.replace(/\\{\\{(\\w+)\\}\\}/g, (_, name) => values[name]);\n}\n",
       NODE_T + APP_T % 'render' + "test('escaped', () => assert.equal(render('<p>{{a}}</p>', { a: '<b>' }), '<p>&lt;b&gt;</p>'));\n",
       NODE_T + APP_T % 'render' + "test('raw', () => assert.equal(render('{{{a}}}', { a: '<b>' }), '<b>'));\ntest('missing', () => assert.equal(render('[{{a}}{{{b}}}]', {}), '[]'));\ntest('both', () => assert.equal(render('{{a}} {{{a}}}', { a: '&' }), '&amp; &'));\ntest('zero', () => assert.equal(render('{{a}}', { a: 0 }), '0'));\n",
       edit('app.mjs', "  return template.replace(/\\{\\{(\\w+)\\}\\}/g, (_, name) => values[name]);", "  return template.replace(/\\{\\{\\{(\\w+)\\}\\}\\}|\\{\\{(\\w+)\\}\\}/g, (_, raw, safe) => {\n    const value = values[raw ?? safe] ?? '';\n    return raw ? String(value) : escapeHtml(String(value));\n  });"),
       extra={'escape.mjs': "export function escapeHtml(text) {\n  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');\n}\n"}),
    js('pwa-precache', 'holdout2', 'precache-diff', 'precachePlan compares old and new {url: hash} maps. add lists URLs that are new or whose hash changed. remove lists URLs that no longer exist. Both are sorted. A changed URL is only in add.',
       "export function precachePlan(oldFiles, newFiles) {\n  return { add: Object.keys(newFiles), remove: [] };\n}\n",
       NODE_T + APP_T % 'precachePlan' + "test('new', () => assert.deepEqual(precachePlan({}, { '/a': '1' }), { add: ['/a'], remove: [] }));\n",
       NODE_T + APP_T % 'precachePlan' + "test('same', () => assert.deepEqual(precachePlan({ '/a': '1' }, { '/a': '1' }), { add: [], remove: [] }));\ntest('changed removed', () => assert.deepEqual(precachePlan({ '/a': '1', '/c': '1', '/b': '1' }, { '/a': '2' }), { add: ['/a'], remove: ['/b', '/c'] }));\n",
       edit('app.mjs', "  return { add: Object.keys(newFiles), remove: [] };", "  const add = Object.keys(newFiles).filter((url) => oldFiles[url] !== newFiles[url]).sort();\n  const remove = Object.keys(oldFiles).filter((url) => !(url in newFiles)).sort();\n  return { add, remove };"), platform='pwa'),
    js('pwa-scope', 'holdout2', 'app-scope', 'This app is served from /notes/, but its manifest can open outside it. Set scope and start_url to /notes/ and add one shortcut named New note that opens /notes/new.',
       '{\n  "name": "Notes",\n  "start_url": "/",\n  "display": "standalone",\n  "icons": [\n    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png" },\n    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png" }\n  ]\n}\n',
       NODE_T + READ + "const m = JSON.parse(readFileSync('manifest.webmanifest', 'utf8'));\ntest('start', () => assert.equal(m.start_url, '/notes/'));\n",
       NODE_T + READ + "const m = JSON.parse(readFileSync('manifest.webmanifest', 'utf8'));\ntest('scope', () => assert.equal(m.scope, '/notes/'));\ntest('shortcut', () => { assert.equal(m.shortcuts.length, 1); assert.equal(m.shortcuts[0].name, 'New note'); assert.equal(m.shortcuts[0].url, '/notes/new'); });\ntest('kept', () => assert.equal(m.icons.length, 2));\n",
       edit('manifest.webmanifest', '  "start_url": "/",', '  "start_url": "/notes/",\n  "scope": "/notes/",\n  "shortcuts": [{ "name": "New note", "url": "/notes/new" }],'), path='manifest.webmanifest', platform='pwa'),
    cs('cs-split', 'holdout2', 'decimal-split', 'Split must divide total into n shares of whole cents that add up exactly to total. Any leftover cents go one each to the earliest shares.',
       'namespace App;\npublic static class Lib {\n  public static decimal[] Split(decimal total, int n) {\n    var shares = new decimal[n];\n    Array.Fill(shares, total / n);\n    return shares;\n  }\n}\n',
       'Eq(string.Join("|", Lib.Split(10m, 2)), "5|5");',
       'var s = Lib.Split(10m, 3); Eq(s[0], 3.34m); Eq(s[1], 3.33m); Eq(s[2], 3.33m);\n    Eq(Lib.Split(0.05m, 2).Sum(), 0.05m);\n    var z = Lib.Split(0.01m, 3); Eq(z[0], 0.01m); Eq(z[1], 0m); Eq(z[2], 0m);',
       edit('Lib.cs', '    Array.Fill(shares, total / n);', '    var cents = (long)(total * 100);\n    for (var i = 0; i < n; i++) shares[i] = (cents / n + (i < cents % n ? 1 : 0)) / 100m;')),
    more(cs('cs-config', 'holdout2', 'config-layering', 'Merge combines layers in order using Layer.Read. Later layers win, a null value removes the key, and keys ignore case.',
       'using System.Collections.Generic;\nnamespace App;\npublic static class Lib {\n  public static Dictionary<string, string> Merge(params Dictionary<string, string?>[] layers) {\n    var merged = new Dictionary<string, string>();\n    foreach (var layer in layers) {\n      foreach (var pair in Layer.Read(layer)) merged[pair.Key] = pair.Value!;\n    }\n    return merged;\n  }\n}\n',
       'var a = new Dictionary<string, string?> { ["x"] = "1" }; var b = new Dictionary<string, string?> { ["x"] = "2" }; Eq(Lib.Merge(a, b)["x"], "2");',
       'var a = new Dictionary<string, string?> { ["Key"] = "1", ["gone"] = "1" }; var b = new Dictionary<string, string?> { ["KEY"] = "2", ["GONE"] = null }; var m = Lib.Merge(a, b); Eq(m["key"], "2"); Eq(m.ContainsKey("gone"), false); Eq(m.Count, 1);',
       edit('Lib.cs', '    var merged = new Dictionary<string, string>();', '    var merged = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);') + '\n\n' +
       edit('Lib.cs', '      foreach (var pair in Layer.Read(layer)) merged[pair.Key] = pair.Value!;', '      foreach (var pair in Layer.Read(layer)) {\n        if (pair.Value is null) merged.Remove(pair.Key);\n        else merged[pair.Key] = pair.Value;\n      }')),
       {'Layer.cs': 'using System.Collections.Generic;\nnamespace App;\npublic static class Layer {\n  public static IEnumerable<KeyValuePair<string, string?>> Read(Dictionary<string, string?> layer) => layer;\n}\n'}),
    cpp('cpp-split', 'holdout2', 'field-split', 'Implement split so every separator produces a field, including empty fields. An empty string is one empty field.',
       '#pragma once\n#include <string>\n#include <vector>\ninline std::vector<std::string> split(const std::string& text, char separator) { return {text}; }\n',
       'CHECK((split("a,b", \',\') == std::vector<std::string>{"a", "b"}));',
       'CHECK((split("a,,b", \',\') == std::vector<std::string>{"a", "", "b"}));\n  CHECK((split(",", \',\') == std::vector<std::string>{"", ""}));\n  CHECK((split("", \',\') == std::vector<std::string>{""}));',
       edit('lib.h', '{ return {text}; }', '{\n  std::vector<std::string> fields;\n  std::string::size_type start = 0, end;\n  while ((end = text.find(separator, start)) != std::string::npos) {\n    fields.push_back(text.substr(start, end - start));\n    start = end + 1;\n  }\n  fields.push_back(text.substr(start));\n  return fields;\n}')),
    cpp('cpp-intervals', 'holdout2', 'sorted-intervals', 'Finish IntervalSet: add merges half-open ranges [low, high) that overlap or touch, contains tests one value, and size is the number of separate ranges.',
       '#pragma once\n#include <cstddef>\n#include <map>\nclass IntervalSet {\n public:\n  void add(int low, int high) { ranges_[low] = high; }\n  bool contains(int value) const { return ranges_.count(value) > 0; }\n  std::size_t size() const { return ranges_.size(); }\n\n private:\n  std::map<int, int> ranges_;\n};\n',
       'IntervalSet s; s.add(1, 3); CHECK(s.contains(2)); CHECK(!s.contains(3));',
       'IntervalSet s; s.add(5, 7); s.add(1, 3); CHECK(s.size() == 2); s.add(3, 5); CHECK(s.size() == 1); CHECK(s.contains(6)); s.add(0, 100); CHECK(s.size() == 1); CHECK(s.contains(99)); CHECK(!s.contains(100));',
       edit('lib.h', '  void add(int low, int high) { ranges_[low] = high; }', '  void add(int low, int high) {\n    auto it = ranges_.upper_bound(low);\n    if (it != ranges_.begin() && std::prev(it)->second >= low) --it;\n    while (it != ranges_.end() && it->first <= high) {\n      low = std::min(low, it->first);\n      high = std::max(high, it->second);\n      it = ranges_.erase(it);\n    }\n    ranges_[low] = high;\n  }') + '\n\n' +
       edit('lib.h', '  bool contains(int value) const { return ranges_.count(value) > 0; }', '  bool contains(int value) const {\n    auto it = ranges_.upper_bound(value);\n    return it != ranges_.begin() && value < std::prev(it)->second;\n  }') + '\n\n' +
       edit('lib.h', '#include <map>', '#include <algorithm>\n#include <iterator>\n#include <map>')),
    java('java-history', 'holdout2', 'bounded-history', 'History keeps only the newest capacity entries. last(n) returns up to n entries, newest first.',
       'import java.util.ArrayDeque;\nimport java.util.ArrayList;\nimport java.util.List;\npublic class Lib {\n  private final int capacity;\n  private final ArrayDeque<String> items = new ArrayDeque<>();\n  public Lib(int capacity) { this.capacity = capacity; }\n  public void add(String item) { items.addLast(item); }\n  public List<String> last(int n) { return new ArrayList<>(items); }\n}\n',
       'Lib a = new Lib(5); a.add("x"); a.add("y"); eq(a.last(1).get(0), "y");',
       'Lib b = new Lib(2); b.add("1"); b.add("2"); b.add("3"); eq(b.last(9).toString(), "[3, 2]"); eq(b.last(1).toString(), "[3]"); eq(new Lib(1).last(3).size(), 0);',
       edit('Lib.java', '  public void add(String item) { items.addLast(item); }', '  public void add(String item) {\n    items.addFirst(item);\n    if (items.size() > capacity) items.removeLast();\n  }') + '\n\n' +
       edit('Lib.java', '  public List<String> last(int n) { return new ArrayList<>(items); }', '  public List<String> last(int n) {\n    List<String> all = new ArrayList<>(items);\n    return all.subList(0, Math.min(n, all.size()));\n  }')),
    py('android-sync-service', 'holdout2', 'manifest-service', 'Declare a non-exported foreground service named .SyncService with foregroundServiceType dataSync, plus the FOREGROUND_SERVICE and FOREGROUND_SERVICE_DATA_SYNC permissions Android 14 requires.',
       SERVICE_MANIFEST,
       MANIFEST_READ + "    def test_parse(self): self.assertEqual(root.tag, 'manifest')\n",
       MANIFEST_READ + "    def test_service(self):\n        s = root.find('.//service'); self.assertEqual(s.get(A+'name'), '.SyncService'); self.assertEqual(s.get(A+'exported'), 'false'); self.assertEqual(s.get(A+'foregroundServiceType'), 'dataSync')\n    def test_permissions(self):\n        names = {p.get(A+'name') for p in root.findall('uses-permission')}\n        self.assertEqual(names, {'android.permission.FOREGROUND_SERVICE', 'android.permission.FOREGROUND_SERVICE_DATA_SYNC'})\n    def test_activity(self): self.assertIsNotNone(root.find('.//activity'))\n",
       edit('AndroidManifest.xml', '  <application android:label="Sample">', '  <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />\n  <uses-permission android:name="android.permission.FOREGROUND_SERVICE_DATA_SYNC" />\n  <application android:label="Sample">') + '\n\n' +
       edit('AndroidManifest.xml', '    <activity android:name=".MainActivity" />', '    <activity android:name=".MainActivity" />\n    <service android:name=".SyncService" android:exported="false" android:foregroundServiceType="dataSync" />'), path='AndroidManifest.xml', platform='android'),
    py('ios-local-http', 'holdout2', 'plist-transport', 'Allow plain HTTP to localhost only. Add an App Transport Security exception for the localhost domain, and do not turn on arbitrary loads.',
       PLIST, PLIST_READ + "    def test_parse(self): self.assertTrue(data['CFBundleName'])\n",
       PLIST_READ + "    def test_exception(self):\n        ats = data['NSAppTransportSecurity']; self.assertNotIn('NSAllowsArbitraryLoads', ats)\n        self.assertIs(ats['NSExceptionDomains']['localhost']['NSExceptionAllowsInsecureHTTPLoads'], True)\n",
       edit('Info.plist', '  <key>CFBundleName</key>', '  <key>NSAppTransportSecurity</key>\n  <dict>\n    <key>NSExceptionDomains</key>\n    <dict>\n      <key>localhost</key>\n      <dict>\n        <key>NSExceptionAllowsInsecureHTTPLoads</key>\n        <true/>\n      </dict>\n    </dict>\n  </dict>\n  <key>CFBundleName</key>'), path='Info.plist', platform='ios'),
]
