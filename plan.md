# Implementation Plan: Windows Single-File Offline Python Coding Agent

## 1. Purpose

This document converts `Final_Windows_Project_Plan.md` into an implementation-ready delivery plan. It defines phases, atomic tasks, affected files, inclusions, exclusions, test obligations, and completion criteria for each task.

This project is a **personal resume/portfolio project**. The MVP prioritizes technically rigorous architecture, deterministic behavior, reproducible evaluation, and transparent safety boundaries over production-scale deployment, broad platform compatibility, or enterprise operational requirements.

The product is a native Windows terminal application named `localdev`. It accepts exactly one explicitly supplied user Python source target file per command, combines deterministic analysis with a locally installed small language model (SLM) running via Ollama, and keeps the user in explicit control of source-code changes.

The product claim is deliberately narrow, technically honest, and appropriately scoped:
- It is a personal portfolio project demonstrating high engineering rigor, clean Windows systems integration, and reproducible local AI agent orchestration; it is **not intended to be production-grade infrastructure** or enterprise software.
- Supported platform: **Windows 11 x64 only**. Supporting other operating systems or legacy Windows releases is intentionally out of scope.
- It is an offline-after-installation assistant for **user-owned or trusted Python code only**. Single-user, local execution is intentional.
- It is **not a security sandbox**. Operational process limits (Windows Job Objects, execution timeouts, output byte limits) terminate runaway tasks and prevent accidental resource exhaustion, but do not protect against malicious code designed to compromise the host.
- The project is intentionally **single-target**, not necessarily "single-file at runtime." Exactly one Python source file is the explicit target for inspection, analysis, diagnosis, patching, validation, and source mutation. Runtime execution is allowed to behave like normal Python and may import or read other modules or filesystem resources required by the target code. Other source files in the project or workspace are never inspected, analyzed, diagnosed, patched, or considered repair targets.
- The existing technical standards, safety invariants, and verification criteria remain non-negotiable; personal-project framing clarifies operational boundaries without weakening software quality or safety invariants.

## 2. Delivery principles

1. **Deterministic primacy:** Deterministic tools (Python AST, compiler flags, isolated Ruff diagnostics, controlled runtime execution) are the primary source of facts; the local SLM is strictly restricted to explaining verified evidence and proposing bounded structured edits.
2. **Strict single source target:** Every command operates on exactly one explicitly supplied Python source target file for inspection, analysis, diagnosis, and patching. Secondary command-line inputs (such as a JSON input file for profiling or expected stdout strings for validation) provide execution data and do not constitute additional source targets.
3. **No automatic discovery or project-wide inspection:** No sibling source files, local modules, test suites, Git history, or project configuration files are discovered, traversed, or inspected automatically.
4. **No autonomous execution or mutation:** Model output never directly executes commands, launches arbitrary processes, or writes directly to source files.
5. **Guarded patch lifecycle:** Every edit proposal is schema-validated, applied to an isolated temporary candidate copy staged on the same filesystem volume as the target, differentially checked against pre-edit baselines, rendered as a human-readable unified diff, explicitly confirmed by the user (or authorized via noninteractive `--apply`), guarded by immediate compare-before-replace SHA-256 stale-edit detection, and applied via atomic file replacement using Windows `ReplaceFileW` with native backup capability (`lpBackupFileName`, `dwReplaceFlags = 0`) on the same filesystem volume. `--apply` never bypasses hash or validation safety gates, no `--force` bypass exists, and power-loss durability is explicitly outside the MVP.
6. **Execution boundary for trusted code:** Subprocess execution is provided only for trusted code under strict operational limits (timeouts, output caps, Windows Job Object containment). Operational containment is an operational guardrail, not a security sandbox.
7. **Empirical validation levels:** Reports strictly separate static validity (Level A: candidate source parses successfully, candidate introduces no new static diagnostics under the configured policy, and if the original repair target included a specific static diagnostic, that diagnostic also disappears; runtime-only fixes pass Level A without needing a Ruff diagnostic to eliminate), failure reproduction removed (Level B: original runtime failure signature no longer reproduced; explicitly noting that Level B does NOT prove the bug is fixed, as replacing an `IndexError` with a `TypeError` satisfies Level B but fails Level C), clean execution (Level C: candidate exits successfully with code 0 under controlled runtime), and verified behavioural correctness (Level D: explicit user-supplied behavioral checks/oracles pass).
8. **Formal complexity contract:** Static complexity reports distinguish time complexity, auxiliary space (transient intermediate memory), and output space (memory escaping as returned structures) under a strict `conservative + assumption-linked + source-linked + abstention-first` contract based on CPython runtime semantics, explicitly differentiating amortized and expected/average complexities from worst-case bounds.
9. **Separated memory and hot-process profiling:** Profiling adheres to hot-process semantics (repeated invocations in a loaded worker), reporting import metrics, invocation times, peak tracemalloc-tracked Python memory allocations (distinct from total process footprint), and approximate worker process-tree RSS separately from external Ollama service residency.
10. **Honest limitations and safe abstention:** Whenever static facts, runtime signatures, complexity bounds, or model responses cannot be established with technical certainty, the application emits an explicit limitation or abstains rather than inventing answers.
11. **Output integrity and display sanitization:** Terminal output is sanitized against ANSI/VT escape sequences to prevent terminal display corruption, while machine-readable JSON output preserves underlying raw data verbatim.

## 3. Project-wide scope

### In scope

- Supported platform: **Windows 11 x64 only** (all other operating systems and legacy Windows releases are explicitly unsupported).
- Exactly one Python source target per command.
- Secondary user-supplied inputs (e.g., `--input` profiling JSON, `--expected-stdout`, `--expected-exit`) that supply test data without acting as source targets.
- Supported Python runtime policy: Python 3.11 (64-bit) minimum supported version; tested support matrix covers Python 3.11 and 3.12 on Windows (leveraging standard Python 3.11+ `-P` / `PYTHONSAFEPATH`).
- Explicit version recording for reproducible evaluation: exact versions pinned and recorded for Python, Pydantic, Ruff, psutil, Ollama, and the selected SLM.
- CLI commands: `info`, `detect`, `analyse`, `debug`, `fix`, `complexity`, and `profile`.
- Dual output modes: human-readable sanitized terminal output and versioned JSON envelopes (`schema_version: "1.0"`).
- Non-executing language detection based on extension, shebang, and AST parsing.
- Static Python syntax checking, AST fact extraction, and isolated Ruff diagnostics with zero project-level cache pollution (defense-in-depth: `--isolated --no-cache`, isolated session execution, and artifact verification).
- Controlled runtime execution with wall-clock timeouts, byte-capped pipe draining, and traceback parsing.
- Process-tree lifecycle management via Windows Job Objects with graceful fallback to `psutil`.
- Local SLM diagnosis and structured edit proposals via a local Ollama HTTP API endpoint using Pydantic JSON Schema objects (`format: Model.model_json_schema()`) with intentionally simple production schemas (objects, strings, integers, booleans, arrays, enums, simple nested objects) tested against the project's pinned Ollama version.
- Separated context window (2,048 tokens), application-controlled prompt budget (1,200 tokens), output budget (600 tokens), and explicit 248-token application safety margin for framing, JSON schema, and tokenization uncertainty (an application safety margin, not a platform-reserved context partition).
- Guarded single-file patch application: schema-validated line edits, temporary candidate validation, unified diff rendering, compare-before-replace SHA-256 stale-edit detection, same-volume candidate staging, and atomic file replacement using Windows `ReplaceFileW` with native backup support (`lpBackupFileName`, `dwReplaceFlags = 0`) on the same filesystem volume (without claiming power-loss durability).
- Static time, auxiliary-space, and output-space complexity analysis with mandatory abstention for unestablished semantics, based on CPython runtime semantics.
- Function profiling with separate import measurement, repeated invocation timings, peak tracemalloc-tracked Python memory allocations, and parent-monitored worker process RSS.
- Fully offline operation after initial installation of Python packages, Ruff binary, Ollama service, and the target model.

### Not in scope

- Production-grade enterprise deployment, multi-user concurrency, distributed execution, or cloud infrastructure.
- Multi-file or repository-wide analysis, cross-file navigation, or multi-file repairs.
- Automatic inspection, discovery, or repair of imported local modules, sibling source files, tests, configuration, or Git history.
- Automatic dependency installation or environment resolution.
- Framework-specific debugging (e.g., Django, Flask, PyTorch test harnesses) or project-wide test suites.
- Execution of untrusted or hostile code; filesystem chroot; network sandboxing (Job Objects provide operational runaway termination, not a security boundary).
- Automatic patch application without explicit write authority (`--apply` removes the interactive prompt but preserves all validation and safety invariants).
- Flags that bypass safety checks (no `--force` flag is permitted).
- Multiple concurrent inference backends or alternative local runtimes in the MVP.
- Concurrent execution or profiling jobs.
- Guaranteed discovery of arbitrary logical bugs, automatic proof of patch correctness, or general symbolic complexity solving.
- Transactional filesystem durability across power-loss events (atomic directory-entry replacement is guaranteed, but hardware-level power-loss flush guarantees are excluded from MVP).
- Execution under WSL2, AppContainer isolation, Windows Sandbox, Docker containers, or virtual machines.

---

## 3.1 Runtime and Isolation Contract

To prevent confusion between single-file static analysis, runtime execution, and security boundaries, `localdev` enforces a formal Runtime and Isolation Contract governing four distinct dimensions:

### 1. Source-Target Scope (Single-Target, Not "Single-File at Runtime")
- `localdev` accepts exactly one explicit Python source file as the target for inspection, analysis, diagnosis, patching, validation, and source mutation.
- Runtime execution is allowed to behave like normal Python and may import or read other modules or resources required by the target code.
- Other source files in the project or workspace are **never** inspected, analyzed, diagnosed, patched, or considered repair targets.
- Secondary user-supplied files (such as a JSON input file supplied via `--input` for profiling or test assertions via `--expected-stdout`) provide execution or validation data and do not constitute additional source targets.
- Automatic discovery, traversal, AST parsing, or inspection of sibling files, parent directories, local packages, project configuration files (`pyproject.toml`, `setup.cfg`), or `.git` repositories is strictly prohibited.
- The project is intentionally **single-target**, not necessarily "single-file at runtime."
- `localdev` does not imply or provide a security sandbox.

### 2. Runtime Import Behavior
- When execution occurs (during `debug`, candidate patch validation, or `profile`), the code is executed as real Python code by the selected Python interpreter.
- Imports initiated by the target file (standard library modules, installed third-party packages, or local sibling modules reachable via Python's standard module search) are ordinary Python runtime actions.
- **Runtime Import Resolution:** Python is launched with `-P`, which disables the automatic prepending of the script directory or current working directory to `sys.path`. However, if a target script programmatically modifies `sys.path` (e.g., via `sys.path.append(...)` or `sys.path.insert(...)`) or resides in a package tree structure with `__init__.py` files, package-relative sibling imports could successfully resolve at runtime. `localdev` does not rewrite AST import statements or intercept Python's runtime import mechanism; runtime imports remain standard Python execution behavior.
- `localdev` does not intercept, inspect, analyze, or attempt to patch imported dependencies or sibling files. Only the single explicit target is inspected, analyzed, and modified.
- Static-only commands (`info`, `detect`, `analyse`, `complexity`) never import or execute target code.

### 3. Filesystem and Network Access
- Subprocess execution runs under the operating system privileges of the invoking user.
- Execution controls (Windows Job Objects, execution timeouts, output byte caps) are operational containment mechanisms designed to terminate runaway loops and child processes; **they do not constitute a security sandbox**.
- `localdev` is strictly intended for **user-owned or trusted Python code**. It does not provide filesystem virtualization, read-only filesystem jail, or network firewalling.

### 4. Environment Policy and Dependency Availability
- **Interpreter invocation:** Subprocesses are launched using the selected Python executable with flags `-E`, `-B`, and `-P`:
  - `-E` ignores all `PYTHON*` environment variables (including `PYTHONPATH` and `PYTHONHOME`), preventing unintended host environment contamination.
  - `-B` (`PYTHONDONTWRITEBYTECODE=1`) prevents bytecode writing (`.pyc` files and `__pycache__` directories).
  - `-P` (`PYTHONSAFEPATH` in Python 3.11+): prevents Python from automatically inserting potentially unsafe ambient script or current working directory paths into `sys.path`.
- **Why isolated mode (`-I`) is NOT used universally:** While Python's `-I` flag enforces strict isolation, it implicitly activates `-s` (disabling user site-packages) and alters `sys.path` in ways that can break virtual environments and prevent legitimate third-party packages needed by user code from loading. Instead, `localdev` uses `-E -B -P` combined with an explicit environment variable allowlist.
- **Environment allowlist:** Child processes inherit only essential operating system environment variables required for basic runtime stability: `SYSTEMROOT`, `SYSTEMDRIVE`, `PATH`, `PATHEXT`, `TEMP`, `TMP`, `COMSPEC`, and `USERNAME`. All other environment variables are stripped.
- **Working directory (`cwd`):** By default, `cwd` is set to the user's invocation working directory (where `localdev` was run), **NOT** the session directory. Using the user's invocation directory preserves standard relative file path resolution (e.g., `open("data.csv")`) so user scripts behave as intended.
- **`sys.path` definition:** Python is launched with `-P`, which suppresses automatic insertion of ambient script/cwd paths. However, `-P` does not strip standard library paths, site-packages, virtual environment paths, existing `PYTHONPATH` entries in the host environment (though stripped by `-E`), or any `.pth` files processed by the site module. Furthermore, target code may programmatically modify `sys.path` at runtime (`sys.path.append(...)`). Thus, `-P` reduces accidental ambient imports from the script's directory rather than creating a hermetic or sandbox environment.
- **`__file__` definition and relocated session copy semantics:**
  - When executing a target for `debug`, validation, or `profile`, `localdev` executes a temporary session copy of the file located under `%TEMP%\localdev\session_<id>\session_target.py`.
  - While `cwd` remains the user's original invocation directory, Python assigns `__file__` to the absolute path of the temporary session copy.
  - Consequently, package identity and relative-resource behavior may differ from executing the original file in place. Resource access relative to `__file__` (e.g., `Path(__file__).parent / "resource.json"`) does **NOT** behave identically to executing the original source file, because `localdev` intentionally does not copy the surrounding project tree or reconstruct package structures.
  - This is an explicit, intentional limitation of the single-target runtime model, covered by dedicated boundary tests.
  - For user-facing reporting, `localdev` normalizes all frame references in tracebacks and diagnostic reports that point to the session copy back to the user's canonical target path.
- **Package availability:** Installed third-party packages residing in the active Python environment (virtual environment or global site-packages) remain available so that trusted scripts execute dependencies normally.

### 5. Same-Volume Staging and Atomic Replacement Contract
To reconcile isolated temporary session management with Win32 `ReplaceFileW` kernel constraints, `localdev` explicitly separates general session data from atomic replacement staging:
- **General session workspace:** General session artifacts, execution logs, and diagnostic metadata reside in `%TEMP%\localdev\session_<id>\...`.
- **Same-volume candidate staging:** Win32 `ReplaceFileW` strictly requires the replacement candidate file, the target file, and the backup file to reside on the **same filesystem volume**. Therefore, a session directory residing under `%TEMP%` (typically volume `C:`) does **NOT** hold the final replacement candidate if the target resides on another volume (e.g., `D:\project\foo.py`).
- **Volume determination and staging:** When preparing the validated candidate for atomic write, `localdev` determines the filesystem volume of the target and creates a dedicated staging location on that same volume (e.g., `D:\<localdev-staging>\<session-id>\candidate.py`).
- **Native backup destination:** The backup file (`<target>.bak`, e.g., `D:\project\foo.py.bak`) is also created on the target's volume.
- **Fail-closed volume invariant:** Any cross-volume replacement attempt is detected and fails closed before write invocation.
- **Atomic replacement invocation:** Replacement uses Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)`. The unsupported flag `REPLACEFILE_WRITE_THROUGH` is not used.
- **Durability boundary:** `ReplaceFileW` guarantees atomic directory entry swapping under normal OS operation (eliminating partial/corrupted files), but power-loss durability (which requires hardware-level write flushes) is explicitly outside the MVP. Stale-edit detection via SHA-256 compare-before-replace guards against external modifications prior to replacement.

---

## 4. Target repository structure

```text
localdev/
├── pyproject.toml
├── README.md
├── LICENSE
├── docs/
│   ├── architecture.md
│   ├── security.md
│   ├── evaluation.md
│   └── demo.md
├── localdev/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── constants.py
│   ├── errors.py
│   ├── schemas.py
│   ├── agent/
│   │   ├── orchestrator.py
│   │   ├── permissions.py
│   │   ├── session.py
│   │   ├── evidence.py
│   │   └── context_builder.py
│   ├── languages/
│   │   ├── base.py
│   │   ├── detector.py
│   │   └── python/
│   │       ├── adapter.py
│   │       ├── syntax.py
│   │       ├── ast_analyser.py
│   │       ├── traceback_parser.py
│   │       ├── diagnostics.py
│   │       ├── complexity.py
│   │       └── selectors.py
│   ├── inference/
│   │   ├── base.py
│   │   ├── ollama_client.py
│   │   ├── prompts.py
│   │   └── response_validator.py
│   ├── execution/
│   │   ├── runner.py
│   │   ├── windows_job.py
│   │   ├── limits.py
│   │   ├── process_tree.py
│   │   ├── output_capture.py
│   │   └── environment.py
│   ├── patching/
│   │   ├── edit_schema.py
│   │   ├── edit_validator.py
│   │   ├── applier.py
│   │   ├── diff_renderer.py
│   │   ├── atomic_write.py
│   │   └── backup.py
│   ├── profiling/
│   │   ├── worker.py
│   │   ├── loader.py
│   │   ├── timer.py
│   │   ├── python_memory.py
│   │   ├── process_memory.py
│   │   └── benchmark.py
│   └── reporting/
│       ├── terminal.py
│       ├── sanitizer.py
│       ├── json_reporter.py
│       └── exit_codes.py
└── tests/
    ├── unit/
    ├── integration/
    ├── windows/
    ├── bug_samples/
    ├── complexity_samples/
    ├── profiling_samples/
    └── boundary_samples/
```

## 5. Standards applied to every task

### Definition of done

A task is complete only when:
1. Implementation code, typed schemas, and error paths are fully written and pass `mypy --strict`.
2. Unit and integration tests pass on the target Windows environment without network access.
3. The Runtime and Isolation Contract is strictly preserved (one Python source target, no sibling discovery, `-E -B -P` execution policy).
4. No unexpected file artifacts (such as `.pyc`, `__pycache__`, or `.ruff_cache`) are created in the target file's directory.
5. Terminal output is sanitized against ANSI/VT escape sequences, while JSON output preserves underlying raw data.
6. The distinction between process limits and security sandboxing is maintained in code comments, warnings, and documentation.

### Testing conventions

- **Isolation:** Unit tests run via `pytest` and must never require network access or a running Ollama daemon (using deterministic fake responses).
- **Windows marks:** Windows-specific process tests, Job Object lifecycle tests, same-volume atomic file replacement tests, and 5-stage handle cleanup tests are marked `@pytest.mark.windows` and run on native Windows 11 x64.
- **Fixture protection:** Tests must never modify fixture files in-place; all mutation tests run inside isolated temporary directories managed by `pytest`.
- **Ruff cache isolation:** Tests verify through defense-in-depth (`--isolated --no-cache`, session execution) that Ruff leaves zero `.ruff_cache` directories or unexpected artifacts in the user's workspace or parent directories.
- **Handle cleanup:** Process tests verify that file and pipe handles are closed before temporary directory deletion to prevent Windows sharing violations (`ERROR_SHARING_VIOLATION`).
- **Schema validation:** All structured outputs and test assertions are validated against `localdev/schemas.py`.

### Planned quality commands

```powershell
python -m pytest
python -m pytest -m windows
ruff check --isolated --no-cache .
python -m mypy --strict localdev
```

---

## 6. Phase summary and dependency order

| Phase | Outcome | Depends on |
|---|---|---|
| 1 | Frozen specification, schemas, Runtime & Isolation Contract, and model decision | None |
| 2 | CLI foundation, single-target validation, sessions, cleanup, and sanitized reporters | Phase 1 |
| 3 | Language detection and adapter architecture | Phase 2 |
| 4 | Python syntax, AST facts, and isolated Ruff evidence (zero cache pollution) | Phase 3 |
| 5 | Controlled Windows execution (`-E -B -P`), output limits, process cleanup, and traceback parsing | Phase 2, Phase 4 |
| 6 | Windows Job Objects, handle management, and separate process memory accounting | Phase 5 |
| 7 | Evidence-grounded local SLM diagnosis with explicit token budget and model lifecycle | Phase 4, Phase 5, Phase 6 |
| 8 | Structured edit schema (1-based, inclusive), diff rendering, backup, and atomic replacement | Phase 7 |
| 9 | Differential patch validation (Levels A–D) | Phase 8 |
| 10 | Static time, auxiliary-space, and output-space analysis with mandatory abstention | Phase 4 |
| 11 | Function profiling (hot-process semantics, direct file loading, separated memory metrics) | Phase 5, Phase 6, Phase 10 selectors |
| 12 | Evaluation, 8 GB budget optimization, documentation, and structured release demo | All prior phases |

---

# Phase 1 — Specification, environment, schemas, and model benchmark

## P1-T1 — Freeze the MVP contract, toolchain, and Runtime & Isolation Contract

**Status:** done

**Definition:** Record the exact personal-project scope, supported Windows 11 platform, supported Python versions, pinned toolchain/model versions, CLI surface, trust model, configuration defaults, and non-goals. Establish the Runtime and Isolation Contract defining single-target scope, runtime import behavior, filesystem/network permissions, same-volume replacement staging, and environment policies. Establish a reproducible Python package with development dependencies and test markers.

**Files:** `pyproject.toml`, `README.md`, `LICENSE`, `docs/security.md`, `docs/architecture.md`, `localdev/__init__.py`, `localdev/constants.py`, `tests/conftest.py`.

**In scope:** 
- Package metadata and console entry point (`localdev = "localdev.cli:main"`).
- Target OS: Supported platform: **Windows 11 x64 only** (all other operating systems and legacy Windows releases are explicitly unsupported).
- Supported runtime policy: Python 3.11 (64-bit) minimum supported version; tested support matrix covers Python 3.11 and 3.12 on Windows.
- Toolchain and model version pinning for reproducible evaluation: record exact versions for Python, Pydantic, Ruff, psutil, Ollama, and the selected SLM.
- Explicit definition of single-target semantics: exactly one explicit Python source target file per command is inspected, analyzed, diagnosed, patched, and validated. Runtime execution behaves like normal Python and may import/read dependencies, but other source files are never analyzed, diagnosed, or patched. Secondary input files (e.g., `--input` profiling JSON) do not count as source targets.
- Runtime execution policy: execution launches the selected interpreter with `-E`, `-B`, and `-P`, using a minimal environment allowlist, default user invocation `cwd`, with `sys.path` not automatically inserting ambient script/cwd paths (while remaining entries are determined by the Python environment and runtime modifications are possible), and `__file__` pointing to the relocated session copy (with the intentional limitation that package tree and sibling resources are not copied); explanation of why isolated mode (`-I`) is not used.
- Explicit non-sandbox disclaimer: operational limits terminate runaways but do not secure against malicious code; localdev is for user-owned/trusted code only.
- Strict compare-before-replace SHA-256 specification: provides stale-edit detection against external modifications, not concurrent filesystem CAS.
- Atomic replacement and same-volume staging architecture: the candidate file used for final replacement must be staged on the same filesystem volume as the target; native backup destination must also reside on the target's volume; atomic replacement uses Windows `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)` without unsupported flags (`dwReplaceFlags = 0`); cross-volume attempts fail closed; power-loss durability is outside the MVP. `--apply` never bypasses hash or validation gates; no `--force` bypass exists.
- Terminal output sanitization contract vs raw JSON preservation.

**Not in scope:** Implementing command logic; installing Ollama or downloading models; non-Windows CI configuration; claiming sandbox security; enterprise deployment features.

**Testing:** 
- Build wheel and install into a clean virtual environment on Windows 11 x64; assert `localdev --help` resolves with zero errors.
- Run import smoke tests.
- Documentation test verifying that the personal-project scope statement, trusted-code warning, single-target definition, and non-sandbox disclaimer are present in `README.md` and `docs/security.md`.

**Acceptance:** Clean package installation succeeds; CLI entry point resolves; all version, trust, and isolation boundaries are unambiguous and documented.

## P1-T2 — Define versioned internal and external schemas

**Status:** done

**Definition:** Implement strongly typed Pydantic representations and validation for diagnostics, runtime results, traceback frames, AST facts, diagnoses, edit proposals, validation reports, complexity reports, profile reports, and the top-level JSON envelope. Ensure production model schemas are intentionally simple and compatible with the pinned Ollama version.

**Files:** `localdev/schemas.py`, `localdev/errors.py`, `tests/unit/test_schemas.py`, `tests/fixtures/schema/`.

**In scope:** 
- Schema version `"1.0"`.
- Pydantic JSON Schema export: provide `.model_json_schema()` generation for diagnosis and edit proposals to supply directly to Ollama's `format` parameter for grammar-constrained token generation.
- Practical production schemas: schemas for local SLM generation are kept intentionally simple and robustly compatible with the pinned Ollama version (restricting constructs to objects, strings, integers, booleans, arrays, enums, and simple nested objects; avoiding arbitrarily complex JSON Schema features). Require testing production schemas against the exact Ollama version used.
- Edit schema indexing: 1-based, inclusive `start_line` and `end_line`; explicit operation types (`replace`, `insert`, `delete`); normalized `expected_text` and `replacement_text`; maximum 8 edits and 80 changed lines per proposal.
- Complexity schema: separate `time`, `auxiliary_space`, and `output_space` fields; supported asymptotic complexity enum explicitly containing `O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`, `O(2^n)`, and `UNKNOWN`; based on CPython runtime semantics, explicitly differentiating amortized costs (e.g., `list.append()`) and expected/average costs (e.g., dict lookup) from worst-case bounds; confidence enum (`HIGH`, `MEDIUM`, `LOW`); explicit assumptions list; abstention reason code enum (`UNKNOWN_CALL`, `DYNAMIC_BOUNDS`, `DYNAMIC_RECURSION`, `EXTERNAL_DEPENDENCY`, `UNSUPPORTED_SYNTAX`).
- Profiling schema: separate fields for `import_duration_ms`, `import_stdout`, `import_stderr`, warm-up statistics, repeated invocation statistics (median, dispersion), `python_allocations_tracemalloc_bytes` (peak tracemalloc-tracked Python memory allocations, distinct from total process footprint), and `approximate_process_tree_rss_bytes`. Hot-process state persistence metadata flag.
- Inference context metadata schema: separated fields for `context_window_tokens` (2,048), `prompt_budget_tokens` (1,200), `output_budget_tokens` (600), `application_safety_margin_tokens` (248), `estimated_prompt_tokens`, `prompt_eval_count`, `eval_count`, `prompt_eval_duration`, `eval_duration`, `was_truncated`, and list of omitted evidence categories.
- Path serialization policy: Windows-safe relative/normalized paths; rejection of path traversal and extra fields in model proposals.

**Not in scope:** Schema migration tooling; database storage; model prompting; terminal formatting.

**Testing:** 
- Round-trip every schema through JSON serialization and deserialization.
- Assert rejection of missing required fields, invalid enums, negative line numbers, reversed line ranges (`start_line > end_line` for replacement), negative memory/time values, and unknown schema versions.
- Assert model edit proposals cannot contain shell commands or additional target file paths.
- Assert `.model_json_schema()` produces compliant, simple JSON Schema draft-07/2020-12 objects accepted by the pinned Ollama structured output engine.

**Acceptance:** Every subsystem exchanges validated, strongly typed structures without unstructured dictionary parsing.

## P1-T3 — Benchmark and select the local inference configuration

**Status:** done

**Definition:** Compare candidate quantized SLMs (one ~1.5B model such as Qwen2.5-Coder-1.5B-Instruct-Q4_K_M and one ~3B model such as Qwen2.5-Coder-3B-Instruct-Q4_K_M) served via local Ollama on the target 8 GB Windows laptop. Establish baseline latency, peak total system RAM, model residency, and diagnostic validity. Define the model lifecycle and context budget. Record exact pinned toolchain and model versions for reproducible evaluation.

**Files:** `docs/evaluation.md`, `tests/bug_samples/initial/`, `tests/fixtures/model_responses/`, `localdev/config.py`.

**In scope:** 
- Verification of completely offline inference through local Ollama HTTP endpoint (`http://127.0.0.1:11434`).
- Pinned evaluation environment: record exact versions of Python, Pydantic, Ruff, psutil, Ollama, and candidate models.
- Separated token budgets: 2,048-token total context window (`num_ctx: 2048`), 1,200-token application prompt budget (input limit for system prompt, evidence, and target excerpts), 600-token output budget (`num_predict: 600`), and 248-token application-level safety margin (for protocol framing, JSON schema definition, and tokenization uncertainty), enforcing `prompt_budget + output_budget + application_safety_margin <= context_window`. The 248 tokens represent an application safety margin, not a platform-reserved context partition.
- Real Ollama JSON Schema structured output: configure Ollama requests with Pydantic-generated JSON Schema objects via the `format` parameter (`format: DiagnosisRecord.model_json_schema()`) to enforce grammar-constrained decoding at the token level, using intentionally simple production schemas tested directly against the pinned Ollama version.
- Application-side Pydantic validation after generation with strict type checks, rejecting malformed responses and triggering a single retry before abstention.
- Tokenizer validation: validate and calibrate token estimation against the actual model's tokenizer (using HuggingFace `tokenizers` or exact tokenizer vocabulary corresponding to the selected model family) to establish accurate token counts and calibrate heuristic fallback limits.
- Observability: capture and report actual Ollama usage metrics (`prompt_eval_count`, `eval_count`, `prompt_eval_duration`, `eval_duration`) in execution metadata.
- Strict memory separation: measure Ollama server process/model memory residency separately from Python CLI/runner process-tree RSS and system-wide committed RAM.
- Model lifecycle policy: configure explicit model residency via Ollama `keep_alive` parameter (e.g., prompt unload via `keep_alive: 0` or short keep-alive) so model memory can be released before memory-intensive profiling or after command completion.
- Evaluation of cold vs warm inference latency on a representative set of 10 bug samples.

**Not in scope:** Model fine-tuning; cloud API fallbacks; testing more than two model weight tiers; simultaneous multi-model execution.

**Testing:** 
- Execute inference benchmark script three times per candidate model.
- Validate token counts across benchmark prompts against the actual model's tokenizer, measuring empirical variance against heuristic estimates.
- Test production Pydantic JSON schemas directly against the pinned Ollama engine to verify error-free decoding.
- Measure peak system RAM, Ollama process RSS, and response validity against the 8 GB budget.
- Disconnect network adapter after model download and verify 100% offline execution.
- Record structured metrics and pinned versions in `docs/evaluation.md` and select primary MVP model and low-memory fallback.

**Acceptance:** One default model and one low-memory fallback are documented with measured benchmarks and exact versions; memory lifecycle policy prevents system memory exhaustion on 8 GB hardware.

---

# Phase 2 — CLI, target validation, sessions, and reporting

## P2-T1 — Build command parsing and stable exit semantics

**Status:** done

**Definition:** Implement the command-line interface for `info`, `detect`, `analyse`, `debug`, `fix`, `complexity`, and `profile`, enforcing exactly one positional Python source target, handling secondary argument flags, and defining stable exit codes.

**Files:** `localdev/cli.py`, `localdev/reporting/exit_codes.py`, `localdev/errors.py`, `tests/unit/test_cli_parsing.py`, `tests/integration/test_cli_help.py`.

**In scope:** 
- Positional argument validation: exactly one Python source target required.
- Secondary input flags: `--input` (for profiling JSON arguments), `--expected-stdout`, `--expected-exit` (for validation), `--stdin-file`. Secondary inputs are validated as data inputs, not source targets.
- Noninteractive write authority: `--apply` flag for `fix` command (provides explicit write authority without interactive prompt, while preserving all validation and safety checks; no `--force` bypass flag).
- Argument separator `--` for passing arguments to target execution.
- Selector syntax parsing: `file.py::function_name`.
- Stable exit codes: `0` (success), `1` (target failure / bug diagnosed), `2` (usage/CLI error), `3` (target validation / IO error), `4` (timeout / resource breach), `5` (model / inference error), `6` (abstention / unsupported case).
- Actionable error messages without Python tracebacks for expected user errors.

**Not in scope:** Executing analysis, runtime, or patching; shell string parsing; supporting multiple source targets.

**Testing:** 
- Parameterized tests covering missing target, multiple targets, unknown commands, invalid flags, malformed selectors, and arguments following `--`.
- Verify that `--apply` without `fix` command produces a clean usage error.
- Help text snapshot tests for all commands.

**Acceptance:** CLI strictly rejects ambiguous or multiple source targets before any file is accessed or process spawned.

## P2-T2 — Enforce the single-file target and preserve file metadata facts

**Status:** done

**Definition:** Resolve and validate the explicitly supplied target path, collecting an immutable record of absolute path, file size, SHA-256 hash, text encoding, UTF-8 BOM, newline style (CRLF vs LF), trailing newline presence, read-only status, and reparse-point/symlink status.

**Files:** `localdev/agent/permissions.py`, `localdev/agent/session.py`, `localdev/constants.py`, `tests/unit/test_target_validation.py`, `tests/boundary_samples/`.

**In scope:** 
- Regular file verification (reject directories, special devices, and non-existent paths).
- Source size limit: reject files exceeding maximum configured threshold (default: 256 KB).
- Encoding detection consistent with PEP 263 coding declarations, defaulting to UTF-8.
- Representation detection: UTF-8 BOM presence, newline style (`\r\n` vs `\n`), trailing newline presence.
- Security attributes: detect and report Windows read-only attribute and reparse points (symlinks, NTFS junctions). Symlinks/reparse points are rejected as write targets.
- SHA-256 computation: record baseline SHA-256 hash for compare-before-replace protection against stale edits.
- Document that SHA-256 check guards against stale edits, not concurrent filesystem CAS.

**Not in scope:** Directory scanning; following symlinks to edit targets; modifying file permissions; reading neighboring files.

**Testing:** 
- Comprehensive boundary tests: nonexistent file, directory path, empty file, oversized file, filenames with spaces and Unicode characters, UTF-8 with/without BOM, UTF-8 with CRLF/LF, missing trailing newline, declared legacy encodings (e.g., `iso-8859-1`), read-only files, and Windows symlinks/junctions.
- Assert that only the single specified target is opened.

**Acceptance:** An immutable `TargetRecord` is constructed; invalid or unsafe targets are rejected immediately.

## P2-T3 — Manage isolated session directories and strict cleanup sequence

**Status:** done

**Definition:** Create a unique session directory in `%TEMP%\localdev\session_<id>` for command metadata and execution copies, establish the architecture for same-volume replacement staging, and enforce a strict 5-stage cleanup sequence to guarantee reliable removal without Windows file-locking failures.

**Files:** `localdev/agent/session.py`, `localdev/config.py`, `tests/unit/test_session.py`, `tests/integration/test_session_cleanup.py`.

**In scope:** 
- Unique session UUID generation and manifest creation.
- Session directory ownership marker (`.localdev_session`) to prevent accidental deletion of non-session directories.
- Copying the target file into the session directory (`session_target.py`) for execution, validation, and diagnostic passes.
- Same-volume staging architecture:
  - Explicitly distinguish general session workspace/metadata (which lives under `%TEMP%\localdev\session_<id>`) from the replacement candidate staging required by Win32 `ReplaceFileW`.
  - Storing a session directory under `%TEMP%` does NOT imply that the final replacement candidate is stored there.
  - When preparing for atomic replacement, determine the target file's filesystem volume and establish a staging directory on that exact volume (e.g., target `D:\project\foo.py` stages replacement candidate at `D:\<localdev-staging>\<session-id>\candidate.py`).
  - Native backup path (`D:\project\foo.py.bak`) must also reside on the target's volume.
  - Cross-volume replacement attempts must fail closed.
- Strict 5-stage cleanup sequence:
  1. Stop/terminate the target process tree (via Windows Job Object or recursive `psutil` termination).
  2. Close pipes and other relevant process I/O handles (stdin, stdout, stderr, and open file handles).
  3. Wait for target/worker process termination (`WaitForSingleObject` / `process.wait()`).
  4. Close Job Object / process handles (`CloseHandle`).
  5. Delete the session directory and any same-volume temporary staging locations.
- Configurable retention flag (`--keep-session`) for debugging failed runs.

**Not in scope:** Persistent session databases; cloud synchronization; deleting directories lacking the ownership marker.

**Testing:** 
- Test standard session lifecycle, exception paths, and simulated Ctrl+C interruption.
- Simulate child processes holding files open and verify that the 5-stage cleanup sequence waits for process exit and handle closure before deleting directories, avoiding `PermissionError` (`ERROR_SHARING_VIOLATION`).
- Test that missing ownership marker aborts deletion.
- Test same-volume staging resolution: verify candidate staging matches target volume across different drive letters (e.g., C: vs D:).
- Verify unrelated files in `%TEMP%` remain untouched.

**Acceptance:** No temporary files remain after command completion; cleanup ordering eliminates Windows file sharing violations; same-volume staging invariant is preserved.

## P2-T4 — Implement sanitized terminal and JSON reporters

**Status:** done

**Definition:** Render human-readable output to the terminal with active ANSI/VT escape sanitization, and produce a deterministic, machine-readable JSON envelope adhering to `schemas.py` that preserves raw underlying data.

**Files:** `localdev/reporting/terminal.py`, `localdev/reporting/sanitizer.py`, `localdev/reporting/json_reporter.py`, `tests/unit/test_terminal_reporter.py`, `tests/unit/test_sanitizer.py`, `tests/unit/test_json_reporter.py`.

**In scope:** 
- Terminal reporter: structured presentation of status, evidence, limitations, unified diffs, units, and validation levels.
- Terminal control-sequence sanitization: sanitize all user source text, stdout, stderr, tracebacks, and model-generated prose before terminal rendering by stripping or escaping ANSI escape sequences (`\x1b[...]`), virtual terminal command sequences (OSC, CSI, cursor manipulation, screen clearing), and control characters (`\x00`–`\x08`, `\x0b`–`\x1f`, `\x7f`), while preserving standard formatting newlines and tabs.
- JSON reporter: serialize the versioned JSON envelope (`schema_version: "1.0"`), preserving the exact, un-sanitized underlying raw strings (properly escaped per RFC 8259).
- Windows console compatibility: graceful fallback for console code pages unable to render specific Unicode glyphs.

**Not in scope:** Rich HTML/web reports; interactive curses/TUI interfaces; conflating terminal display sanitization with a Python execution sandbox.

**Testing:** 
- Unit tests feeding terminal sanitizer hostile strings containing CSI cursor reset, screen clear, OSC title set, and null bytes; verify terminal output is sanitized.
- Unit tests asserting JSON reporter preserves raw strings verbatim.
- Snapshot tests for successful, failure, timeout, and abstention reports.
- Redirection tests piping output to files and non-TTY stdout.

**Acceptance:** Terminal output is safe against control sequence injection; JSON output is schema-valid and preserves data fidelity.

---

# Phase 3 — Language detection and adapter architecture

## P3-T1 — Define the language adapter contract

**Status:** done

**Definition:** Create an abstract base class defining the language adapter interface (detection confidence, syntax checking, AST fact extraction, diagnostics, execution preparation, complexity analysis, and candidate validation), and implement the Python adapter registry.

**Files:** `localdev/languages/base.py`, `localdev/languages/python/adapter.py`, `localdev/agent/orchestrator.py`, `tests/unit/test_adapter_contract.py`.

**In scope:** 
- Strongly typed abstract method signatures returning validated schemas.
- Adapter capability reporting.
- Python adapter implementation skeleton.
- Dependency injection support for mock adapters in orchestrator unit tests.

**Not in scope:** Implementing non-Python adapters; dynamic third-party plugin loading.

**Testing:** 
- Test orchestrator flows using a mock adapter.
- Verify incomplete adapter subclasses cannot be instantiated.
- Verify orchestrator depends strictly on the abstract adapter contract.

**Acceptance:** Python-specific analysis is decoupled behind the adapter contract, allowing independent testing.

## P3-T2 — Implement layered language detection

**Status:** done

**Definition:** Detect whether the target is supported Python using non-executing signals: file extensions (`.py`, `.pyw`), standard Python shebang lines, and syntax compilation check (`ast.parse`).

**Files:** `localdev/languages/detector.py`, `localdev/languages/python/adapter.py`, `tests/unit/test_language_detector.py`, `tests/boundary_samples/languages/`.

**In scope:** 
- Extension inspection (`.py`, `.pyw`).
- Shebang inspection (e.g., `#!/usr/bin/env python3`, `#!python`).
- AST parse validation (`ast.parse` in `exec` mode) without code execution.
- Confidence scoring: `CERTAIN`, `PROBABLE`, `UNSUPPORTED`.
- Explicit conflict and non-Python handling.

**Not in scope:** Executing target code to detect language; heuristic guessing of non-Python source; converting unsupported languages to Python.

**Testing:** 
- Test valid Python files, syntax-broken `.py` files, extensionless scripts with Python shebangs, Python files with misleading extensions, binary files, HTML/JS files, and empty files.
- Assert language detection never spawns a subprocess.

**Acceptance:** Detection reliably classifies Python targets and safely abstains on unsupported or ambiguous inputs without execution.

## P3-T3 — Wire `info` and `detect` end to end

**Status:** done

**Definition:** Integrate CLI parsing, target validation, session management, language detection, and sanitized reporting to deliver the `info` and `detect` commands.

**Files:** `localdev/cli.py`, `localdev/agent/orchestrator.py`, `tests/integration/test_info_command.py`, `tests/integration/test_detect_command.py`.

**In scope:** 
- `localdev info <target>`: report file size, lines, SHA-256, encoding, BOM, newline style, trailing newline, read-only status, reparse-point status, and language detection result.
- `localdev detect <target>`: report detected language, confidence, and reason codes.
- Both terminal and JSON reporting modes.

**Not in scope:** AST analysis, Ruff diagnostics, model inference, or code execution.

**Testing:** 
- Integration tests executing `localdev info` and `localdev detect` from PowerShell against valid, invalid, Unicode-named, and non-Python fixtures.
- Verify target file contents, timestamps, and directory remain completely unmodified.

**Acceptance:** `info` and `detect` commands operate end-to-end as stable, non-modifying commands.

---

# Phase 4 — Python syntax, AST, and isolated Ruff evidence

## P4-T1 — Decode and syntax-check Python without source-side effects

**Status:** done

**Definition:** Decode the source file using PEP 263 encoding declarations (defaulting to UTF-8) and validate syntax using `compile(..., mode="exec", flags=ast.PyCF_ONLY_AST)` without importing, executing, or emitting bytecode.

**Files:** `localdev/languages/python/syntax.py`, `localdev/languages/python/adapter.py`, `tests/unit/test_python_syntax.py`, `tests/bug_samples/syntax/`.

**In scope:** 
- Detection of `SyntaxError` and `IndentationError`.
- Extraction of exact 1-based line number, column offset, end line/column, error message, and source line text.
- Bytecode emission suppression (`sys.dont_write_bytecode = True`).

**Not in scope:** Semantic verification; runtime execution; auto-formatting; fixing syntax errors.

**Testing:** 
- Test valid files, syntax errors, indentation errors, invalid tokens, declared encodings, UTF-8 BOM, CRLF line endings, and code that would execute side-effects if imported.
- Assert that no `.pyc` files or `__pycache__` directories are created.

**Acceptance:** Syntax checking provides precise error locations deterministically with zero source or filesystem side-effects.

## P4-T2 — Extract bounded AST facts and source ranges

**Status:** done

**Definition:** Parse valid Python AST to extract module-level functions, classes, methods, parameters, loops, branches, calls, returns, and precise 1-based line spans for context building, complexity analysis, and selector matching.

**Files:** `localdev/languages/python/ast_analyser.py`, `localdev/languages/python/selectors.py`, `tests/unit/test_ast_analyser.py`, `tests/complexity_samples/ast/`.

**In scope:** 
- Discovery of functions, async functions, classes, and methods.
- Qualified name resolution (e.g., `ClassName.method_name`).
- Inclusive 1-based source line ranges (`start_line`, `end_line`).
- Function selector matching (`file.py::function_name`).
- Syntax error short-circuit (return clean syntax error fact without AST traversal).

**Not in scope:** Cross-file symbol resolution; type inference; executing decorators; supporting nested functions in selectors.

**Testing:** 
- Test functions, classes, methods, async functions, decorators, multiline signatures, comprehensions, and nested functions.
- Verify all reported line ranges fall within file boundaries.

**Acceptance:** AST facts provide reliable structural anchors for context building and complexity analysis without executing code.

## P4-T3 — Run Ruff in isolated single-file mode with zero project cache pollution

**Status:** done

**Definition:** Invoke the Ruff linter against the explicit session target copy using isolated configuration, argument lists, `shell=False`, and cache suppression (`--no-cache`), enforcing a defense-in-depth policy to ensure that `localdev` leaves zero unexpected Ruff cache artifacts in the user's target project or its relevant parent directories.

**Files:** `localdev/languages/python/diagnostics.py`, `localdev/languages/python/adapter.py`, `tests/unit/test_ruff_parser.py`, `tests/integration/test_ruff_isolation.py`.

**In scope:** 
- Execution command: `ruff check --isolated --no-cache --output-format=json <session_target_copy.py>`.
- Defense-in-depth workspace protection:
  - Invocation on isolated session copies outside the user project tree.
  - `--isolated` flag ignores any `pyproject.toml`, `ruff.toml`, or `.ruff.toml` in the target's directory tree.
  - `--no-cache` flag instructs Ruff not to create or update cache directories.
  - Explicit artifact assertions verifying that zero `.ruff_cache` directories or unexpected files are left in the user's target project directory or its relevant parent directories.
- Normalization of Ruff JSON output into `DiagnosticRecord` schemas (rule code, message, 1-based start/end line and column, severity).
- Timeout handling (default: 5 seconds) and missing-binary handling.

**Not in scope:** Ruff auto-fixes (`--fix`); directory scanning; project configuration inheritance; treating all linter warnings as fatal.

**Testing:** 
- Place a conflicting `pyproject.toml` (with incompatible Ruff rules) beside the fixture and verify it is completely ignored.
- Verify through post-execution filesystem audit that running Ruff produces no `.ruff_cache` directory in or beside the target file's directory or its parent tree.
- Test clean files, files with multiple findings, timeout handling, and malformed output handling.

**Acceptance:** Ruff executes in strict single-file isolation, produces normalized diagnostics, and leaves zero unexpected cache artifacts in the user's target project.

## P4-T4 — Deliver the `analyse` workflow

**Status:** done

**Definition:** Integrate syntax validation, AST fact extraction, and isolated Ruff diagnostics into the deterministic `analyse` command, formatting results for terminal and JSON output.

**Files:** `localdev/agent/orchestrator.py`, `localdev/agent/evidence.py`, `localdev/cli.py`, `tests/integration/test_analyse_command.py`.

**In scope:** 
- Workflow: validate target → syntax check → if valid, extract AST facts and run isolated Ruff.
- Short-circuit: syntax errors immediately skip Ruff and AST extraction.
- Output: clear summary of syntax status, AST declarations, and normalized diagnostics.
- Completely deterministic; zero model dependency.

**Not in scope:** Code execution; model diagnosis; patch generation.

**Testing:** 
- Integration tests on clean files, files with linter warnings, syntax-broken files, and oversized files.
- Verify offline operation and absence of temporary artifacts.

**Acceptance:** `localdev analyse <target>` provides comprehensive deterministic static evidence without executing or modifying the target.

---

# Phase 5 — Basic Windows execution and traceback parsing

## P5-T1 — Build controlled execution requests and runtime environment

**Status:** done

**Definition:** Implement subprocess request construction for target execution using argument lists, `shell=False`, the selected Python executable, flags `-E`, `-B`, and `-P`, user invocation directory as default working directory (`cwd`), and the environment variable allowlist defined in the Runtime and Isolation Contract.

**Files:** `localdev/execution/runner.py`, `localdev/execution/environment.py`, `localdev/execution/limits.py`, `tests/unit/test_execution_request.py`.

**In scope:** 
- Execution command formulation: `[python_path, "-E", "-B", "-P", session_target_copy_path, *target_args]`.
- Enforcing `-E` to ignore all `PYTHON*` variables (`PYTHONPATH`, `PYTHONHOME`, etc.).
- Enforcing `-P` (`PYTHONSAFEPATH`) to prevent automatic insertion of potentially unsafe ambient script or current working directory paths into `sys.path`. Remaining `sys.path` entries are determined by the Python environment (stdlib, site-packages, `.pth` additions); target code may modify `sys.path` at runtime. `-P` reduces accidental ambient imports rather than creating a hermetic import sandbox.
- Working directory (`cwd`): set to the user invocation directory by default, preserving relative path expectations for files opened by user code (not the session directory).
- `__file__` definition and normalization: target executes from the relocated temporary session copy (`session_target.py` in session dir); runtime `__file__` points to the session copy, meaning package identity and resource access relative to `__file__` (e.g., `Path(__file__).parent / "resource.json"`) do not behave identically to execution of the original file (the package tree is not reconstructed and sibling resources are not copied). Traceback parsing normalizes session copy paths back to canonical user target paths in diagnostic reports.
- Environment allowlist: inherit only `SYSTEMROOT`, `SYSTEMDRIVE`, `PATH`, `PATHEXT`, `TEMP`, `TMP`, `COMSPEC`, and `USERNAME`.
- Rejection of shell metacharacter expansion (`shell=False` always).
- Stdin redirection from string or file.

**Not in scope:** Shell command pipelines; filesystem virtualization; network sandboxing; arbitrary package installation; claiming the relocated session copy reproduces the original package-tree or resource environment.

**Testing:** 
- Unit tests inspecting generated execution commands, `cwd`, and environments.
- Verify that parent environment variables (like `PYTHONPATH=C:\malicious`) are stripped.
- Verify that shell metacharacters (`&`, `|`, `<`, `>`, `;`) passed in arguments remain literal arguments and cannot trigger shell execution.
- Explicit boundary tests covering:
  - `os.getcwd()` (verifying it matches user invocation directory).
  - `__file__` (verifying it points to the temporary session copy path).
  - relative file access from `cwd` (verifying files opened relative to invocation directory resolve correctly).
  - `Path(__file__).parent` (verifying it evaluates to the session directory rather than the original source directory).
  - `sys.path` inspection (verifying `-P` suppresses ambient `cwd` and script directory auto-prepending, while preserving stdlib and site-packages).
  - sibling-module import behavior (verifying failure when relying on ambient `cwd` prepending without `sys.path` modification, and success when script programmatically modifies `sys.path` or runs in package tree).
  - behavior when a script depends on resources beside the original file (verifying failure when accessed relative to `Path(__file__).parent` without copying, demonstrating intentional single-target limits).

**Acceptance:** Execution requests are strictly deterministic, isolated from host `PYTHON*` variables, and incapable of shell command injection.

## P5-T2 — Capture output with timeout and byte limits

**Status:** done

**Definition:** Execute the target subprocess while concurrently draining stdout and stderr pipes, enforcing wall-clock timeout and combined output byte limits, and preserving partial output upon breach.

**Files:** `localdev/execution/runner.py`, `localdev/execution/output_capture.py`, `localdev/execution/limits.py`, `tests/windows/test_runner_limits.py`, `tests/bug_samples/runtime/`.

**In scope:** 
- Default wall-clock timeout: 10 seconds (configurable).
- Default output byte cap: 512 KB combined stdout/stderr (configurable).
- Asynchronous pipe draining preventing pipe buffer deadlocks.
- Retention of partial stdout/stderr when timeout or output cap occurs.
- Setting explicit boolean flags `timed_out` and `output_truncated` in execution result.

**Not in scope:** Interactive terminal emulation; unlimited log capture.

**Testing:** 
- Test normal termination, non-zero exit code, infinite loops (timeout trigger), stdout floods (>512 KB), stderr floods, mixed output, and blocking stdin reads.
- Assert parent process never deadlocks and memory consumption stays bounded during output floods.

**Acceptance:** Subprocess execution reliably terminates on limit breaches, capturing partial evidence without deadlocking.

## P5-T3 — Terminate the process tree with psutil fallback

**Status:** done

**Definition:** Implement reliable descendant process discovery and termination using `psutil` as a baseline fallback mechanism, ensuring child and grandchild processes are cleaned up upon timeout or termination.

**Files:** `localdev/execution/process_tree.py`, `localdev/execution/runner.py`, `tests/windows/test_process_tree.py`, `tests/boundary_samples/processes/`.

**In scope:** 
- Recursive discovery of child and grandchild processes.
- Two-stage termination: graceful `terminate()` followed by forced `kill()`.
- Process exit waiting with timeout to prevent zombie processes.
- Handle-closed checks before returning control.

**Not in scope:** Windows Job Objects (implemented in Phase 6); claiming protection against processes that intentionally break away.

**Testing:** 
- Test fixtures that spawn child and grandchild processes.
- Trigger timeout and verify that all descendant PIDs are terminated.
- Verify that unrelated parent and sibling processes are not terminated.

**Acceptance:** Standard descendant processes are reliably terminated upon execution cancellation or timeout.

## P5-T4 — Parse tracebacks and deliver `debug` execution evidence

**Status:** done

**Definition:** Parse Python execution tracebacks into structured frames, classifying frames as target-local versus external, extracting exception type and message, normalizing session copy `__file__` paths to the canonical target, and delivering deterministic evidence for the `debug` command.

**Files:** `localdev/languages/python/traceback_parser.py`, `localdev/agent/evidence.py`, `localdev/agent/orchestrator.py`, `tests/unit/test_traceback_parser.py`, `tests/integration/test_debug_command.py`.

**In scope:** 
- Parsing standard and chained (`raise ... from ...`) Python tracebacks.
- Target frame matching and normalization: identify frames corresponding to the target source file (by session copy path) and normalize paths back to the canonical user target path.
- External frame identification: mark frames originating in standard library or third-party packages as `external`, recording module name and line number without reading external source files.
- Original error signature generation: `(exception_type, normalized_message, top_target_file, top_target_line)`.
- `localdev debug <target>` deterministic workflow (execution + traceback evidence without SLM).

**Not in scope:** Inspecting or modifying external library source code; native C crash debugging.

**Testing:** 
- Test single exceptions, chained exceptions, syntax errors at runtime, recursion depth errors, and exceptions originating in external libraries.
- Assert that external source files are never read or opened.
- Integration test running `localdev debug` against bug samples.

**Acceptance:** Tracebacks are parsed into structured evidence, distinguishing target-local faults from external dependencies.

---

# Phase 6 — Windows Job Objects and memory reporting

## P6-T1 — Add a Windows Job Object lifecycle wrapper

**Status:** done

**Definition:** Wrap the native Windows Job Object API via `ctypes` to enforce process-tree containment with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, handling handle management, process assignment, and nested-job environments.

**Files:** `localdev/execution/windows_job.py`, `localdev/errors.py`, `tests/windows/test_windows_job.py`.

**In scope:** 
- Native Win32 API bindings: `CreateJobObjectW`, `SetInformationJobObject`, `AssignProcessToJobObject`, `CloseHandle`, `TerminateJobObject`.
- Basic limit configuration: `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
- Handle management: deterministic handle closure during cleanup.
- Nested Job Object handling: accommodate execution inside environments where the parent process is already in a job (e.g., VS Code integrated terminal, Windows Terminal, CI runners); handle `ERROR_ACCESS_DENIED` (code 5) or utilize `JOB_OBJECT_LIMIT_BREAKAWAY_OK` where permitted. Explicitly document that in nested environments such as the VS Code integrated terminal where outer jobs lack breakaway permissions, Job Object assignment fails by design and the `psutil` fallback is the **expected default containment mechanism** (not a rare edge case).
- Clear error translation and reporting.

**Not in scope:** AppContainer tokens; Windows security integrity levels; non-Windows implementations.

**Testing:** 
- Native Windows tests creating a job, assigning a process, and verifying that closing the job handle terminates the process and its descendants.
- Test behavior in standard PowerShell, VS Code terminal, and nested job simulation.
- Verify handle leak prevention across repeated job creation cycles.

**Acceptance:** Windows Job Objects provide robust process-tree termination with clean handle management.

## P6-T2 — Integrate Job Objects with safe fallback policy

**Status:** done

**Definition:** Integrate Job Object control into the execution runner, preferring Job Objects for process containment and falling back to `psutil` when running in restricted environments, reporting the active control backend.

**Files:** `localdev/execution/runner.py`, `localdev/execution/windows_job.py`, `localdev/config.py`, `tests/windows/test_job_runner_integration.py`.

**In scope:** 
- Backend selection: attempt Job Object assignment; if assignment fails (e.g., nested parent job without breakaway permissions, common in VS Code terminals), seamlessly fall back to `psutil` process-tree management as the primary expected containment backend.
- Execution metadata: record active backend (`windows_job` or `psutil_fallback`) in execution result.
- Configurable strict mode: `--fail-on-job-failure` flag to fail closed if Job Object creation fails.

**Not in scope:** Security equivalence between backends; claiming Job Objects prevent hostile code escapes.

**Testing:** 
- Rigorously test the `psutil` fallback path (simulating nested job environments without breakaway permissions) to ensure QA does not under-test this primary containment mechanism used by developer IDE environments like VS Code; verify full child/grandchild tree discovery, graceful-then-forced kill sequence, and timeout enforcement under `psutil` alone.
- Test successful Job Object containment of multi-process trees when running un-nested.
- Verify `--fail-on-job-failure` aborts execution when Job Object assignment fails.

**Acceptance:** Process containment is maximized via Job Objects while maintaining graceful, observable fallback in constrained environments.

## P6-T3 — Measure and label process-tree memory

**Status:** done

**Definition:** Periodically sample the RSS memory of the target process tree during execution, reporting the approximate peak RSS and clearly distinguishing process RSS from Python heap allocations, Job Object limits, and external Ollama service residency.

**Files:** `localdev/execution/process_tree.py`, `localdev/execution/runner.py`, `localdev/schemas.py`, `tests/windows/test_process_memory.py`.

**In scope:** 
- Periodic background RSS sampling (default: 20 ms interval).
- Aggregating parent and descendant process RSS.
- Reporting `approximate_peak_process_tree_rss_bytes` with clear approximation metadata.
- Explicit distinction in schemas and reports between:
  1. Target Python process-tree RSS.
  2. Windows Job Object memory limits.
  3. Python `tracemalloc` heap allocations (Phase 11).
  4. Ollama server and model memory residency.
  5. Total system committed RAM.

**Not in scope:** Microsecond-accurate instantaneous peak guarantees; attributing external Ollama memory to the target process.

**Testing:** 
- Test known memory allocation patterns in parent and child processes.
- Verify peak RSS reflects allocation increases within acceptable sampling tolerance.
- Handle process exit races during sampling without raising unhandled exceptions.

**Acceptance:** Process-tree memory is reported with explicit units, approximation labels, and strict architectural separation from model memory.

---

# Phase 7 — Evidence-grounded local SLM diagnosis

## P7-T1 — Implement the inference abstraction and Ollama client

**Status:** done

**Definition:** Implement a local HTTP inference client for Ollama (`http://127.0.0.1:11434`), enforcing separated token budgets (2,048 context window, 1,200 prompt budget, 600 output budget, 248 application safety margin), structured JSON Schema constrained requests using simple Pydantic schemas tested against the pinned Ollama engine, application-side Pydantic validation, error handling, usage metrics capture, and model lifecycle control.

**Files:** `localdev/inference/base.py`, `localdev/inference/ollama_client.py`, `localdev/config.py`, `tests/unit/test_ollama_client.py`, `tests/integration/test_ollama_smoke.py`.

**In scope:** 
- Local Ollama API integration (`/api/chat` or `/api/generate`).
- Separated token budgets in request options: 2,048-token total context window (`num_ctx: 2048`), 600-token output budget cap (`num_predict: 600`), with an application-enforced prompt budget of 1,200 tokens leaving a 248-token application safety margin for framing/schema/tokenization overhead.
- Real Ollama JSON Schema structured output: pass simple Pydantic-generated JSON Schema objects via the `format` parameter (`format: DiagnosisRecord.model_json_schema()`), activating Ollama's grammar-constrained decoding at the token level using straightforward schemas (objects, primitives, arrays, enums, simple nested objects) tested against the pinned Ollama version.
- Application-side Pydantic validation: perform strict Pydantic validation (`DiagnosisRecord.model_validate_json(...)`) on model output immediately upon receipt; reject malformed or schema-invalid responses.
- Observability and usage metrics: capture and report actual Ollama metrics (`prompt_eval_count`, `eval_count`, `prompt_eval_duration`, `eval_duration`) in execution metadata (or `null` with warning if omitted by backend).
- Non-execution invariant: no free-form model prose or structured output is ever treated as executable instructions, shell commands, or direct file mutations.
- Offline verification: verify endpoint is local (`127.0.0.1` / `localhost`); refuse external URLs.
- Model lifecycle management: support explicit `keep_alive` parameter (e.g., `keep_alive: 0` to unload model immediately when exiting inference phase, or short keep-alive) to release model RAM before memory-heavy operations.
- Clear error handling: server not running, model not pulled, context exceeded, HTTP timeout.

**Not in scope:** Automatic model downloading; cloud API fallbacks; streaming UI generation; arbitrarily complex JSON Schema constructs.

**Testing:** 
- Mock client unit tests for connection refused, timeout, invalid JSON response, and model-not-found error.
- Verify request payload strictly sets `num_ctx: 2048`, `num_predict: 600`, and passes simple Pydantic JSON Schema dictionary in `format`.
- Verify Ollama usage metrics (`prompt_eval_count`, `eval_count`) are captured and preserved.
- Smoke test against running local Ollama instance (marked `@pytest.mark.ollama`).

**Acceptance:** The inference client enforces separated token budgets, grammar-constrained JSON Schema generation, and application-side validation, failing gracefully when the model is unavailable.

## P7-T2 — Build compact, boundary-safe model context within prompt budget

**Status:** done

**Definition:** Assemble compact, bounded model prompts from deterministic facts (syntax, AST facts, normalized Ruff diagnostics, parsed traceback frames, and targeted source spans) strictly within a hard 1,200-token prompt budget (reserving 600 tokens for output and an explicit 248-token application safety margin in the 2,048-token context window) using prioritized truncation.

**Files:** `localdev/agent/context_builder.py`, `localdev/agent/evidence.py`, `localdev/inference/prompts.py`, `tests/unit/test_context_builder.py`.

**In scope:** 
- Formal separation of three distinct concepts:
  1. **Model context window:** configured total capacity in Ollama (`num_ctx: 2048`).
  2. **Application prompt budget:** hard upper limit (1,200 tokens) enforced by the context builder for prompt assembly (system prompt, deterministic evidence, code excerpts).
  3. **Maximum generation budget:** hard upper limit on model output (`num_predict: 600`).
  - **Application safety margin:** remaining 248 tokens reserved by the application for protocol overhead, JSON schema representation, tokenizer variance, and chat framing (an application-level safety margin, not a platform-reserved context partition).
  - Explicit guarantee: `prompt_budget (1,200) + output_budget (600) + application_safety_margin (248) <= context_window (2,048)`.
- Implementation enforcement: the context builder strictly enforces `prompt_budget <= 1200` rather than merely documenting it.
- Token estimation and validation: the 3.5 characters-per-token heuristic is strictly an offline fallback; real tokenizer validation against the model's actual vocabulary/tokenizer (calibrated in P1-T3) is required to prevent silent context truncation by the Ollama runtime. The context builder enforces the hard 1,200-token prompt budget using tokenizer counting where available and conservative heuristic budgeting as fallback.
- Priority order for evidence inclusion:
  1. System prompt and strict JSON Schema output instructions.
  2. Primary failure signature (exception class, message, top target frame line).
  3. Target function/method AST source excerpt containing the fault.
  4. Specific Ruff diagnostics targeting lines within that excerpt.
  5. Surrounding context lines within the target function (up to available prompt budget).
  6. Broader AST outline / secondary diagnostics if budget permits.
- Truncation metadata: record `context_window_tokens` (2,048), `prompt_budget_tokens` (1,200), `output_budget_tokens` (600), `application_safety_margin_tokens` (248), `estimated_prompt_tokens`, `was_truncated`, and `omitted_evidence_categories`.
- Strict single-target boundary: prompts include only excerpts from the single target file.
- Defensive framing: enclose user source code in explicit untrusted data delimiters to prevent prompt injection.

**Not in scope:** Sending whole files blindly; reading imported files; unbounded prompts.

**Testing:** 
- Test with small files, large files (>1,000 lines), multiple traceback frames, numerous Ruff diagnostics, and code containing prompt-injection attempts.
- Assert that context builder never exceeds the 1,200-token prompt budget.
- Verify truncation metadata accurately lists omitted categories.

**Acceptance:** Prompts remain strictly within the 1,200-token prompt budget while preserving the highest-priority diagnostic evidence.

## P7-T3 — Validate diagnosis responses and evidence grounding

**Status:** done

**Definition:** Parse model JSON responses against the diagnosis schema via application-side Pydantic validation, verify that cited line numbers and evidence IDs match actual supplied facts, retry once on malformed output, and abstain safely if invalid.

**Files:** `localdev/inference/response_validator.py`, `localdev/schemas.py`, `localdev/inference/prompts.py`, `tests/unit/test_diagnosis_validator.py`.

**In scope:** 
- Schema validation: parse JSON into `DiagnosisRecord` via Pydantic with strict type enforcement (`extra = 'forbid'`); explicitly trap and reject structurally valid JSON with the wrong shape (e.g., integer where string expected, string instead of array, missing required keys, or unexpected keys). Structural shape mismatches are trapped by Pydantic validation errors and routed to the schema-correction retry.
- Production schema simplicity: schemas for model proposals avoid complex features (such as deeply nested polymorphic unions or arbitrary pattern regexes) and are tested against the pinned Ollama engine.
- Evidence grounding verification: every cited evidence ID must exist in the context builder's supplied evidence manifest; reject hallucinated evidence IDs.
- Line range verification: cited lines must fall within the target file bounds.
- Retry policy: if model response is malformed JSON or schema-invalid (including structural shape mismatches), retry exactly once with an explicit schema-correction prompt; if retry fails, emit an explicit abstention report (`DiagnosisAbstention`).
- Non-execution rule: model diagnosis prose and proposals are informational structures and are never treated as executable commands.

**Not in scope:** Free-form prose generation; executing model suggestions.

**Testing:** 
- Unit tests feeding: valid diagnosis, syntactically invalid JSON, structurally valid JSON with the wrong shape (e.g., incorrect field data types, unexpected keys, string where array expected, missing required nested keys), missing required fields, hallucinated evidence IDs, out-of-bounds line numbers, and empty responses.
- Verify that Pydantic validation traps structural shape mismatches and triggers the retry flow, safely falling back to abstention if retry fails.
- Verify that invalid responses never reach the user as valid diagnoses.

**Acceptance:** Only schema-valid, evidence-grounded diagnoses are accepted; ungrounded or malformed responses safely trigger abstention.

## P7-T4 — Integrate static and runtime diagnosis flows

**Status:** done

**Definition:** Wire deterministic evidence collection and local SLM diagnosis into the `analyse` and `debug` command flows, presenting grounded explanations in terminal and JSON outputs.

**Files:** `localdev/agent/orchestrator.py`, `localdev/cli.py`, `localdev/reporting/terminal.py`, `tests/integration/test_diagnosis_flow.py`.

**In scope:** 
- Integrating static diagnosis (`localdev analyse --diagnose <target>`) and runtime diagnosis (`localdev debug --diagnose <target>`).
- Terminal presentation: clear separation between deterministic facts (syntax, Ruff, traceback) and model interpretation.
- Graceful degradation: if Ollama is unreachable or model fails, return deterministic evidence with a warning that diagnosis was unavailable.
- Cross-file abstention: if traceback indicates the fault is in an external library or uninspected dependency, report an explicit limitation.

**Not in scope:** Generating code edits (Phase 8); repository-level analysis.

**Testing:** 
- Integration tests with fake model responses covering successful diagnosis, no-bug diagnosis, external dependency fault, and Ollama offline fallback.
- Verify deterministic evidence is always displayed regardless of inference availability.

**Acceptance:** Diagnosis flows provide grounded explanations alongside deterministic facts, degrading gracefully on failure.

---

# Phase 8 — Structured edit generation and safe application

## P8-T1 — Define and validate bounded edit proposals

**Status:** done

**Definition:** Define and enforce a strict, unambiguous patch schema with frozen indexing rules, validating that proposals target only the single canonical file, do not overlap, and respect tight complexity bounds.

**Files:** `localdev/patching/edit_schema.py`, `localdev/patching/edit_validator.py`, `localdev/inference/response_validator.py`, `tests/unit/test_edit_validator.py`.

**In scope:** 
- Frozen indexing semantics:
  - Line numbers are **1-based** and **inclusive** (`start_line`, `end_line`).
  - Lines are indexed by logical line content.
  - **Replacement:** `start_line <= end_line`; `expected_text` must match lines `[start_line, end_line]` exactly; `replacement_text` contains new lines.
  - **Insertion before line $K$:** `start_line = K, end_line = K - 1`; `expected_text = ""`; `replacement_text` contains lines to insert.
  - **EOF insertion (append):** for file with $N$ lines, `start_line = N + 1, end_line = N`; `expected_text = ""`.
  - **Deletion:** `start_line <= end_line`; `expected_text` matches lines to delete; `replacement_text = ""`.
  - **Empty file (0 lines):** insertion at `start_line = 1, end_line = 0`.
  - **Trailing newline handling:** target file's trailing newline state is preserved unless the edit explicitly modifies the final line.
  - **Line ending normalization:** model outputs replacement text with standard `\n`; applier normalizes replacement text line endings to match the target's detected newline style (`\r\n` or `\n`).
- Ordering and overlap: multiple edits must be strictly ordered by increasing line numbers (`start_line_i > end_line_{i-1}`); overlapping or contiguous ambiguous edits are rejected.
- Edit bounds: maximum 8 edits and maximum 80 total changed lines per proposal.
- Target check: proposal target path must match the explicit target file canonical path.

**Not in scope:** Model-generated unified diff strings; creating/deleting files; editing multiple files.

**Testing:** 
- Comprehensive unit tests covering: valid replace, insert at line 1, insert at EOF, delete lines, empty file insert, overlapping ranges, out-of-order ranges, mismatched `expected_text`, edit count > 8, changed lines > 80, and path traversal attempts.

**Acceptance:** The edit schema is mathematically unambiguous, and any malformed or out-of-bounds proposal is rejected immediately.

## P8-T2 — Apply edits to a temporary copy and render a diff

**Status:** done

**Definition:** Apply validated edits to an in-memory/session candidate copy of the target file, preserving original encoding, BOM, newline style, and trailing newline state, and render a standard contextual unified diff.

**Files:** `localdev/patching/applier.py`, `localdev/patching/diff_renderer.py`, `tests/unit/test_patch_applier.py`, `tests/unit/test_diff_renderer.py`.

**In scope:** 
- Applying edits in reverse line order (or via immutable line array reconstruction) so line index shifts do not corrupt subsequent edits.
- Verifying exact `expected_text` matches against target source lines before applying each chunk. Exact-matching `expected_text` against the target file is explicitly documented and enforced as the primary defense against the model proposing edits based on stale in-session AST facts, drifted context, or hallucinated line offsets; any mismatch aborts candidate generation immediately.
- Preserving file metadata facts: encoding, UTF-8 BOM, newline style (`\r\n` vs `\n`), trailing newline presence.
- Generating a standard 3-line contextual unified diff for user review.
- Preparation for atomic write: ensure candidate content can be written to a dedicated same-volume staging directory when write authority is granted.
- Guarantee: the original source target file on disk remains completely untouched during this task.

**Not in scope:** Writing to the original target file; partial application when an edit chunk fails.

**Testing:** 
- Test application of multi-chunk edits, insertions, deletions, CRLF preservation, UTF-8 BOM preservation, and no-trailing-newline preservation.
- Verify that mismatch in `expected_text` aborts the entire candidate generation.
- Byte-compare original source file before and after candidate creation to prove zero modification.

**Acceptance:** The candidate copy accurately reflects the approved edits with exact metadata preservation, while the original file remains unmodified.

## P8-T3 — Implement confirmed atomic replacement with native ReplaceFileW backup

**Status:** done

**Definition:** Implement the final write stage: after validation and explicit confirmation, re-resolve the target path, reject reparse points, perform compare-before-replace SHA-256 stale-edit detection, stage the replacement candidate on the target's volume, and atomically replace the target file using Windows `ReplaceFileW` with flags set to 0, leveraging its native `lpBackupFileName` parameter for atomic backup creation.

**Files:** `localdev/patching/atomic_write.py`, `localdev/patching/backup.py`, `localdev/agent/permissions.py`, `tests/windows/test_atomic_write.py`.

**In scope:** 
- Explicit write authority: interactive confirmation prompt (`Apply this patch? [y/N]`) or noninteractive `--apply` flag.
- `--apply` semantics: grants write authority in noninteractive/CI mode without prompt; **cannot bypass any validation, hash check, or safety invariant**. There is no `--force` path that skips the safety gates.
- Target re-resolution: re-resolve target path immediately before write; reject if target has become a reparse point (symlink/junction).
- Compare-before-replace SHA-256 stale-edit detection: compute current SHA-256 of target; compare with baseline SHA-256 recorded at analysis start; if mismatched, abort replacement with a clear stale-edit error.
- Explicit concurrency limitation: document that the SHA-256 check provides stale-edit detection against external file modifications, not a true concurrent filesystem compare-and-swap; it does not eliminate the tiny microsecond race window between hash check and `ReplaceFileW`.
- Same-volume staging architecture:
  - Win32 `ReplaceFileW` strictly requires `lpReplacedFileName`, `lpReplacementFileName`, and `lpBackupFileName` to reside on the **same filesystem volume**.
  - While ordinary session metadata lives under `%TEMP%\localdev\session_<id>`, the candidate file used for `ReplaceFileW` MUST be staged on the target's filesystem volume (e.g., target `D:\project\foo.py` stages replacement candidate at `D:\<localdev-staging>\<session-id>\candidate.py`).
  - Native backup path (`D:\project\foo.py.bak`) must also remain on the target's volume.
  - Cross-volume replacement attempts fail closed with an explicit error before calling `ReplaceFileW`.
- Native atomic backup via `ReplaceFileW`:
  - Rather than executing a separate manual backup/copy step (which introduces TOCTOU copy races and out-of-sync failure modes), `localdev` leverages the native `lpBackupFileName` parameter of Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)`.
  - The unsupported flag `REPLACEFILE_WRITE_THROUGH` is omitted per Microsoft Win32 API documentation; flags are passed as `0`.
  - If backup is requested (via `--backup` or default configuration), the backup path (`<target>.bak`) is passed directly as `lpBackupFileName`.
  - The Windows kernel performs the directory entry swap and preserves the original file at the backup destination as part of the atomic replacement operation.
  - If replacement fails, the original file is preserved and no backup file is created or corrupted. If replacement succeeds, the backup is guaranteed to reflect the pre-patch state.
  - If backup is not requested, `lpBackupFileName` is passed as `NULL`.
- Durability limitation: atomic replacement guarantees all-or-nothing directory entry replacement (preventing partial or corrupted file writes), but atomic replacement semantics do not imply power-loss durability without explicit hardware cache flushes; power-loss durability is outside the MVP.

**Not in scope:** Transactional filesystem rollback across power loss; non-atomic fallback writes; bypassing validation via `--force`.

**Testing:** 
- Test interactive confirmation: `y` applies, `n` aborts.
- Test noninteractive `--apply`, verifying validation and hash checks remain fully enforced.
- Test stale SHA-256 abortion (modify target file between analysis and apply).
- Test reparse point rejection.
- Test atomic replacement with native backup enabled: verify target content updates atomically and original content is preserved at `<target>.bak`.
- Test atomic replacement without backup: verify target updates and no backup file is created.
- Test replacement failure simulation (e.g., target file locked by external process): verify original file is unchanged and no partial/corrupted backup exists.
- Test same-volume enforcement: verify candidate staging respects the target volume across volume boundaries (e.g., C: vs D:) and fails closed if cross-volume.
- Test atomic replacement on Windows NTFS/ReFS volumes.

**Acceptance:** Target files are updated only with explicit authority, verified hash matching (stale-edit detection), pre-verified backup, same-volume constraints, and atomic replacement semantics.

## P8-T4 — Deliver `fix` and `--propose-fix` workflows

**Status:** done

**Definition:** Connect diagnosis, edit proposal generation, candidate application, diff rendering, candidate validation (Phase 9), user confirmation, and atomic replacement into the `fix` command workflow.

**Files:** `localdev/agent/orchestrator.py`, `localdev/cli.py`, `localdev/reporting/terminal.py`, `tests/integration/test_fix_workflow.py`.

**In scope:** 
- `localdev fix <target>`: full diagnostic and repair workflow.
- By default, displays diagnosis, proposed unified diff, and validation results, then prompts for confirmation.
- Proposal retry policy: if first proposal fails schema validation or Level A structural validation, request one alternative proposal from model; if second fails, abstain.
- Clean terminal reporting showing validation level achieved before asking user confirmation.

**Not in scope:** Fully autonomous multi-file repair; unbounded edit retry loops.

**Testing:** 
- Integration tests using fake model responses covering: clean patch application, user declined apply, retry on invalid proposal, stale hash abort, and unsupported issue abstention.
- Verify source file is modified only when the complete pipeline passes and user confirms.

**Acceptance:** `localdev fix` provides a safe, interactive, user-controlled repair experience with strict safety guardrails.

---

# Phase 9 — Differential patch validation

## P9-T1 — Implement structural and Ruff baseline comparison (Validation Level A — Static validity)

**Status:** done

**Definition:** Validate candidate syntax and compare normalized static diagnostics before and after applying the patch, certifying Validation Level A (Static validity) when syntax parses cleanly, no new diagnostics are introduced, and any targeted static finding is eliminated.

**Files:** `localdev/patching/applier.py`, `localdev/languages/python/diagnostics.py`, `localdev/agent/evidence.py`, `tests/unit/test_diagnostic_diff.py`.

**In scope:** 
- **Validation Level A — Static validity** definition and requirements:
  1. Candidate source parses successfully (`ast.parse` succeeds with zero syntax errors).
  2. Candidate introduces no new static diagnostics under the configured static-analysis policy (zero newly introduced Ruff diagnostics or syntax errors compared to baseline).
  3. If the original repair target included a specific static diagnostic (e.g., Ruff error or syntax fault), that specific diagnostic must also disappear in the candidate.
  4. Runtime-only repair clarity: a runtime-only fix (targeting an unhandled exception or logic fault on a baseline that had no Ruff findings) can legitimately pass Level A without having had a Ruff diagnostic to eliminate, provided it parses cleanly and introduces zero new static diagnostics.
- Handling shifted lines: account for line number shifts caused by inserted or deleted lines when comparing baseline findings to candidate findings.
- Pre-existing findings tolerance: unrelated pre-existing linter warnings do not fail Level A as long as they are not newly introduced by the patch.

**Not in scope:** Requiring the entire file to become 100% linter-clean; suppressing newly introduced linter warnings.

**Testing:** 
- Test candidate resolving a static finding with unchanged baseline (passes Level A).
- Test runtime-only repair candidate where no static findings existed in baseline, verifying Level A passes if syntax is valid and zero new diagnostics appear.
- Test candidate with shifted line numbers for pre-existing findings.
- Test candidate introducing a new syntax error (must fail Level A).
- Test candidate introducing a new Ruff warning (must fail Level A).

**Acceptance:** Validation Level A cleanly verifies static validity across both static and runtime repairs without rejecting runtime fixes on Ruff-clean baselines.

## P9-T2 — Compare runtime failure signatures on candidate (Validation Levels B and C)

**Status:** done

**Definition:** Execute the temporary candidate copy under identical inputs, environment, and limits as the baseline run, certifying Validation Level B (Failure reproduction removed) when the original runtime exception no longer occurs, or Validation Level C (Clean execution) when execution exits with code 0.

**Files:** `localdev/agent/orchestrator.py`, `localdev/languages/python/traceback_parser.py`, `localdev/schemas.py`, `tests/integration/test_runtime_patch_validation.py`.

**In scope:** 
- Executing only the session candidate copy; the original source file is never executed during validation.
- **Level B — Failure reproduction removed:** the original runtime failure is no longer reproduced (the original runtime exception signature `(exception_type, message, frame)` no longer occurs, even if execution terminates with a different non-zero exit code or different error).
- **Explicit user-facing interpretation:** Level B does NOT mean "the bug is proven fixed." For example, if an original `IndexError` is replaced by a `TypeError`, this candidate formally satisfies Level B (the original failure reproduction was removed) but must fail Level C. User-facing reporting must never imply that achieving Level B proves behavioral correctness.
- **Level C — Clean execution:** candidate exits successfully under the controlled runtime (exit code 0 under identical inputs, environment, and limits).
- Regression detection: candidate triggers a new unhandled exception or wall-clock timeout (fails Level C validation).

**Not in scope:** Assuming exit code 0 guarantees semantic correctness; loosening timeouts to force candidate to pass.

**Testing:** 
- Test candidate where original exception is removed.
- Test candidate where original exception is replaced by another exception (Level B pass, Level C fail).
- Test candidate where execution succeeds with exit code 0 (Level C pass).
- Test candidate that introduces an infinite loop / timeout (validation fail).
- Assert original source file bytes remain unchanged throughout candidate execution.

**Acceptance:** Runtime candidate validation cleanly differentiates between "failure reproduction removed" (Level B) and "clean execution" (Level C).

## P9-T3 — Add explicit behavioural checks (Validation Level D — Behavioral oracle)

**Status:** done

**Definition:** Support user-supplied behavioural assertions (`--expected-stdout` exact or substring, `--expected-exit`) and certify Validation Level D (Behavioral oracle) only when the candidate satisfies the explicit oracle.

**Files:** `localdev/agent/orchestrator.py`, `localdev/schemas.py`, `localdev/reporting/terminal.py`, `tests/integration/test_behaviour_validation.py`.

**In scope:** 
- CLI options: `--expected-stdout <string>`, `--expected-stdout-contains <string>`, `--expected-exit <code>`.
- Level D certification: awarded if and only if Level C is achieved AND all user-specified output/exit expectations are strictly satisfied.
- Strict oracle contract: Level D cannot be awarded without an explicit user-supplied expectation; the system never claims semantic correctness on its own.
- Clear reporting of achieved validation level (`NONE`, `LEVEL_A`, `LEVEL_B`, `LEVEL_C`, `LEVEL_D`).

**Not in scope:** Automatic test suite synthesis; guessing expected output without user guidance.

**Testing:** 
- Test exact stdout match (pass Level D).
- Test substring stdout match (pass Level D).
- Test stdout mismatch (pass Level C, fail Level D).
- Test exit code mismatch (fail Level D).
- Assert Level D is impossible when no expectation options are provided.

**Acceptance:** Validation levels A–D are reported transparently and strictly backed by empirical evidence.

---

# Phase 10 — Static time, auxiliary-space, and output-space analysis

## P10-T1 — Define the restricted cost model, space semantics, and abstention contract

**Status:** done

**Definition:** Establish a formal, restricted static cost model and typed schemas for time, auxiliary space, and output space based on supported CPython runtime semantics, enforcing a strict `conservative + assumption-linked + source-linked + abstention-first` contract.

**Files:** `localdev/languages/python/complexity.py`, `localdev/schemas.py`, `docs/architecture.md`, `tests/unit/test_complexity_cost_model.py`.

**In scope:** 
- Supported asymptotic classes: `O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`, `O(2^n)`, and `UNKNOWN`.
- Closed vocabulary enforcement: the classifier must emit strictly from this frozen schema enumeration; inventing arbitrary complexity strings is prohibited. Any pattern outside this closed vocabulary routes to `UNKNOWN` with an explicit abstention reason code.
- CPython runtime semantics foundation: the cost model is explicitly grounded in the CPython 3.11/3.12 runtime behavior selected by the MVP.
- Explicit distinction of complexity flavors:
  - **Amortized complexity:** operations such as `list.append()` are documented and reported as amortized $O(1)$ time (accounting for dynamic array growth).
  - **Expected/average complexity:** hash-based operations (dictionary and set lookups/insertions) are reported as expected/average $O(1)$ time under explicit documented assumptions (e.g., `"Assumes uniform hash distribution without pathological collisions"`), without claiming a universal worst-case $O(1)$.
  - **Worst-case complexity:** upper bounds for iterative and divide-and-conquer loops where bounds are provable.
- Formal space definitions:
  - **Auxiliary space:** memory allocated during computation for intermediate data structures, temporary variables, recursion call stack frames, and slicing copies that do *not* escape as the function's return value.
  - **Output space:** memory allocated for the final result returned to the caller (e.g., newly allocated lists, dictionaries, sets, tuples, or strings). If a generator or iterator is returned without materializing elements, output space is `O(1)`.
- Mandatory abstention policy: the analyzer must abstain and report `UNKNOWN` with an explicit reason code whenever costs depend on unestablished semantics, including:
  - Unknown or user-defined function/method calls.
  - External library functions.
  - Dynamic dispatch or method overriding.
  - Unknown container implementations or user-defined `__contains__`/`__getitem__`.
  - Type-dependent operations where operand types are not statically provable.
  - Data-dependent loop bounds that cannot be resolved from local constants or parameters.
  - Dynamic, mutual, or unbounded recursion.
  - Unknown collection sizes.
- Prohibit silently assuming arbitrary function calls are `O(1)`.
- Explicit assumption reporting: every complexity assessment must link to cited line numbers and state the exact assumptions (e.g., `"Assumes len(items) is n and dict lookup is expected O(1) under uniform hashing"`).

**Not in scope:** General symbolic algebra; theorem proving; inter-procedural pointer analysis; runtime profiling.

**Testing:** 
- Unit tests verifying table-driven cost rules.
- Verify that unknown function calls trigger abstention (`UNKNOWN_CALL`).
- Verify that data-dependent loops without obvious bounds trigger abstention (`DYNAMIC_BOUNDS`).
- Verify every result includes source lines, complexity flavors, and assumptions.

**Acceptance:** The complexity cost model is formally defined, grounded in CPython runtime semantics, and strictly abstains whenever sound static justification is missing.

## P10-T2 — Analyse loops, nesting, built-ins, and space allocation

**Status:** done

**Definition:** Traverse Python AST facts to evaluate iterative algorithms, combining loop nesting, recognizing common built-in operations, and formally separating auxiliary space from output space.

**Files:** `localdev/languages/python/complexity.py`, `tests/unit/test_complexity_iterative.py`, `tests/complexity_samples/iterative/`.

**In scope:** 
- Sequential loop addition ($O(n) + O(n) = O(n)$).
- Nested loop multiplication ($O(n) \times O(m) = O(nm)$).
- Standard built-in costs: `len()` ($O(1)$), `range()` ($O(1)$ space), `sorted()` / `.sort()` ($O(n \log n)$ time, $O(n)$ space), `list.append()` ($O(1)$ amortized time).
- Membership testing: distinguish `x in list` ($O(n)$ worst-case) from `x in set` / `x in dict` ($O(1)$ expected/average time with explicit assumption).
- Space distinction:
  - Temporary list comprehension consumed in loop: auxiliary space $O(n)$, output space $O(1)$.
  - Generator expression: auxiliary space $O(1)$, output space $O(1)$.
  - Returned list comprehension: auxiliary space $O(1)$ (transient), output space $O(n)$.
  - Slicing: `a[1:]` creates a copy ($O(k)$ time and space).

**Not in scope:** Dynamic runtime tracking; cross-file type inference.

**Testing:** 
- Golden fixture tests for each supported iterative class: `O(1)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(nm)`.
- Test auxiliary vs output space separation: returned list vs temporary list, generator vs comprehension.
- Test `list` membership vs `set` membership.

**Acceptance:** Iterative algorithms are classified consistently with explicit time, auxiliary-space, and output-space metrics.

## P10-T3 — Add bounded recursion analysis and abstention

**Status:** done

**Definition:** Recognize simple, bounded direct recursion patterns (linear decrement, binary divide-and-conquer), account for call-stack depth in auxiliary space, emit `O(2^n)` for binary branching recursion, and abstain on unsupported or dynamic recursive patterns.

**Files:** `localdev/languages/python/complexity.py`, `tests/unit/test_complexity_recursion.py`, `tests/complexity_samples/recursive/`.

**In scope:** 
- Direct linear recursion (e.g., factorial, linear traversal): $O(n)$ time, $O(n)$ auxiliary call-stack space.
- Fixed divide-and-conquer (e.g., binary search structure): $O(\log n)$ time, $O(\log n)$ auxiliary space.
- Binary recursion without memoization (e.g., naive Fibonacci): $O(2^n)$ time, $O(n)$ auxiliary space.
- Call-stack space accounting: recursion depth is explicitly included in auxiliary space.
- Mandatory abstention: abstain on mutual recursion, dynamic branching recursion, or recursion lacking a recognizable static base case.

**Not in scope:** General recurrence relation solving; proving termination of arbitrary recursive code.

**Testing:** 
- Test linear recursion ($O(n)$ time, $O(n)$ space), binary search structure ($O(\log n)$ time, $O(\log n)$ space), and naive binary recursion ($O(2^n)$ time, $O(n)$ space).
- Test mutual recursion fixture and verify clean abstention (`DYNAMIC_RECURSION`).
- Test missing base case fixture and verify clean abstention.

**Acceptance:** Supported recursive patterns receive conservative assessments, and unsupported patterns fail safely via abstention.

## P10-T4 — Deliver the `complexity` command

**Status:** done

**Definition:** Connect AST analysis, cost models, and selector parsing to deliver the `complexity` command, providing file summaries or targeted function-level complexity with source lines, assumptions, and JSON output.

**Files:** `localdev/languages/python/selectors.py`, `localdev/agent/orchestrator.py`, `localdev/cli.py`, `tests/integration/test_complexity_command.py`.

**In scope:** 
- CLI syntax: `localdev complexity <target>` (summary of all top-level functions/methods) or `localdev complexity <target>::<function_name>`.
- Output: time complexity (strictly within supported enum including `O(2^n)`), auxiliary space, output space, confidence, assumptions, and cited line numbers.
- Fully static and deterministic: never imports or executes the target file.
- Clean abstention reporting when complexity cannot be established.

**Not in scope:** Nested function selectors in MVP; profiling execution time.

**Testing:** 
- Integration tests running `localdev complexity` against valid, ambiguous, syntax-broken, and abstention fixtures.
- Verify target file is never executed or imported.
- Verify JSON schema compliance against the closed complexity vocabulary.

**Acceptance:** Users receive conservative, assumption-linked complexity assessments with safe abstention for unsupported code.

---

# Phase 11 — Function profiling

## P11-T1 — Parse selectors and load targets via direct file loading in disposable worker

**Status:** Backlog

**Definition:** Parse function selectors (`file.py::function_name`) and load the target module directly from its file path using `importlib.util.spec_from_file_location` inside a disposable worker subprocess, capturing import metrics and side effects separately.

**Files:** `localdev/profiling/loader.py`, `localdev/profiling/worker.py`, `localdev/languages/python/selectors.py`, `tests/unit/test_profile_selector.py`, `tests/windows/test_profile_loader.py`.

**In scope:** 
- Selector parsing: support top-level functions and class methods (`ClassName.method_name`); reject nested functions in MVP.
- Direct file-based loading: load module directly via `importlib.util.spec_from_file_location("profile_target", target_file_path)` followed by `spec.loader.exec_module(module)`.
- Do NOT use `import target` or modify `sys.path` to include the user project directory.
- Worker isolation: worker runs as an independent subprocess using `-E -B -P`.
- Import accounting: separate measurement of import duration, stdout, stderr, and import-time exceptions/timeouts.
- Do not pretend import is side-effect free: capture and report import-time side-effects.

**Not in scope:** Profiling nested functions; suppressing target import-time code; repository-wide dependency resolution.

**Testing:** 
- Test module function selector, class method selector, missing selector, ambiguous selector, and unsupported nested function selector.
- Test target with import-time stdout/stderr, slow import, and import exception.
- Assert worker crash or import failure does not destabilize the parent CLI.

**Acceptance:** Targets are loaded directly by path in a disposable worker, with import metrics cleanly isolated from function invocation.

## P11-T2 — Define JSON inputs and recreate them for each run

**Status:** Backlog

**Definition:** Parse and validate user-supplied JSON argument files (`--input`), deeply recreating fresh argument structures for every warm-up and measured run to prevent argument mutation contamination.

**Files:** `localdev/profiling/benchmark.py`, `localdev/profiling/worker.py`, `tests/unit/test_profile_inputs.py`, `tests/profiling_samples/inputs/`.

**In scope:** 
- Schema: `{ "args": [...], "kwargs": {...} }`.
- Input validation: validate JSON structure; enforce maximum input size (default: 1 MB) and maximum nesting depth (default: 20 levels).
- Re-creation policy: fresh deep copies of `args` and `kwargs` are reconstructed from raw JSON data for every warm-up and measured run.
- Guard against argument mutation: functions that mutate their input (e.g., `list.sort()`, `dict.pop()`) receive identical fresh arguments on every invocation.

**Not in scope:** Pickle deserialization; arbitrary Python object instantiation; custom constructors.

**Testing:** 
- Test valid args/kwargs, empty input, invalid JSON, oversized input, and deeply nested structures.
- Test a mutating function fixture; assert every run receives the exact initial input state.

**Acceptance:** Profiling arguments are safe, validated, and isolated against intra-benchmark mutation.

## P11-T3 — Measure repeated function time and Python allocations under hot-process semantics

**Status:** Backlog

**Definition:** Execute warm-up and measured function invocations under hot-process semantics in the worker subprocess, measuring execution time with high-resolution timers and tracking Python heap allocations with `tracemalloc`.

**Files:** `localdev/profiling/timer.py`, `localdev/profiling/python_memory.py`, `localdev/profiling/worker.py`, `tests/windows/test_function_timing.py`.

**In scope:** 
- Hot-process semantics:
  - Target module is imported once into the worker.
  - Configured warm-ups (default: 2) and measured runs (default: 7) execute in the same worker/module environment.
  - Reconstruct fresh arguments from JSON for every invocation.
  - Explicitly document and report that module-level state (global variables, caches, `lru_cache`, singletons) may persist across invocations.
  - Document that this measures repeated invocation performance rather than fresh-process startup costs.
- Timing: high-resolution monotonic timer (`time.perf_counter_ns`); calculate median, min, max, and standard deviation.
- Python memory tracking: use `tracemalloc` to record peak Python allocations tracked by `tracemalloc` per invocation (`python_allocations_tracemalloc_bytes`), explicitly noting this measures Python object allocations rather than total OS process memory.
- Error handling: handle function exceptions or timeouts during invocations.

**Not in scope:** Fresh-process-per-call profiling mode in MVP; kernel-level cache flushing; hardware PMU profiling; describing tracemalloc as total function memory.

**Testing:** 
- Test timing on controlled sleep and computation fixtures.
- Test a stateful function (e.g., accumulating global list, `@lru_cache`) and verify report indicates hot-process persistence.
- Test memory allocation heavy function and verify peak tracemalloc-tracked allocations increase predictably.

**Acceptance:** Function timing and peak tracemalloc-tracked Python memory allocations are measured reliably under clearly documented hot-process semantics.

## P11-T4 — Add parent-side process RSS sampling and deliver `profile`

**Status:** Backlog

**Definition:** Monitor the worker subprocess tree externally from the parent process to sample peak process RSS, integrating import metrics, function timings, peak tracemalloc-tracked Python allocations, and process RSS into the `profile` command report.

**Files:** `localdev/profiling/process_memory.py`, `localdev/profiling/benchmark.py`, `localdev/agent/orchestrator.py`, `localdev/cli.py`, `tests/integration/test_profile_command.py`.

**In scope:** 
- External RSS sampling of the worker process tree at 20 ms intervals.
- Integrated reporting explicitly separating:
  1. Target import-time cost (import duration, stdout, and stderr).
  2. Function runtime timing (warm-up and measured run median, dispersion).
  3. Peak tracemalloc-tracked Python memory allocations (`python_allocations_tracemalloc_bytes` per invocation).
  4. Worker process-tree approximate peak RSS (OS-level process footprint).
  5. Hot-process profiling context (module imported once, persistent global/module state) versus fresh-process startup benchmarking.
- Strict separation: do not conflate worker RSS with external Ollama service memory or Python heap allocations.
- Terminal and JSON output.

**Not in scope:** Profiling multiple functions concurrently; extrapolating complexity curves from a single run.

**Testing:** 
- End-to-end integration tests for pure functions, mutating functions, stateful functions, slow imports, and import errors.
- Verify worker process tree is completely terminated and cleaned up after profiling.
- Verify metrics are distinct and labeled correctly in terminal and JSON outputs.

**Acceptance:** `localdev profile` delivers comprehensive, transparent runtime measurements without conflating import, invocation, tracemalloc-tracked Python allocations, and process RSS metrics.

---

# Phase 12 — Evaluation, optimisation, documentation, and release

## P12-T1 — Build the evaluation datasets and harness

**Status:** Backlog

**Definition:** Construct versioned, reproducible evaluation datasets and an automated evaluation harness covering deterministic correctness, SLM diagnosis, patch safety, complexity accuracy, profiling stability, and cleanup. Record exact pinned toolchain and model versions for personal-project evaluation reproducibility.

**Files:** `tests/bug_samples/`, `tests/complexity_samples/`, `tests/profiling_samples/`, `tests/boundary_samples/`, `tools/evaluate.py`, `docs/evaluation.md`.

**In scope:** 
- Pinned toolchain record: record exact versions for Python, Pydantic, Ruff, psutil, Ollama, and selected SLM model files in `docs/evaluation.md`.
- Bug dataset: 60–80 diverse Python bug samples (syntax errors, standard exceptions, logic errors, clean files, external dependency faults).
- Complexity dataset: 35–50 functions covering all supported classes, auxiliary vs output space distinctions, and mandatory abstention cases.
- Profiling dataset: pure, mutating, stateful, and memory-intensive functions with validated JSON inputs.
- Boundary dataset: filenames with spaces/Unicode, CRLF/LF, BOM, missing trailing newlines, read-only files, reparse points.
- Automated harness executing offline evaluations, recording pass/fail rates, latency, memory, and schema validity.

**Not in scope:** Fine-tuning datasets; modifying test fixtures to artificially inflate scores.

**Testing:** 
- Validate dataset manifests and schema compliance.
- Run harness on a subset to verify repeatability.
- Ensure evaluation includes correct programs and unsupported cases, not only known bugs.

**Acceptance:** A comprehensive evaluation harness is versioned and executable offline with verifiable results.

## P12-T2 — Measure quality, safety, performance, and resource budgets

**Status:** Backlog

**Definition:** Execute the complete evaluation harness on native Windows 11 x64 on the target 8 GB laptop, recording accuracy, false positive rates, abstention rates, patch safety, memory usage, and execution latency.

**Files:** `docs/evaluation.md`, `docs/security.md`.

**In scope:** 
- Cold and warm inference latency measurements.
- Memory measurements: measure peak total system RAM, Ollama model residency, and target execution RSS separately.
- Zero boundary violations: verify 0 single-file boundary breaches across the entire suite.
- Patch safety: verify 0 unvalidated source file mutations.
- Complexity accuracy: measure exact class matches and sound abstentions.
- Cleanup verification: verify 0 orphaned processes or leaked session directories using the 5-stage cleanup sequence.

**Not in scope:** Cherry-picking results; claiming unmeasured performance.

**Testing:** 
- Execute evaluation harness twice on Windows 11 x64 to assess consistency.
- Manually audit sample outputs from each category.
- Verify results meet the constraints of the 8 GB RAM target machine.

**Acceptance:** All published metrics are backed by reproducible evaluation data recorded in `docs/evaluation.md`.

## P12-T3 — Optimize within the 8 GB laptop budget

**Status:** Backlog

**Definition:** Optimize prompt construction, memory lifecycle, process scheduling, and output limits based on evaluation findings to ensure responsive, stable operation on an 8 GB Windows laptop.

**Files:** `localdev/config.py`, `localdev/agent/context_builder.py`, `localdev/inference/ollama_client.py`, `localdev/execution/limits.py`, `docs/evaluation.md`.

**In scope:** 
- Strict adherence to the separated token budgets: 2,048-token context window, 1,200-token prompt budget, 600-token generation cap, and 248-token application safety margin (satisfying `prompt + output + margin <= context`).
- Model lifecycle management: trigger Ollama unload (`keep_alive: 0` or short timeout) when exiting inference to free memory before heavy profiling.
- Single-process execution: ensure target runner and profiler never overlap with active inference.
- Tuning RSS sampling interval to balance accuracy and CPU overhead.
- Validating low-memory fallback model configuration (1.5B tier).

**Not in scope:** Relaxing validation or safety invariants to gain performance; loading multiple models concurrently.

**Testing:** 
- Benchmark complete workflows (analyse → debug → fix → complexity → profile) on the target machine.
- Verify total system RAM commit remains within safe limits without paging exhaustion.

**Acceptance:** Workflows execute stably within the 8 GB RAM budget without out-of-memory errors or safety compromises.

## P12-T4 — Complete user, architecture, security, and operations documentation

**Status:** Backlog

**Definition:** Author clear, technically accurate documentation detailing installation, offline configuration, command usage, architecture, security boundaries, Runtime & Isolation Contract, validation levels, and limitations.

**Files:** `README.md`, `docs/architecture.md`, `docs/security.md`, `docs/evaluation.md`, `docs/demo.md`.

**In scope:** 
- Installation instructions for Windows 11 x64 (supported platform: Windows 11 x64 only), Python 3.11 (minimum baseline; tested with 3.11 and 3.12), Ruff, and Ollama.
- Clear statement of personal portfolio/resume project scope (demonstrating technical rigor and clean systems engineering within a bounded single-target platform, not enterprise infrastructure).
- Prominent disclaimers: **"user-owned or trusted code only"** and **"not a security sandbox"**.
- Full explanation of the Runtime and Isolation Contract (single-target scope, runtime imports, `-E -B -P` policy, default user invocation `cwd`, `sys.path` environment behavior, `__file__` session copy semantics and resource limitations, path normalization in reports).
- Explanation of SHA-256 compare-before-replace stale-edit detection, same-volume staging architecture, `ReplaceFileW` native atomic replacement with backup (`lpBackupFileName`, `dwReplaceFlags = 0`) on the same volume, and invariant that `--apply` cannot bypass safety gates (no `--force` bypass).
- Explanation of Validation Levels A–D (Level A Static validity criteria for static vs runtime-only repairs, Level B failure reproduction removal with explicit note that Level B does not prove correctness, Level C clean execution, and Level D behavioral oracle).
- Explanation of hot-process profiling semantics and separated memory metrics (peak tracemalloc-tracked Python allocations vs worker process RSS).
- Documented exit codes, CLI flags, and JSON schema references.

**Not in scope:** Overstating security guarantees; claiming support for unverified features.

**Testing:** 
- Follow documentation from scratch in a clean Windows 11 x64 environment to verify accuracy.
- Documentation linting and link checking.

**Acceptance:** Documentation is comprehensive, technically precise, and completely transparent regarding capabilities and limitations.

## P12-T5 — Rehearse and sign off the final demonstration

**Status:** Backlog

**Definition:** Prepare a clean, self-contained Python demonstration fixture and deliver a structured, four-part presentation script showcasing the primary debug-and-fix workflow, static complexity, function profiling, and honest abstention.

**Files:** `examples/demo.py`, `examples/demo_input.json`, `docs/demo.md`, `tests/integration/test_demo_sequence.py`.

**In scope:** 
- Demo fixture: a dependency-free script containing a reproducible runtime bug, an explicit expected output, an $O(n^2)$ function, and an `if __name__ == "__main__":` entry point.
- Structured demonstration narrative:
  1. **Primary Debug and Repair Workflow:**
     `analyse → debug → diagnose → propose fix → validate candidate → confirm → apply → rerun`
  2. **Static Complexity Analysis Demonstration:**
     Run `complexity` on the target function, highlighting time, auxiliary space, output space, and explicit assumptions.
  3. **Function Profiling Demonstration:**
     Run `profile` with `demo_input.json`, highlighting separated import time, function timing, Python heap allocations (`tracemalloc`), and worker process RSS under hot-process semantics.
  4. **Honest Abstention Demonstration:**
     Demonstrate safe abstention on an unsupported case (e.g., cross-file dependency or dynamic complexity).
- Verified completely offline.

**Not in scope:** Artificially combining all commands into an uninterrupted script; hiding limitations.

**Testing:** 
- Automated test executing the full demo sequence against a temporary fixture copy.
- Manual rehearsal from PowerShell on the target Windows laptop.

**Acceptance:** The demonstration clearly tells the product story, operates 100% offline, and verifies all core user-facing capabilities.

---

## 7. Cross-cutting test matrix

| Area | Required test cases |
|---|---|
| **CLI & Parsing** | Exactly one source target enforced; secondary data inputs (`--input`, `--expected-*`) parsed; invalid options; `--` arguments; noninteractive `--apply` write authority without prompt; stable exit codes. |
| **Target & Paths** | Paths with spaces, Unicode characters, relative/absolute forms, non-existent files, directories, empty files, oversized files, read-only files, reparse points (symlinks/junctions rejected for write). |
| **File Preservation** | UTF-8, UTF-8 BOM, declared PEP 263 encodings, CRLF line endings, LF line endings, missing trailing newline preservation; byte-level candidate verification. |
| **Static Analysis** | Clean files, syntax errors, indentation errors, multiple Ruff diagnostics, isolated configuration (`--isolated`), defense-in-depth zero cache artifacts (`--no-cache`, zero `.ruff_cache` in project or parent directories), bytecode write suppression. |
| **Execution & Limits** | Controlled command construction (`-E -B -P`), environment variable allowlist, user invocation directory as default `cwd`, `PYTHONSAFEPATH` (`-P`) preventing automatic insertion of ambient script/cwd paths into `sys.path`, `sys.path` environment inspection and runtime modification tests, session copy `__file__` path normalization in error reports, boundary tests for `__file__` and `Path(__file__).parent` package and sibling resource limitations, stdout/stderr pipe draining, timeout enforcement, output byte cap truncation, descendant process termination. |
| **Windows Job Objects** | Job Object assignment, process-tree kill-on-close, handle closure verification, execution from standard PowerShell, VS Code terminal, and nested-job environments on Windows 11 x64, observable `psutil` fallback, `--fail-on-job-failure`. |
| **Inference & Context** | Local Ollama endpoint with native JSON Schema structured output via simple Pydantic schemas (`format: Model.model_json_schema()`) tested against the pinned Ollama engine, formal token budget separation (hard 2,048-token context window, 1,200-token prompt budget, 600-token output cap, 248-token application safety margin satisfying `prompt + output + margin <= context`), post-generation Pydantic validation, Ollama usage metrics capture (`prompt_eval_count`, `eval_count`), conservative token estimation, priority truncation, truncation metadata tracking, schema validation retry, hallucinated evidence ID rejection, model lifecycle / unload. |
| **Patching & Safety** | 1-based inclusive indexing, replacement/insertion/deletion/EOF/empty-file rules, CRLF newline normalization, max 8 edits / 80 lines, temporary candidate application, diff rendering, interactive confirmation, noninteractive `--apply` safety (no bypass of hash or validation; no `--force` exists), compare-before-replace SHA-256 stale-edit detection abort, same-volume candidate staging constraint (separate from `%TEMP%` session metadata), atomic replacement and native backup via Win32 `ReplaceFileW` (`lpBackupFileName`, `dwReplaceFlags = 0`, fail-closed: backup failure aborts replacement; no power-loss durability claimed). |
| **Validation Levels** | Level A (Static validity: AST parses cleanly, zero newly introduced syntax errors or Ruff diagnostics; diagnostic removal verified for static repairs, but diagnostic removal not required for runtime-only repairs where baseline code was already Ruff-clean; shifted line support), Level B (Failure reproduction removed: original exception signature no longer reproduced; explicitly noting that Level B does not prove the bug is fixed), Level C (Clean execution: clean exit 0 under identical limits), Level D (Behavioral oracle: user-supplied expected stdout/exit satisfied; cannot pass without explicit oracle). |
| **Complexity Analysis** | All supported classes in closed vocabulary (`O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`, `O(2^n)`), grounded in CPython runtime semantics, explicit distinction of amortized and expected/average complexities, formal auxiliary vs output space separation, generator vs comprehension, list vs set membership, direct recursion, call-stack space accounting, mandatory abstention on unknown calls, dynamic bounds, or dynamic recursion. |
| **Function Profiling** | Function and class method selectors, direct file-based loading in disposable worker (`importlib.util.spec_from_file_location`), separate measurement of all 5 metrics: import-time cost (duration, stdout, stderr), function timing (warm-up, median, dispersion), peak tracemalloc-tracked Python memory allocations, worker process RSS, and hot-process semantics (single module load, persistent module/global state) versus fresh-process startup benchmarking. |
| **Sanitization & Reporting** | Terminal output sanitization stripping ANSI escape sequences, CSI/OSC control codes, and dangerous control characters; JSON output raw data preservation; console Unicode fallback. |
| **Session Cleanup** | Explicit 5-stage cleanup sequence: 1. stop/terminate processes → 2. close pipes/handles → 3. wait for process termination → 4. close job/process handles → 5. delete session directory and same-volume staging; prevention of Windows sharing violations (`ERROR_SHARING_VIOLATION`); retention with `--keep-session`. |

---

## 8. Release gates

The MVP must not be declared complete until all 20 release gates pass on native **Windows 11 x64**:

1. **Single-target scope enforcement:** Supported platform is Windows 11 x64 only; exactly one Python source target is enforced across every command for inspection, analysis, diagnosis, and patching; secondary data inputs (such as profiling JSON or validation strings) provide execution data without counting as source targets; single-target scope is never conflated with a runtime security sandbox, and runtime imports are permitted.
2. **Runtime import and environment contract:** Runtime execution is strictly defined, documented, and tested using `-E -B -P`, a minimal environment allowlist, user invocation directory as default `cwd`, and `PYTHONSAFEPATH` (`-P`) preventing automatic insertion of ambient script/cwd paths into `sys.path`; `__file__` reflects the relocated temporary session copy while reports normalize it back to the canonical target path; resource and package limitations relative to `__file__` are tested and documented; installed third-party packages remain available, while automatic ambient sibling discovery is prohibited.
3. **Direct file-based profiling loader:** Profiling loads the explicit target directly from its path using `importlib.util.spec_from_file_location` inside a disposable worker, without relying on `import target` or accidental `sys.path` discovery, capturing import metrics and side-effects separately.
4. **Deterministic static isolation:** Static-only commands (`info`, `detect`, `analyse`, `complexity`) never execute or import target code, and emit no bytecode.
5. **Ruff cache isolation:** Ruff executes in single-file isolated mode with `--no-cache`, with defense-in-depth ensuring zero unexpected `.ruff_cache` directories or artifacts are left in the user's target project or relevant parent directories.
6. **Execution limits and descendant cleanup:** Execution limits (timeout, output byte cap) retain partial evidence, and descendant processes are cleaned up following the explicit 5-stage handle and process cleanup sequence.
7. **Job Object environment resilience:** Job Objects provide operational containment and are tested on Windows 11 x64 in standard consoles, VS Code terminals, and nested-job environments; `psutil` fallback is observable, and documentation never describes Job Objects as a security sandbox.
8. **Separated memory accounting and model lifecycle:** Ollama server and model memory residency is measured and reported separately from target execution RSS and Python heap allocations; explicit model lifecycle management prevents memory exhaustion on the 8 GB RAM target budget.
9. **Terminal display sanitization:** Terminal output is actively sanitized against ANSI escape sequences, virtual terminal commands, and control characters, while versioned JSON output preserves underlying raw data.
10. **Hard inference budgets and structured output:** Model context strictly separates context window (hard 2,048 tokens), prompt budget (capped at 1,200 tokens), output budget (capped at 600 tokens), and explicit application safety margin (248 tokens), enforcing `prompt_budget + output_budget + application_safety_margin <= context_window`; structured outputs use simple Ollama JSON Schema objects via Pydantic (`format: Model.model_json_schema()`) tested against the pinned Ollama engine with application-side Pydantic validation; Ollama usage metrics are captured; model output cannot execute commands or write files.
11. **Frozen edit schema:** Edit indexing is frozen (1-based, inclusive, explicit replace/insert/delete/EOF semantics, CRLF normalization, max 8 edits / 80 lines); edits are applied to a temporary candidate without touching the source.
12. **Compare-before-replace SHA-256 stale-edit detection:** SHA-256 verification and reparse-point rejection are enforced immediately before replacement; the guarantee is documented as stale-edit detection against external edits rather than a concurrent filesystem CAS.
13. **Atomic replacement with native backup on same volume:** When backup is requested, atomic replacement and backup creation are executed atomically using Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)` on the target's filesystem volume (distinct from `%TEMP%` session metadata); if backup or replacement cannot complete, replacement is aborted fail-closed without modifying the target; partial writes are prevented; power-loss durability is not claimed.
14. **Noninteractive `--apply` safety invariants:** `--apply` grants write authority without interactive prompts, but cannot bypass any validation, hash checks, or reparse-point protections; no `--force` bypass exists.
15. **Source representation preservation:** Target encoding, UTF-8 BOM, newline style (CRLF vs LF), and trailing newline state pass byte-level preservation fixtures.
16. **Empirical validation levels:** Validation levels A–D are assigned strictly from empirical evidence; Level A (Static validity) requires clean AST parsing and zero newly introduced diagnostics, requiring specific diagnostic removal for static repairs but not requiring Ruff finding removal for runtime-only repairs where the baseline code was already Ruff-clean; Level B requires failure reproduction removal (explicitly documenting that Level B does not prove correctness); Level C requires clean exit 0; Level D requires passing an explicit expected output oracle.
17. **Conservative complexity contract:** Complexity analysis separates time, auxiliary space, and output space using formal definitions based on CPython runtime semantics, explicitly differentiating amortized and expected/average complexities, restricts emission strictly to the closed vocabulary (`O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`, `O(2^n)`), and strictly abstains with clear reasons whenever costs depend on unestablished static semantics or external calls.
18. **Documented hot-process profiling semantics:** Profiling documents and tests hot-process semantics (module-level state persistence across runs with fresh argument reconstruction), separating import-time metrics, invocation times, peak tracemalloc-tracked Python memory allocations, and worker process RSS from external Ollama memory and fresh-process startup costs.
19. **Prominent trust and security disclaimers:** README and security documentation prominently state that `localdev` is for **user-owned or trusted code only**, is **not a security sandbox**, and is a **personal portfolio project**.
20. **Hardware budget validation:** Standard evaluation workflows complete within the measured memory budget on native Windows 11 x64 on the target 8 GB Windows laptop.

---

## 9. Scope-reduction order

If schedule pressure requires reducing the MVP, defer work strictly in this order:

1. Class-method profiling (retain module-level function profiling).
2. Advanced divide-and-conquer complexity analysis (retain iterative complexity analysis and linear recursion).
3. Noninteractive `--expected-stdout-contains` validation (retain exact `--expected-stdout` and `--expected-exit`).
4. Configurable Job Object strict mode (`--fail-on-job-failure`), keeping automatic psutil fallback as default.
5. Automated retry for model diagnosis (abstain immediately on first schema failure).

> **Note on automated schema retry:** Small quantized models (1.5B–3B parameters) frequently fail structured JSON schema generation on their initial attempt. A single schema-correction retry is load-bearing for basic tool reliability in local SLM operation; therefore, automated retry is placed lowest in the reduction order and must only be deferred under extreme schedule pressure.

**Non-negotiable invariants:** Under no circumstances may the following be removed or compromised:
- Strict single-target scope enforcement (exactly one explicit target file; runtime imports permitted; no sibling inspection or multi-file patching).
- Supported platform strictly bounded to Windows 11 x64 only.
- Trusted-code warning, personal-project scope statement, and non-sandbox disclaimer.
- Runtime and Isolation Contract (`-E -B -P`, default user `cwd`, `sys.path` restriction, `__file__` session copy resource limitations).
- Ruff cache defense-in-depth (`--isolated --no-cache`).
- Same-volume candidate staging (distinct from `%TEMP%` session metadata).
- Temporary candidate application before write.
- Structured edit validation and frozen indexing rules.
- Compare-before-replace SHA-256 stale-edit detection.
- Atomic replacement with native backup on target volume (`ReplaceFileW` with `lpBackupFileName`, `dwReplaceFlags = 0`).
- Noninteractive `--apply` safety (no bypass of validation, hash checks, or reparse point protections; no `--force` bypass).
- Terminal output sanitization.
- Separate memory accounting (peak tracemalloc-tracked Python allocations vs worker process RSS).
- Honest validation levels (Level A static validity, Level B failure reproduction removal [noting Level B does not prove bug fixed], Level C clean exit, Level D behavioral oracle) and explicit abstention.
- Strict 5-stage handle and process cleanup sequence.

---

## 10. Principal risks and mitigations

| Risk | Impact | Mitigation and owning tasks |
|---|---|---|
| **OOM on 8 GB RAM laptop** | System freeze or process crash | Separate Ollama memory from execution; enforce separated token budgets (2,048 context window, 1,200 prompt budget, 600 output cap, 248 application safety margin); configure model unload (`keep_alive`); test 1.5B fallback model (P1-T3, P7-T2, P12-T3). |
| **Model hallucinates evidence** | Misleading diagnosis / bad patches | Enforce strict evidence ID validation; reject proposals citing invented evidence; abstain safely (P7-T2, P7-T3). |
| **Malformed or destructive edits** | Source code corruption | Frozen 1-based edit schema; max 8 edits / 80 lines; validate against temporary candidate copy before review (P8-T1, P8-T2). |
| **Concurrent on-disk modification** | Loss of external/IDE edits | Immediate compare-before-replace SHA-256 stale-edit detection before write; same-volume candidate staging and atomic file replacement (P2-T2, P8-T3). While SHA-256 guards against stale external modifications, exact-matching `expected_text` protects against in-session model context drift. |
| **Model context drift / stale AST** | Destructive/misaligned edits | Mandatory exact-matching of `expected_text` against target file lines before candidate generation; any mismatch immediately aborts the edit proposal (P8-T1, P8-T2). |
| **Backup failure leaves file at risk** | Inability to recover original code | Native atomic backup via Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)` on the target's volume (distinct from `%TEMP%` session metadata): kernel-level directory swap ensures target replacement only completes if backup succeeds; fail-closed on any error (P8-T3). |
| **Ruff pollutes user workspace** | Unwanted `.ruff_cache` in repo | Enforce defense-in-depth (`--isolated --no-cache`, isolated session execution, post-run audit) ensuring zero unexpected cache artifacts in project or parent directories (P4-T3). |
| **Terminal control injection** | Terminal display corruption / spoofing | Sanitize all source code, stdout, stderr, and model prose before terminal rendering (strip/escape ANSI/VT sequences); preserve raw data in JSON (P2-T4). |
| **Runaway descendant processes** | CPU/RAM exhaustion on timeout | Two-tier process management: Windows Job Objects with `KILL_ON_JOB_CLOSE` + `psutil` recursive termination fallback on Windows 11 x64 (P5-T3, P6-T1, P6-T2). |
| **Windows file lock during cleanup** | `PermissionError` on session cleanup | Strict 5-stage cleanup sequence: stop/terminate processes → close pipes/handles → wait for process exit → close job/process handles → delete session directory and same-volume staging (P2-T3). |
| **Import side effects during profiling** | Uncontrolled code execution in CLI | Execute profiling in disposable subprocess; load via direct path (`importlib.util.spec_from_file_location`); capture import metrics separately (P11-T1). |
| **Profiling state mutation** | Inconsistent repeated timings | Hot-process semantics explicitly documented; deep reconstruction of fresh arguments from JSON for every invocation (P11-T2, P11-T3). |
| **Unsound complexity claims** | False algorithmic guarantees | `Conservative + assumption-linked + source-linked + abstention-first` contract based on CPython runtime semantics; explicitly differentiate amortized and expected/average complexities; restricted to closed vocabulary (`O(1)` through `O(2^n)`); mandatory abstention on unknown calls or dynamic bounds (P10-T1, P10-T2, P10-T3). |
| **User assumes security sandbox** | Security vulnerability from hostile code | Prominent warnings in CLI, README, and docs: personal portfolio project for user-owned/trusted code only; Job Objects are operational limits, not a security sandbox (P1-T1, P12-T4). |

---

## 11. Completion statement

The project is complete when all 20 release gates pass on native **Windows 11 x64**; the evaluation report provides reproducible offline metrics across the evaluation datasets with pinned toolchain versions; the four-part demonstration succeeds offline from a clean installation; and all documentation transparently limits the product to one user-owned or trusted Python source target per command.

---

## 12. Revision Notes

This revised plan incorporates technical refinements across all phases to ensure the implementation contract is technically precise, honest, and achievable on Windows:

1. **Personal Resume/Portfolio Project Scope & Windows 11 x64 Boundary:** Explicitly framed `localdev` as a personal portfolio project demonstrating high engineering rigor, clean systems integration, and reproducible local SLM orchestration. Bounded the supported platform strictly to Windows 11 x64 only (all other operating systems and legacy Windows versions are explicitly unsupported), while maintaining strict technical quality.
2. **Single-Target Scope & Runtime Import Separation:** Refined Section 3.1 (`Runtime and Isolation Contract`) to explicitly state that `localdev` is intentionally single-target, not necessarily "single-file at runtime." Exactly one Python source target is inspected, analyzed, diagnosed, patched, and validated. Runtime execution behaves like normal Python and may import or read dependencies, but other source files are never inspected or patched. Clarified that `localdev` is an assistant for trusted code, not a security sandbox.
3. **Execution Environment Policy, Path Semantics, and `__file__` Limitations:** Specified interpreter execution using `-E` (strip `PYTHON*` variables), `-B` (suppress bytecode), and Python 3.11+ `-P` (`PYTHONSAFEPATH` to prevent automatic insertion of ambient script/cwd paths into `sys.path`). Clarified that `-P` does not strip standard library paths, site-packages, virtualenv paths, `.pth` files, or runtime modifications, functioning as an ambient path guard rather than a hermetic sandbox. Configured `cwd` to default to the user's invocation directory rather than the session directory. Documented that `__file__` points to the relocated session copy (`%TEMP%\localdev\session_<id>\session_target.py`); consequently, package identity and resource access relative to `__file__` do not behave identically to execution of the original file, as the package tree is not reconstructed and sibling resources are not copied. Added comprehensive boundary tests in P5-T1 covering `os.getcwd()`, `__file__`, `cwd`-relative access, `Path(__file__).parent`, `sys.path`, sibling imports, and resource dependencies beside the original file.
4. **Profiling Loading & State Semantics:** Replaced `import target` with direct file loading via `importlib.util.spec_from_file_location` in a disposable worker. Formalized hot-process profiling semantics with fresh JSON argument reconstruction and explicit reporting of module-level state persistence, distinctly separating all 5 profiling metrics (import duration/stdout/stderr, function timing, peak tracemalloc-tracked Python memory allocations, worker process RSS, and hot-process vs fresh-process startup benchmarking).
5. **Stale-Edit Detection & Patch Guarantees:** Consistently described the SHA-256 compare-before-replace check as stale-edit detection against external file modifications rather than a concurrent filesystem CAS, acknowledging the microsecond window between hashing and replacement, while exact-matching `expected_text` protects against in-session model context drift.
6. **Same-Volume Replacement Staging & Native ReplaceFileW Backup:** Distinguished general session workspace/metadata (which resides under `%TEMP%\localdev\session_<id>`) from replacement staging; required the replacement candidate and backup file (`<target>.bak`) to be staged on the exact filesystem volume of the target. Specified Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)` without the unsupported `REPLACEFILE_WRITE_THROUGH` flag; defined `--apply` as explicit noninteractive write authority that cannot bypass validation or hash checks; prohibited any `--force` bypass; noted that power-loss durability is outside the MVP.
7. **Frozen Edit Schema:** Standardized 1-based inclusive line indexing, explicit replace/insert/delete/EOF semantics, empty-file handling, and CRLF normalization.
8. **Ruff Cache Defense-in-Depth:** Enforced the invariant that `localdev` leaves zero unexpected Ruff cache artifacts in the user's target project or relevant parent directories via `--isolated`, `--no-cache`, session execution, and filesystem audit assertions.
9. **Memory Separation & Lifecycle:** Distinctly separated target process-tree RSS, Job Object limits, peak tracemalloc-tracked Python memory allocations, external Ollama service residency, and total system RAM. Configured model lifecycle unload (`keep_alive`) to fit the 8 GB budget.
10. **Separated Inference Token Budgets & Simple Structured Output:** Divided the hard 2,048-token context window (`num_ctx: 2048`) into an application prompt budget (1,200 tokens), an output budget (600 tokens, `num_predict: 600`), and an explicit 248-token application safety margin for framing/schema/tokenization uncertainty, formally enforcing `prompt_budget + output_budget + application_safety_margin <= context_window`. Configured Ollama structured output using simple Pydantic JSON Schema objects (`format: Model.model_json_schema()`) tested directly against the pinned Ollama engine, backed by post-generation Pydantic validation and Ollama usage metrics tracking (`prompt_eval_count`, `eval_count`). Required tokenizer validation against the actual model vocabulary in P1-T3 and P7-T2, using the 3.5 chars/token heuristic strictly as a fallback.
11. **Pydantic Structural Shape Validation:** In P7-T3, explicitly required testing and trapping structurally valid JSON with the wrong shape (incorrect types, unexpected keys), safely routing to retry and abstention.
12. **Terminal Output Sanitization:** Added ANSI/VT escape sequence sanitization for terminal presentation, while preserving raw underlying bytes in JSON output.
13. **CPython Complexity Cost Model & Space Semantics:** Formalized the `conservative + assumption-linked + source-linked + abstention-first` complexity contract grounded in CPython runtime semantics, explicitly differentiating amortized costs (e.g., `list.append()`) and expected/average costs (e.g., dict lookup) from worst-case bounds, formalized auxiliary space versus output space definitions, and established a closed output vocabulary explicitly including `O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`, and `O(2^n)`, with unclassifiable patterns routing to `UNKNOWN`.
14. **Windows Process Robustness & Standardized 5-Stage Cleanup Sequence:** Added Windows Job Object testing for PowerShell, VS Code terminal, and nested-job environments on Windows 11 x64; established `psutil` fallback as the expected default in nested environments; standardized the cleanup sequence across all documentation and tests to five conceptual stages: 1. terminate target process tree → 2. close pipes and handles → 3. wait for process exit → 4. close Job Object/process handles → 5. delete session directory and same-volume staging.
15. **Scope Reduction Reordering:** Moved automated diagnosis retry lower in Section 9 deferral order, noting that first-attempt retries are load-bearing for small quantized SLM reliability.
16. **Demo Scope Refinement:** Restructured the final demonstration into a coherent four-part story (primary debug-and-fix workflow, static complexity, function profiling, and honest abstention).
17. **Validation Levels & Explicit Level B Interpretation:** Formally defined Level A as static validity (syntax parses cleanly, zero new diagnostics, targeted static finding eliminated if applicable; runtime-only repairs pass without Ruff finding removal). Explicitly documented that Level B (failure reproduction removed) does NOT prove the bug is fixed, as replacing an exception with another error passes Level B but fails Level C (clean execution exit 0). Level D requires an explicit user behavioral oracle.
18. **Toolchain Version Pinning:** Established an explicit reproducibility requirement in Phase 1, Phase 12, and the evaluation harness to record exact pinned versions for Python, Pydantic, Ruff, psutil, Ollama, and selected SLM model files.

---

## 13. Release-gate and task consistency pass

To guarantee architectural consistency across all sections, the final release gates have been systematically cross-checked and verified against the detailed phase tasks:

| Invariant / Requirement | Detailed Task Reference | Release Gate & Matrix Alignment | Verification Status |
|---|---|---|---|
| **Personal Project Scope & Platform Boundary** | P1-T1, Section 1, Section 3 | Gate 1, Section 7 | **Consistent:** Personal resume/portfolio project; supported platform is Windows 11 x64 only; no enterprise infrastructure or legacy Windows compatibility work. |
| **Single-Target Scope** | P1-T1, P2-T2, Section 3.1 | Gate 1, Section 7 (CLI & Parsing) | **Consistent:** Exactly one Python target is analyzed, diagnosed, or patched; runtime execution is permitted to import dependencies; no sibling discovery; no multi-file mutations. |
| **Execution Semantics (`cwd`, `__file__`, `sys.path`)** | P5-T1, Section 3.1 | Gate 2, Section 7 (Execution & Limits) | **Consistent:** `cwd` defaults to invocation directory; `__file__` points to relocated session copy (with tested package tree and sibling resource limitations); `sys.path` has no ambient script/cwd prepending due to `-P`; remaining entries come from Python environment. |
| **Token Budgets & Application Safety Margin** | P1-T3, P7-T1, P7-T2, P12-T3 | Gate 10, Section 7 (Inference & Context) | **Consistent:** Context window (2,048), prompt budget (1,200), output budget (600), and application safety margin (248) are enforced such that `prompt + output + margin <= context_window`; 248 tokens documented as application safety margin, not a platform-reserved partition. |
| **Ollama JSON Schema Structured Output** | P1-T2, P7-T1, P7-T3 | Gate 10, Section 7 (Inference & Context) | **Consistent:** Pass simple Pydantic JSON Schema (`model_json_schema()`) in `format`, tested against the pinned Ollama version; post-generation Pydantic validation; usage metrics (`prompt_eval_count`, `eval_count`) captured; no model output treated as executable. |
| **Validation Levels A–D & Level B Semantics** | P9-T1, P9-T2, P9-T3, Principle 7 | Gate 16, Section 7 (Validation Levels) | **Consistent:** Level A (Static validity: clean parse, zero new diagnostics, targeted finding removed if static; runtime-only repairs pass without Ruff finding removal); Level B (failure reproduction removed, explicitly noting it does not prove correctness); Level C (clean exit 0); Level D (behavioral oracle passed). |
| **Complexity Vocabulary & CPython Semantics** | P1-T2, P10-T1, P10-T2, P10-T3, P10-T4 | Gate 17, Section 7 (Complexity Analysis) | **Consistent:** Grounded in CPython runtime semantics; explicit distinction of amortized and expected/average complexities; closed enum explicitly includes `O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`, `O(2^n)`, and `UNKNOWN`; arbitrary classes prohibited; abstention-first. |
| **Same-Volume Candidate Staging & ReplaceFileW** | P2-T3, P8-T3, Section 3.1, Principle 5 | Gate 13, Section 7 (Patching & Safety) | **Consistent:** Win32 `ReplaceFileW(..., 0, ...)` with `lpBackupFileName` without unsupported flags; replacement candidate and backup staged on target's volume (distinct from `%TEMP%` session metadata); atomic replacement does not claim power-loss durability; compare-before-replace SHA-256 stale-edit detection enforced. |
| **Noninteractive `--apply` Invariants** | P8-T3, P8-T4, Principle 5 | Gate 14, Section 7 (Patching & Safety) | **Consistent:** `--apply` authorizes write without prompts; cannot bypass validation, hash checks, or reparse point checks; no `--force` bypass exists. |
| **Profiling Semantics & Memory Metrics** | P11-T1, P11-T2, P11-T3, P11-T4 | Gate 18, Section 7 (Function Profiling) | **Consistent:** Distinctly separates all 5 metrics: import-time cost, function timing, peak tracemalloc-tracked Python memory allocations, worker process RSS, and hot-process profiling (module imported once, persistent state) versus fresh-process startup benchmarking. |
| **Standardized 5-Stage Cleanup Sequence** | P2-T3, Section 5, Section 7, Section 10 | Gate 6, Section 7 (Session Cleanup) | **Consistent:** Standardized across all tasks and documentation to 5 stages: stop/terminate processes → close pipes/handles → wait for process termination → close job/process handles → delete session directory and staging locations. |
