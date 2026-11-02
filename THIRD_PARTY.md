# Third-party components and distribution

Takumi is distributed as original source code or a compiled **Takumi** desktop host. The repository and release artifacts must not bundle Ollama, OpenCode, Claude, Codex, model weights, firmware, keys, game files or other proprietary payloads. The Windows installer optionally fetches and installs official inference and coding engines **onto the end user's PC**, outside the Takumi package. It also downloads model weights from the upstream registry on that PC. No third-party engines or model weights are bundled into the distributable ZIP.

- **Ollama** — local model inference service, MIT license; https://github.com/ollama/ollama. Takumi communicates over documented loopback endpoints. The inference runtime and model weights are downloaded on the user's PC at setup and *not* copied into Takumi's release.
- **OpenCode** — optional autonomous coding engine, MIT license; https://github.com/anomalyco/opencode. Takumi launches the independent local CLI and consumes structured output. The executable and source are *not* vendored here.
- **Microsoft .NET 10 runtime and Windows Forms** — used to compile Takumi's own Windows desktop host. If published self-contained, Microsoft .NET runtime files may be included inside the Takumi executable subject to Microsoft's applicable licenses. https://dotnet.microsoft.com/
- **Microsoft Edge WebView2 SDK/runtime** — the embedded UI engine on Windows; NuGet package `Microsoft.Web.WebView2` version `1.0.4314.35`. The WebView2 Evergreen runtime is installed separately; the SDK may contribute its licensed native loader to published output. Follow Microsoft's distribution and license terms: https://developer.microsoft.com/en-us/microsoft-edge/webview2/
- **Python 3.11+** — required to run Takumi's local standard-library service. Python runtime is installed separately rather than bundled in Takumi's ZIP. https://python.org/
- **Tailscale** — optional private PC-to-phone network connection, separately installed with its own terms. https://tailscale.com/
- **GitHub CLI / Figma MCP** — optional integrations using the user's own existing authentication. No credentials are copied to a release.
- **Go** — used only to compile Takumi's own `Takumi-Setup.exe`. No Go runtime installation is needed to run the compiled setup executable.
- **LLVM/Clang and Khronos glslang** — separately installed, optional source-index/syntax tools. No compiler executables are bundled in the Takumi ZIP. Clang is discovered on PATH or in the standard Windows LLVM directory; glslang is discovered on PATH, in a Vulkan SDK or in the user's separately installed `LocalAppData/Takumi/Tools/glslang/bin`. Upstream source, notices and licenses: https://github.com/llvm/llvm-project and https://github.com/KhronosGroup/glslang. A syntax pass does not establish target dependency, ABI, linking or runtime correctness.

- **Bonsai 2 27B / Sumi 1.0 base** — Sumi 1.0 is a local application profile referencing the separately installed `bonsai-2:27b` upstream model published by Prism ML. The upstream model is listed as Apache-2.0; verify its distributed model card and any third-party terms before redistribution of weights. Takumi redistributes **neither model tensors nor upstream model executables**. See https://ollama.com/library/bonsai-2:27b and https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf. Naming Sumi 1.0 does not imply training or independent ownership of the base weights.

The Takumi logo and supplied screenshots are owned/provided by the project owner. See `BRANDING.md`; they are not automatically MIT-licensed. For redistributed third-party modified software, each upstream license continues to apply independently.


### Rei 1.0 model profile

Rei 1.0 uses separately downloaded Prism ML Bonsai 2 9B weights (`bonsai-2:9b`, Apache-2.0), not included in the Takumi ZIP. See https://ollama.com/library/bonsai-2:9b and the upstream model license for attribution and redistribution terms. Takumi's Modelfile customizes instructions and context settings only; it does not train or distribute independent model weights.
