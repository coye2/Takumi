"""Small starter projects whose checks work from the first commit.

A new project with a test from the start gives the verified loop something real
to run. Every template here is original and contains no dependencies to fetch
except what the platform's own build tool requires.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

PLATFORMS = {'web': 'Web app', 'pwa': 'Installable PWA', 'dotnet': '.NET console app', 'cpp': 'C++ with CMake',
             'android': 'Android app', 'ios': 'iOS Swift package', 'go': 'Go program', 'rust': 'Rust program'}
# Plain 1x1 transparent PNG; replace with real artwork.
PIXEL = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                      '1f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082')
NODE_PACKAGE = {'private': True, 'type': 'module', 'scripts': {'test': 'node --test'}}
WEB_PAGE = '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>{name}</title>{head}\n</head>\n<body>\n  <main id="app"></main>\n  <script type="module" src="app.js"></script>{body}\n</body>\n</html>\n'
WEB_LOGIC = 'export function greeting(name) {\n  return `Hello, ${name}`;\n}\n'
WEB_APP = "import { greeting } from './logic.js';\n\ndocument.querySelector('#app').textContent = greeting('world');\n"
WEB_TEST = "import test from 'node:test';\nimport assert from 'node:assert/strict';\nimport { greeting } from './logic.js';\n\ntest('greets by name', () => assert.equal(greeting('Sumi'), 'Hello, Sumi'));\n"


def _web(name: str) -> dict:
    return {'package.json': json.dumps({'name': name.lower(), **NODE_PACKAGE}, indent=2) + '\n',
            'index.html': WEB_PAGE.format(name=name, head='', body=''), 'logic.js': WEB_LOGIC, 'app.js': WEB_APP, 'logic.test.js': WEB_TEST}


def _pwa(name: str) -> dict:
    files = _web(name)
    files['index.html'] = WEB_PAGE.format(name=name, head='\n  <link rel="manifest" href="manifest.webmanifest">',
                                          body="\n  <script>\n    if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');\n  </script>")
    files['manifest.webmanifest'] = json.dumps({'name': name, 'short_name': name[:12], 'start_url': '.', 'display': 'standalone', 'background_color': '#ffffff', 'theme_color': '#ffffff',
                                                'icons': [{'src': 'icon-192.png', 'sizes': '192x192', 'type': 'image/png'}, {'src': 'icon-512.png', 'sizes': '512x512', 'type': 'image/png'}]}, indent=2) + '\n'
    files['sw.js'] = ("const CACHE = 'app-v1';\nconst FILES = ['.', 'index.html', 'app.js', 'logic.js', 'manifest.webmanifest'];\n\n"
                      "self.addEventListener('install', (event) => event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES))));\n"
                      "self.addEventListener('activate', (event) => event.waitUntil(caches.keys().then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n))))));\n"
                      "self.addEventListener('fetch', (event) => event.respondWith(fetch(event.request).catch(() => caches.match(event.request))));\n")
    files['icon-192.png'] = PIXEL
    files['icon-512.png'] = PIXEL
    return files


def _dotnet(name: str) -> dict:
    return {f'{name}.csproj': '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n    <OutputType>Exe</OutputType>\n    <TargetFramework>net10.0</TargetFramework>\n    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n  </PropertyGroup>\n  <ItemGroup>\n    <Compile Remove="tests/**" />\n  </ItemGroup>\n</Project>\n',
            'Program.cs': 'Console.WriteLine(Greeter.Greeting("world"));\n\npublic static class Greeter\n{\n    public static string Greeting(string name) => $"Hello, {name}";\n}\n',
            f'tests/{name}.Tests.csproj': f'<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n    <OutputType>Exe</OutputType>\n    <TargetFramework>net10.0</TargetFramework>\n    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n  </PropertyGroup>\n  <ItemGroup>\n    <ProjectReference Include="../{name}.csproj" />\n  </ItemGroup>\n</Project>\n',
            'tests/Program.cs': 'if (Greeter.Greeting("Sumi") != "Hello, Sumi")\n{\n    Console.Error.WriteLine("error: wrong greeting");\n    return 1;\n}\nreturn 0;\n'}


def _cpp(name: str) -> dict:
    return {'CMakeLists.txt': f'cmake_minimum_required(VERSION 3.20)\nproject({name} LANGUAGES CXX)\nset(CMAKE_CXX_STANDARD 20)\nset(CMAKE_CXX_STANDARD_REQUIRED ON)\nenable_testing()\nadd_library(core src/greeting.cpp)\ntarget_include_directories(core PUBLIC src)\nadd_executable(app src/main.cpp)\ntarget_link_libraries(app PRIVATE core)\nadd_executable(core_test tests/core_test.cpp)\ntarget_link_libraries(core_test PRIVATE core)\nadd_test(NAME core_test COMMAND core_test)\n',
            'src/greeting.h': '#pragma once\n#include <string>\n\nstd::string greeting(const std::string& name);\n',
            'src/greeting.cpp': '#include "greeting.h"\n\nstd::string greeting(const std::string& name) { return "Hello, " + name; }\n',
            'src/main.cpp': '#include <iostream>\n#include "greeting.h"\n\nint main() {\n  std::cout << greeting("world") << "\\n";\n}\n',
            'tests/core_test.cpp': '#include <cstdio>\n#include "greeting.h"\n\nint main() {\n  if (greeting("Sumi") != "Hello, Sumi") {\n    std::puts("error: wrong greeting");\n    return 1;\n  }\n}\n'}


def _go(name: str) -> dict:
    module = 'dev.takumi.' + name.lower()
    return {'go.mod': f'module {module}\n\ngo 1.22\n',
            'main.go': 'package main\n\nimport "fmt"\n\nfunc Greeting(name string) string {\n\treturn "Hello, " + name\n}\n\nfunc main() {\n\tfmt.Println(Greeting("world"))\n}\n',
            'greeting_test.go': 'package main\n\nimport "testing"\n\nfunc TestGreeting(t *testing.T) {\n\tgot := Greeting("Sumi")\n\tif got != "Hello, Sumi" {\n\t\tt.Fatalf("Greeting(%q) = %q, want %q", "Sumi", got, "Hello, Sumi")\n\t}\n}\n'}


def _rust(name: str) -> dict:
    package = name.lower()
    return {'Cargo.toml': f'[package]\nname = "{package}"\nversion = "0.1.0"\nedition = "2021"\n',
            'src/main.rs': 'fn greeting(name: &str) -> String {\n    format!("Hello, {name}")\n}\n\nfn main() {\n    println!("{}", greeting("world"));\n}\n\n#[cfg(test)]\nmod tests {\n    use super::greeting;\n\n    #[test]\n    fn greets_by_name() {\n        assert_eq!(greeting("Sumi"), "Hello, Sumi");\n    }\n}\n'}


def _android(name: str) -> dict:
    package = 'dev.takumi.' + name.lower()
    path = package.replace('.', '/')
    return {'settings.gradle.kts': f'pluginManagement {{\n    repositories {{ google(); mavenCentral(); gradlePluginPortal() }}\n}}\ndependencyResolutionManagement {{\n    repositories {{ google(); mavenCentral() }}\n}}\nrootProject.name = "{name}"\ninclude(":app")\n',
            'build.gradle.kts': 'plugins {\n    id("com.android.application") version "8.13.0" apply false\n    id("org.jetbrains.kotlin.android") version "2.2.20" apply false\n}\n',
            'app/build.gradle.kts': f'plugins {{\n    id("com.android.application")\n    id("org.jetbrains.kotlin.android")\n}}\n\nandroid {{\n    namespace = "{package}"\n    compileSdk = 36\n    defaultConfig {{\n        applicationId = "{package}"\n        minSdk = 26\n        targetSdk = 36\n        versionCode = 1\n        versionName = "1.0"\n    }}\n}}\n\ndependencies {{\n    testImplementation("junit:junit:4.13.2")\n}}\n',
            'app/src/main/AndroidManifest.xml': f'<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n    <application android:label="{name}">\n        <activity android:name=".MainActivity" android:exported="true">\n            <intent-filter>\n                <action android:name="android.intent.action.MAIN" />\n                <category android:name="android.intent.category.LAUNCHER" />\n            </intent-filter>\n        </activity>\n    </application>\n</manifest>\n',
            f'app/src/main/java/{path}/MainActivity.kt': f'package {package}\n\nimport android.app.Activity\nimport android.os.Bundle\nimport android.widget.TextView\n\nclass MainActivity : Activity() {{\n    override fun onCreate(state: Bundle?) {{\n        super.onCreate(state)\n        setContentView(TextView(this).apply {{ text = greeting("world") }})\n    }}\n}}\n\nfun greeting(name: String) = "Hello, $name"\n',
            f'app/src/test/java/{path}/GreetingTest.kt': f'package {package}\n\nimport org.junit.Assert.assertEquals\nimport org.junit.Test\n\nclass GreetingTest {{\n    @Test fun greetsByName() = assertEquals("Hello, Sumi", greeting("Sumi"))\n}}\n'}


def _ios(name: str) -> dict:
    return {'Package.swift': f'// swift-tools-version:5.9\nimport PackageDescription\n\nlet package = Package(\n    name: "{name}",\n    platforms: [.iOS(.v17), .macOS(.v14)],\n    targets: [\n        .target(name: "{name}"),\n        .testTarget(name: "{name}Tests", dependencies: ["{name}"]),\n    ]\n)\n',
            f'Sources/{name}/Greeting.swift': 'public func greeting(_ name: String) -> String {\n    "Hello, \\(name)"\n}\n',
            f'Tests/{name}Tests/GreetingTests.swift': f'import XCTest\n@testable import {name}\n\nfinal class GreetingTests: XCTestCase {{\n    func testGreetsByName() {{\n        XCTAssertEqual(greeting("Sumi"), "Hello, Sumi")\n    }}\n}}\n',
            'Info.plist': f'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n<dict>\n  <key>CFBundleIdentifier</key>\n  <string>dev.takumi.{name.lower()}</string>\n  <key>CFBundleName</key>\n  <string>{name}</string>\n</dict>\n</plist>\n'}


BUILDERS = {'web': _web, 'pwa': _pwa, 'dotnet': _dotnet, 'cpp': _cpp, 'android': _android, 'ios': _ios, 'go': _go, 'rust': _rust}
IGNORE = 'node_modules/\nbin/\nobj/\nbuild/\ntarget/\n.gradle/\n.build/\n.takumi-build/\n*.user\n'


def create(platform: str, folder: Path, name: str = '') -> list[str]:
    """Create a starter project in an empty or new folder. Never overwrites."""
    if platform not in BUILDERS:
        raise ValueError('Choose one of: ' + ', '.join(PLATFORMS))
    folder = Path(folder)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError('Choose an empty or new folder; existing files are never overwritten')
    name = re.sub(r'[^A-Za-z0-9]', '', name or folder.name)
    if not name or not name[0].isalpha():
        name = 'App' + name
    files = BUILDERS[platform](name) | {'.gitignore': IGNORE}
    for relative, body in files.items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding='utf-8', newline='\n')
    return sorted(files)
