# Verified build

Describe a change. Sumi edits the project, the project's real build and tests run, and Sumi repairs from the errors. It is opt-in, under **Tools → Verified build**. The default Work path still runs on the installed OpenCode engine, and nothing here changes a model profile or weights.

## What a result means

| State | Meaning |
| --- | --- |
| `verified` | Every check ran, none were blocked, and a build or test step passed |
| `partially_verified` | What could run passed, but a required check is blocked or only static checks exist |
| `unverified` | Edits applied, and nothing can check them. Edits are kept |
| `failed`, `stuck`, `no_change`, `cancelled` | Your files are put back as they were |
| `model_unavailable` | The local model did not answer. Your files are put back |

With Sumi, `verified` also requires the edge-case step to pass. If that step cannot finish, its edits are undone, the rest of the change is kept, and the result is `partially_verified` with the failure named. `verified` still does not prove the change is right, because those checks may be incomplete. In the held-out run below, 2 of 8 tasks passed their visible tests and failed hidden ones. A report also flags any run that edited test files.

## Safety

- Check commands come from project files and a fixed table, never from model output. Owner-defined `takumi.tasks.json` tasks take priority.
- Edits stay inside the project. `.git`, `.ssh`, `.env` files and key files are refused. Edits to build configuration need **Allow edits to build configuration**, because that configuration decides what the checks run.
- Tests and builds still run model-written code with your permissions. Use it on projects you are willing to run.
- Environment variables that look like tokens, passwords or API keys are not passed to project checks.
- Rollback will not overwrite a file you changed during the run. It reports the conflict instead.
- A run refuses to start while training or a GPU-exclusive test holds the GPU, and training cannot start during a run.

## Platforms on the owner's PC (2026-11-05)

| Platform | Checks | Status |
| --- | --- | --- |
| Web | npm scripts, or HTML and JS syntax | Runs here (Node) |
| PWA | Above, plus manifest, icons and service worker contract | Runs here, tested end to end through the UI |
| .NET | `dotnet build`, then `dotnet test` when the project uses the test SDK, or `dotnet run` of a `*.Tests.csproj` program when it does not | The starter's greeting check passed on this Linux machine with SDK 10.0.100 (`training/evidence/2026-11-06/dotnet-starter.json`). A wrong greeting failed that check. This was not the owner's PC, where SDK 10.0.403 was recorded earlier |
| C++ | CMake and CTest, native or through WSL | The starter was configured, built, and tested on Linux with CMake and g++ (`training/evidence/2026-11-06/cpp-starter.json`). The checks report a build and a test, not a runtime step. Running the built app separately printed `Hello, world`. That was not the owner's Windows PC, and WSL was not used |
| Android | Manifest rules, Gradle unit tests and assemble | Manifest checks and Java logic with the Android Studio JDK. **Gradle and Kotlin builds not run**: the starter has no wrapper yet, so run `gradle wrapper` in it first |
| iOS apps | `Info.plist` rules, `swift`, `xcodebuild` | `Info.plist` checks run here. Compiling Swift needs macOS with Xcode, so results stay `partially_verified` |
| Go | `go build`, then `go test ./...` | The starter's greeting check passed on this Linux machine with Go 1.22.2 (`training/evidence/2026-11-06/go-starter.json`). A wrong greeting failed the test after the build passed. A `go test` comparison failure was reported at `main_test.go:8` with both `Hi, Sumi` and `Hello, Sumi` (`training/evidence/2026-11-06/go-test-failure.json`). A compile error `undefined: Missing` was reported at `main.go:4` (`training/evidence/2026-11-06/go-rust-compile.json`). This was not the owner's PC |
| Rust | `cargo build`, then `cargo test` | The starter's greeting check passed on this Linux machine with Cargo 1.83.0 (`training/evidence/2026-11-06/rust-starter.json`). A wrong greeting failed the test after the build passed, and the diagnostic named `src/main.rs` with both values. A compile error `E0425` was reported at `src/main.rs:2` (`training/evidence/2026-11-06/go-rust-compile.json`). This was not the owner's PC |

The Android and iOS starters are untested beyond their static checks. The Go and Rust starters were built and tested here; running each binary separately printed `Hello, world`. The check steps themselves report a build and a test, not a runtime step.

**Using it from an iPhone** is a separate matter and already works through Takumi's paired PWA over Tailscale Serve. A paired phone can read the panel and start or stop runs; the work runs on the PC. Creating projects and allowing build configuration edits stay PC-only. The routes are tested with Tailscale-style proxy headers (`tests/test_vibe_remote.py`); this panel has not yet been opened on a real phone.

## Context and inference on the RTX 4060 Ti 16 GB

From `training/evidence/2026-11-04/context-sweep.json`, using Sumi (Q2_0), Ollama, about half of each window filled:

| `num_ctx` | GPU | GPU memory in use | Output tok/s | First prompt | Same prefix again |
| --- | --- | --- | --- | --- | --- |
| 8,192 | 100% | 10,115 MiB | 26.6 | 4,645 tokens, 5.6 s | 1.4 s |
| 16,384 | 100% | 10,403 MiB | 25.9 | 9,165 tokens, 10.9 s | 1.5 s |
| 24,576 | 100% | 10,548 MiB | 25.3 | 13,683 tokens, 15.8 s | 0.9 s |
| 32,768 | 100% | 10,860 MiB | 24.6 | 18,204 tokens, 22.0 s | 1.0 s |

So Sumi uses 32K for this loop, and each prompt puts files and the repo map first, with changing diagnostics last. That keeps repair turns inside Ollama's prefix cache. Chat keeps its own budgets, so switching between chat and a run can reload the model. Rei stays at 8K.

**Do not raise this past 32K without a guarded test.** The 49K attempt was followed by an unclean reboot under low RAM. The cause is not established, and the PC also had three other unclean reboots that day before this work started. Batch tools refuse to start below 6 GiB of available RAM.

## Training

`training/vibe/` holds 57 original MIT tasks: 25 train, 11 dev, 8 test and 13 `holdout2`, with no shared families across splits. A reference edit enters `vibe-coding-v1.json` only after real toolchains show that the hidden tests fail before it and pass after it. Three of the train tasks also have a repair row: the messages are the second turn the loop actually sends after a visible check fails, and the supervised completion is the fix, not the failed edit. Those rows cover the two shapes behind the false `verified` results below — `key in value` after a null comparison, and a reverse-DNS name that was named in the task but never written — on families the held-out split does not use. Rebuilding keeps a previously verified row when this machine has no toolchain for it.

Training uses the existing exact-base LoRA path (`training/bonsai/smoke.py`), on the same `4f99aed0…` weights. Prompts are rendered by the same code the loop serves with. Existing suites and adapters are unchanged.

```bash
py -3 -m training.vibe.dataset build training/vibe/vibe-coding-v1.json
```

Start it from **Sumi trainer → Coding tasks · 64 steps**. Then score the recorded replies with real toolchains:

```bash
py -3 -m training.vibe.mlx_eval RUN_FOLDER
```

Limits to keep in mind:

- The wrapper needs **12 GiB of available RAM** to start, and that is not always enough. The first attempt started at 13.4 GiB and was stopped by the 2 GiB guard as the model finished loading, with a test suite also running (`training/evidence/2026-11-05/training-attempt-001.json`). Attempt 2 stored no steps: full-row logits for the 22 training rows exceeded the 6 GiB host guard, so parity checks now keep 32 positions per row. Attempt 3 ran **19 optimizer steps** on that 22-row set, then stopped because GPU display headroom fell below 1 GiB (`training/evidence/2026-11-05/training-attempt-003.json`). The partial adapter was not scored and is not a candidate. Aim for 16 GiB free, keep the display light, and run nothing else heavy. The repair rows were added after attempt 3. The first complete 64-step run trained on them and was rejected; see below.
- A trained adapter **cannot be served through Ollama yet**. Scores come from the MLX trainer, and export to the served model is unbuilt. The running Sumi does not wait on that export: when a visible check fails, the repair turn includes a short worked example from the training split (`key in Object(value)`, and copying a reverse-DNS id the task already named). Held-out answers are not in that example.
- If display memory stays under 1 GiB for three samples, the guard asks the trainer to finish the current step and save, instead of killing it on the first dip. **Continue saved run · 32 steps** loads that adapter and trains another slice of the same suite, including after repair rows were added. A different suite is refused. The saved adapter still does not replace the served model.
- Sumi is Bonsai 2 27B (`qwen35` architecture). It is not a diffusion model, so generic trainers do not apply.

### Latest training result: no gain

Three gentler settings were compared on dev under a rule fixed in advance (`training/vibe/sweep/README.md`). The evidence is `training/evidence/2026-11-06/sweep-and-holdout2.json`.

| Setting | Dev, 11 tasks | `holdout2`, 13 tasks |
| --- | --- | --- |
| Untrained Sumi | 7 | **6** |
| Learning rate 0.00003, scale 4.0, step 28 (selected) | 8 | **5** |
| Same setting, step 64 | 7 | Not run |
| Learning rate 0.0001, scale 1.0, step 28 | 7, stopped before step 64 | Not run |
| Learning rate 0.00003, scale 1.0 | Stopped during scoring | Not run |

The adapter fixed one held-out task and broke two, so it is not used. Gentler settings did remove the repetition loops of the earlier run. Thirteen tasks cannot separate 5 from 6, but nothing here supports a claim that training helps.

A first held-out attempt stopped at 7 replies, so those replies were seen before the final score. The adapter was not changed in response.

### Earlier training result: rejected

The first complete run (`training/evidence/2026-11-05/training-run-007.json`) made Sumi worse. The adapter is not deployed and the Sumi profile is unchanged.

| Measure | Untrained | After 64 steps |
| --- | --- | --- |
| Held-out tasks passed (hidden tests, one greedy reply) | **7 of 8** | **3 of 8** |
| Mean loss on the 28 training rows | 0.205 | 0.137 |

Mechanically the run was sound: frozen weights hashed the same, a zero or disabled adapter matched the base exactly, and training stayed inside every memory guard. But training loss fell while held-out work collapsed, and 4 replies ran out their budget, stuck in repetition loops. Rank 4 at scale 4.0 and learning rate 0.0003 looks too strong for a model already near its best on these tasks. That is a hypothesis, not a measured cause.

The 8 test tasks are no longer a clean measure, because checks and repair rows were modelled on two of their failures. Any further tuning should select on the dev split and report on new held-out tasks.

## Benchmarks

```bash
py -3 -m training.vibe.evaluate --split test --out report.json
```

Latest result: `training/evidence/2026-11-05/heldout-loop.json`, Sumi digest `fcf3a5e5a5af`.

| Measure | Result |
| --- | --- |
| Hidden tests passed | 6 of 8 |
| Passed: python, pwa, dotnet, cpp, android ×2 | 6 |
| Failed: `js-deep-get` (`in` on a number), `ios-url-scheme` (omitted a requested key) | 2 |
| Reached `verified` from visible tests | 8 of 8, so 2 were false confidence |
| Median time per task | 8.0 s |

These are small fixtures. All 8 finished on the first attempt, so this run **did not exercise repair**, and it says nothing about large projects or how Sumi compares with any other product.

Two visible checks were added after that run, aimed at the two false `verified` results. A JavaScript `in` used on a value that was only compared with null (`==` or `!=`) is now a static error (`key in Object(value)` is the safe form). A reverse-DNS name the task asks for, such as `dev.takumi.link`, must appear in a project file before the loop accepts the edit. The prompt renderer is unchanged. The training-set hash changed later, when the repair tasks were added; the 6 of 8 above was scored at `72de82793ba89c7531e0b281ed1c2cda888908a85683c70c854678dc0e33a53e` and these checks do not by themselves raise it.

Attempt 3 also scored the base model inside the trainer, greedy, one reply, no repair loop, on the 22-row set (`f8fbf44cc6fed637984757bca06fe34423d2933b3a40523506cb282cc16a808f`). Hidden tests passed 7 of 8. `js-deep-get` passed. `ios-url-scheme` failed. That is a different path from the loop result above, and the 19-step adapter was not the model being scored.

### New held-out tasks, through the loop

`training/evidence/2026-11-05/loop-holdout2.json` and `loop-dev.json`, scored by hidden tests. Repair fixed real Sumi failures here for the first time.

| Tasks | First reply | With repair | Checks passed, hidden tests failed |
| --- | --- | --- | --- |
| `holdout2`, 13 | 7 | **8** | 4 |
| Dev, 11 | 8 | **9** | 2 |

These ran before the repair examples added to the prompt in `110f5c0`. The main weakness is finishing on incomplete checks, since the loop stops as soon as visible tests pass. The `ios-local-http` hidden test is arguably too strict, because it forbids the arbitrary-loads key even when set to false. It was left unchanged after results were seen.

### Edge-case step: rule fixed before scoring

After the project's checks pass, the edge-case step asks Sumi to add tests that try to break its change, then runs them with the project's own test command. It started as opt-in and had to earn its place.

The rule below was committed before `holdout3` was scored. `holdout3` is 14 new tasks in `training/vibe/holdout3.py`, outside the training set, scored once. One run gives both arms: hidden tests score the project just before the step and again at the end, so model randomness cannot separate them. Hidden scoring restores the visible tests and ignores test files the run added.

The step becomes the default only if all of these hold:

1. Hidden passes with the step are at least the passes without it.
2. Falsely verified results are strictly fewer.
3. Correct work it refuses to call verified does not outnumber wrong work it stops calling verified.

If any fails, it stays an opt-in setting and the numbers are recorded either way. Dev runs before the rule (`dev` is already used): 9 of 11 and 8 of 11 hidden passes, no change from the step in either run. False verified fell from 2 to 0 and from 2 to 1. In each run the step refused one correct result.

Result on `holdout3` (14 tasks, one run, Sumi `fcf3a5e5a5af`, evidence in `training/evidence/2026-11-06/edge-holdout3.json`):

| | Without the step | With the step |
| --- | --- | --- |
| Hidden tests passed | 10 | 10 |
| Called verified | 14 | 9 |
| Falsely verified | 4 | 1 |
| Correct work not called verified | 0 | 2 |

All three conditions held (10 is not below 10, 1 is below 4, 2 does not exceed 3), so the step is now on for Sumi in the Takumi panel. It stays off for Rei, which was not measured.

What this does and does not show:

- The step made the label more trustworthy, not the code better. It fixed no task: gained none, lost none. Sumi's tests often found the real bug, but it could not repair the bug in the two tries it had.
- 14 tasks is small, and one run. Before-and-after use the same run, so randomness cannot separate the arms, but a repeat would give different individual results.
- These tasks are now used. A later claim needs new ones.
- It cost time: tasks that passed the step took 17 to 36 seconds in total. `cs-grid` took 496 seconds because three replies ran to the length limit. After scoring, a reply that hits the limit now ends the step at once. No other scored task hit it, and `cs-grid` still ends `partially_verified`, so the table stands.
- A test that passes can still be weak: `pwa-theme` stayed falsely verified. Weakening is caught only when lines of an existing test are changed or removed.

## Reasoning in the loop

Sumi supports native reasoning, and the loop has always turned it off. In `holdout3` that fits what went wrong: repairs that copied the code unchanged, tests with wrong hand-worked answers, and in `py-invoice` a correct repair undone because Sumi's own wrong tests kept failing.

Both switches are off until measured. `Options.edge_think` lets the edge-case step reason, and `Options.main_think` also covers the first reply and its repairs. A reasoning reply gets 8,192 output tokens instead of 4,096.

Trying them on the used `holdout3` tasks (development, not evidence):

| | Hidden tests passed | First reply passed | Falsely verified | Median seconds |
| --- | --- | --- | --- | --- |
| No reasoning | 10 of 14 | 9 | 1 | not comparable |
| Edge-case step | 11 of 14 | 9 | 1 | 106 |
| Every reply | 12 of 14 | 11 | 2 | 197 |

The rule below was committed before any new task was scored. `holdout4` has 17 tasks from families not used before. Each arm runs once, in the order A, B, C, with Sumi `fcf3a5e5a5af`:

- A: the current panel, edge-case step on, no reasoning.
- B: reasoning in the edge-case step.
- C: reasoning on every reply.

B or C replaces A only if all of these hold against A:

1. Hidden tests passed is at least 2 higher. One task can differ between runs by chance.
2. Falsely verified is no higher.
3. Correct work not called verified is at most 1 higher.
4. Median time per task is at most 300 seconds.

If both qualify, the one with more hidden passes wins, then fewer falsely verified, then the faster. If neither qualifies, reasoning stays off.

The chosen arm is B. On the first holdout4 run, A passed 10 hidden tests with 2 falsely verified, B passed 14 with 0 falsely verified, and C passed 14 with 2 falsely verified. B and C both cleared the rule. They tied on hidden passes, so B won because it falsely verified none. C's first reply passed 14, against 7 for A and B. Median seconds were 48.5, 107.2, and 171.1. Correct work not called verified was 4, 0, and 0. `edge_lost` was empty for B.

That comparison is the decision. The tasks are used, so the merged-code rerun below is a regression check, not new evidence. Same Sumi digest `fcf3a5e5a5af`, edge-case step on, reasoning only in that step. One run, 17 tasks, evidence `training/evidence/2026-11-06/reasoning-holdout4-b-merged.json`.

| | Original B | Merged-code B |
| --- | --- | --- |
| Hidden tests passed | 14 | 15 |
| Falsely verified | 0 | 1 (`js-table`) |
| Correct work not called verified | 0 | 2 (`cpp-big-add`, `java-day-of-year`) |
| First reply passed | 7 | 7 |
| Median seconds | 107.2 | 136.0 |
| `edge_lost` | none | none |

`js-ordinal` was still verified, with the hidden test passing. This run did not catch the known bad-test path. The decision stays B.

## Trusting the tests Sumi writes

In `js-ordinal`, Sumi wrote `ordinal(121) == '121th'`, changed correct code to match it, and the run ended `verified`. In the merged rerun, `js-table` was falsely verified after Sumi's own tests fixed the wrong separator format. `Options.edge_review` adds two checks, both off until they meet this rule:

- **Review:** when the edge-case step's checks fail or it changed code, a separate reasoning call judges the new test lines from the task alone. It never receives the code. If it finds a wrong line, the step's code changes are undone and Sumi must correct the test.
- **Strength:** the changed code lines are broken in up to 4 small ways, and the files always go back, including on Stop. If most breaks still pass, the tests are too weak for `verified`.

This rule was committed before scoring. The change is kept only if all of these hold:

1. A wrong test is not counted as verified: a scripted run where the code bends to a test the reviewer rejects does not keep that code.
2. A mutation of correct code fails the tests, and weak tests that survive mutation do not end `verified`.
3. The `js-ordinal` replay does not end `verified` with the code changed to match `ordinal(121) == '121th'`, including one call to the real Sumi reviewer on that exact test.
4. On `holdout4`, against the merged-code B run: hidden passes do not fall from 15, and falsely verified does not rise from 1.

`edge_lost` and correct work refused are reported whatever the result. `holdout4` is used, so this can only show no regression, not a new gain.

Result: **the rule was missed, so `edge_review` stays off.** Same Sumi digest, one run, evidence in `training/evidence/2026-11-06/edge-review-holdout4.json` and `edge-review-live.json`:

| | Merged-code B | With review and strength checks |
| --- | --- | --- |
| Hidden tests passed | 15 | 14 |
| Falsely verified | 1 | 0 |
| Correct work not called verified | 2 | 0 |
| `edge_lost` | none | none |
| Median seconds | 136.0 | 174.5 |

Conditions 1 to 3 held. The real reviewer rejected the exact `ordinal(121) == '121th'` line, accepted the correct one, and picked the wrong line out of three, but those are single calls. Condition 4 did not: hidden passes fell from 15 to 14.

The two tasks behind that drop, `py-tiered` and `cpp-big-add`, failed before the edge-case step began, so the new checks did not touch them and run-to-run variation is the likely cause. The rule did not allow for that, and it is not being changed after the fact. Other limits: the reviewer rejected nothing during this run, 2 reviews gave no verdict, and other work shared the GPU, so the timings are not clean. The false-label numbers are good enough to justify scoring these checks on new tasks, with a rule that compares paired results.

## Whole features

`training/vibe/features.py` builds an 83-file service and asks for features that span several layers, scored by hidden end-to-end tests. `--check` proves each hidden suite fails on the untouched project and passes with a reference solution. There are 2 development features and 2 unseen features (`customer-tier`, `shipment-tracking`), 11 hidden tests each way.

This rule was committed before any feature was run on Sumi:

1. Run the current product on the development features and on the unseen features. The unseen result stays sealed: it is written to a file and not read until the change is finished.
2. Change the product using only the development results.
3. Run the changed product once on the unseen features, then open both unseen results.
4. Keep the change only if unseen hidden tests passed is higher than the sealed baseline, and a `holdout4` rerun does not get worse on hidden passes or falsely verified.

Two features and one run per side is a very small sample, so a gain here is only a first signal.

Baseline, with reasoning in the edge-case step (evidence in `training/evidence/2026-11-06/feature-*-baseline.json`):

| | Hidden tests passed | Features fully passing | Time per feature |
| --- | --- | --- | --- |
| Development | 10 of 10 | 2 of 2 | 500 to 522 s |
| Unseen | 7 of 11 | 1 of 2 | 535 to 825 s |

Sumi found and changed every file the reference solutions touch, including 7 across layers for `customer-tier`. The development results gave no failure to design a change from, so no product change was made and steps 2 to 4 did not run. The rule stays open for a change with a real reason. `shipment-tracking` was left `partially_verified` with 2 of 6 hidden tests passing, so that label warned correctly. These tasks are small compared with a real product, other work shared the GPU, and this says little about a project as large as NX Infinity.

## What stays local

A request that starts by asking to add, fix, implement, or test the project is not sent to web search. Search still runs when the request asks for it (`look up`, `search`, `what is`, a subreddit, or research mode set to always) and when the request is a repository review. This does not change Sumi, Rei, or the training prompts.

After a project's own checks pass, including after the edge-case step, a named JavaScript or TypeScript import that the target file does not export is still a failure. A Go file in the same package is checked the same way before `go` runs: a call to a function the package does not define, and a method call on a value that file builds with `Box{}` or `&Box{}` when the method is missing or the argument count does not match. A call on another package, or on a value assigned more than one type, is left for the compiler. A missing method names the methods that type already defines, and the repair opens those functions even when they sit far below the type. `export *` is left alone, because that file's exports are not visible to a lexical read. The report's `verification` field keeps source edits, a static source check, a build, tests, a runtime step, and an end-to-end pass as separate facts. A passing test does not count as a passing build, and a static check does not count as either. A test command that exits 0 after running zero tests is a failure. Removing `#[test]`, marking the only test `#[ignore]`, a `go test -v` log of only `SKIP`, a Node summary of `# pass 0` and `# fail 0`, or unittest `OK (skipped=N)` for every test it ran, does not count as a pass. Python checks delete `__pycache__` first, so a fix saved in the same second is the code the test imports. `verified` is the only state that means the end-to-end checks passed. A task id `run` or `runtime` in `takumi.tasks.json` is that runtime step.

No adapter was trained for this change. Sumi and Rei are the same models.

While a verified run is working, the panel and the open conversation receive events from the run itself: `task_started`, `project_inspection`, `plan_updated`, `context_retrieval`, `file_changed`, `build_started`, `build_completed`, `test_started`, `test_completed`, `diagnostic_found`, `repair_started`, `checkpoint_saved`, `task_blocked`, and `task_completed`. A step that did not run does not get an event. A passing check is not run again when the files are still the ones that passed. The conversation gets the run's own final sentence when a session was selected.

The last run is remembered only for those exact file bytes. Editing a file drops it, so a later run is not told that an older result still holds. A Python file that calls or imports a name another project file no longer defines is a failure, the same as a missing JavaScript export.

A verified run refreshes the project index before each attempt. A file whose bytes still match is kept, and only a changed file is parsed again. The attempt then reads a symbol the task names at its definition line instead of the head of a long file. A failure line in that file is read as well, so the error does not hide the definition. A failure that quotes a name, or writes it as `Box.store`, `save_invoice`, or `CamelCase`, is read at that definition even when the task does not say the name. A Python or JavaScript traceback keeps the line that raised and up to three project callers above it. Pytest's `file:line: in function` form is read the same way. A library frame is left out. JavaScript keeps the first `at file:line` frame as the raise. A `node --test` assertion keeps the stack line and the expected-value diff, and leaves `node:internal` frames out. A Rust panic keeps `panicked at file:line` and the left and right values, instead of Cargo's `error: test failed` line. A Go compile line `file:line:col: message` and a rustc `error[E1234]` with `--> file:line:col` keep that source line. A Go test line `file:line: message` keeps that line and both compared values. Pytest's `file:line: in function` stays a traceback. A sentence such as "value must be 2" does not name a symbol. A call is read at the call only when the index records that call's target as that definition, including a JavaScript name imported from a relative file. `Box().save()` and `module.Box().save()` record the target as `Box.save`, and a relative `from .a import Box` does the same. `box = Box()` followed by `box.save()` records `Box.save` when every assignment of that name in the scope is that constructor, including an assignment in the surrounding module. A parameter, `box = None`, two different constructors, and a comprehension that reuses the name stay a call on `box`. The same assignment is checked for a missing method and a wrong argument count. A JavaScript class method is indexed at `Class.method`. `new Box().save()` and `const box = new Box(); box.save()` record that method. A parameter in one function does not unbind a different function's `box`, and `const box = null` stays unbound. A missing method or a wrong argument count on that call is reported. The index schema is 6, so a snapshot written by an older parser is read again. At most four other files are added for those calls. A call that only shares the short name, such as `box.save()`, is left out. A hash that no longer matches the file is ignored. A name defined in one fresh file is that definition. The same name in several files is read from the file the task names, or, when the task names no file, from the one definition other files actually call. A call inside the defining file does not decide it. When both definitions are called, neither is treated as the only one. On a repair turn the files in the prompt are the copies on disk, and a previous reply that disagrees with them is not. When a repository has at least six indexed files, a word that appears in 40 percent or more of them does not pull those files into the prompt. A function the task names, and a file that calls it, still rank first. In a 12-file fixture, `save_invoice` and its caller were selected and the ten files that only repeat the word "invoice" were not. The same measurement windowed a 4,000-line caller at `return save_invoice()` and the definition at `return 1`. It also windowed `return Box().save(1)` and `box = Box(); return box.save(1)`, and left `box = None` unanchored. A JavaScript `const box = new Box(); return box.save(1)` was windowed at the call and at `save(item)`. `const box = null` was not anchored. A name that appears in the failure is ranked even when the task and the failing file never write it. A long file then opens at that definition, including a Go function the source index does not parse. When a few files define that name, each definition is opened. A name defined across the tree is not. The window then includes that function through its return. A longer function keeps its start and its return. A SEARCH block that copies an `... omitted middle ...` banner is applied as the pieces on either side, so the return can change while the hidden lines stay. A Go project that fails `go test` can be repaired that way: the build and the test stay separate facts, and `verified` requires both. Once the index has resolved a name, a file that only contains that word is not ranked as a caller. In a nine-file fixture that unbound file was left out of the prompt, while the same prompt without the index still read it. A crash that quoted `Box` while the task said only "fix the crash" was windowed at `def save`. A traceback that raised in `invoice.py` and called it from `checkout.py` kept both lines and left the library frame out. The same two lines were kept from pytest's `file:line: in function` form, with the message `ZeroDivisionError: division by zero`. A real `node --test` run failed on `logic.test.js:4` and the diagnostic included `Hello, Sumi`. The same measurement, given two `save` definitions and a checkout that imports `billing.invoice`, read `billing/invoice.py` and the caller and left `notes/draft.py` out. Refreshing that index after one file changed parsed that file and reused the other eleven (`training/evidence/2026-11-06/agent-system-bench.json`). That run did not use a model or a GPU.

A plain call whose argument count cannot match the function it imports is an error, even when the project's tests pass. The same check applies to `Class().method()` when the class is defined in another project file and does not inherit. Defaults, rest arguments, keywords, and stars are left alone. When that failure names the defining file, the next repair reads it as well as the caller. Diagnostic room this turn does not use is spent on source instead. A repair that deletes or rewrites a test line that was already in the project is put back and tried again. An assertion in the same file as the function, such as Rust's `assert_eq!` in `src/main.rs`, is kept the same way. The function around it can still change. The task can still change a test when it names that file. The edit prompt lists the checks this run will execute and any check that is blocked, by name and kind. It does not include the command line. The training rows do not include that section, because their prompts are rendered without a project plan. A file that is already included in full is left out of the repository map, so the prompt does not carry a second copy of the same source. Up to four uncommitted source files are read even when the task does not name them. The three repair rows in `training/vibe/vibe-coding-v1.json` were re-rendered to match that prompt. Their fixes were not changed, and no adapter was trained on the new wording.

## Forge Universal

Takumi reads Forge's install and reports its version. It never installs, starts, stops or upgrades Forge. The setting in the panel gives OpenCode agent runs Forge's own MCP configuration, and it is off by default because those tools run with your permissions. No live agent run has used those tools yet.

## Sources of technique

Licenses were checked through the GitHub API on 2026-11-04. No code was copied.

| Project | License | Used here |
| --- | --- | --- |
| Aider | Apache-2.0 | SEARCH/REPLACE format, personalised PageRank repo map |
| OpenHands | MIT | Stuck detection (repeated failure identity) |
| SWE-agent | MIT | Lint and test gating around edits |
| Cline, Roo Code (archived), Continue | Apache-2.0 | Checkpoint rollback, project rules. Approaches only, from memory |
| Codex | Apache-2.0 | Patch-oriented edits, per-project instructions. Approach only, from memory |
| OpenCode | MIT | Remains the default engine |
| Claude, Cursor | Proprietary | General public product behaviour only (shadow verification before accepting edits) |
