# Release status

**1.5.1-preview.1 · 2026-11-02**

This update adds phone layout/reconnection, theme-aware chrome, an experimental
exact-model trainer and an optional browser connector. It is not a trained-model
release or a claim of frontier parity or general Windows UI control.

Phone layout was checked at 320×568, 390×844 and reduced 375×420 viewport sizes.
Private HTTPS pairing and authenticated API access were exercised. A paired user
device is registered. Physical iPhone/Android status-bar rendering, keyboard,
lock/unlock and cellular handoff remain unverified. OS/browser versions may
override requested status-bar colors.

The browser connector successfully connected through the installed OpenCode
1.19.5 engine. Autonomous model-driven browser task success is not qualified.
The base model has completed a four-token forward; the exact-model training
path remains experimental, memory-constrained and not deployable as a qualified
adapter. See [setup and limits](BROWSER-AND-TRAINING.md).

## Previous preview evidence



The development build ran on Windows 11 with an Intel Core i7-14700F, 32 GB RAM and RTX 4060 Ti 16 GB. The installed runtime was Ollama 0.40.13, with the Sumi 1.0 profile over a 26.9B Q2_0 model. Native WebView2 startup and actual local generation were exercised.

The prior full Python suite ran 182 tests: 177 passed and five environment-dependent tests were skipped. The final communication change also passed 26 targeted tests, frontend syntax checks and Markdown regressions. Public-export checks and its separately built native artifact are recorded in the GitHub release notes and CI.

Readability tests improved a long review from 761 words to three paragraphs totaling 173 words. This is one observed comparison, not a general benchmark. An independently checked generated alignment function passed 7,004 checks. A detailed ring-buffer explanation failed both its requested word limit and technical/runtime checks. These failures remain part of the qualification record.

## Known limitations

- Model explanations and code can be wrong. In particular, source reviews can overstate historical documents as current facts. Evidence links identify sources; they do not prove conclusions.
- GitHub review reads selected, bounded source excerpts and branch heads. It is not an exhaustive latest-work audit or a build/test run.
- Vague project requests that omit repository/GitHub wording can still take the wrong research route. Public search can return irrelevant results; Reddit access was blocked on the development PC.
- Source syntax checks do not establish linking, ABI behavior, runtime correctness or gameplay.
- Rei's physical 8 GB GPU target, private phone access, Figma OAuth, Discord endurance and long unattended goals still need broader qualification.
- The source-based installer and updater have recovery logic and automated tests; a clean-machine upgrade/rollback matrix has not been completed for this public preview.
- Voice depends on browser capabilities and may use external services. The desktop is not a full remote-control environment.

## Next priorities

Improve research intent/relevance, calibrate source-review claims, expand executable engineering evaluations, and qualify installation, lower-memory hardware and external integrations. Contributions with reproducible failures and measured results are welcome.
