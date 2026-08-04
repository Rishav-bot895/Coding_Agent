# Implementation Plan: Windows Single-File Offline Python Coding Agent

## 1. Purpose

This document converts `Final_Windows_Project_Plan.md` into an implementation-ready delivery plan. It defines phases, atomic tasks, affected files, inclusions, exclusions, test obligations, and completion criteria for each task.

The product is a native Windows terminal application named `localdev`. It accepts exactly one user-supplied Python source file per command, combines deterministic analysis with a locally installed small language model (SLM), and keeps the user in control of source-code changes.

The product claim is deliberately narrow: it is an offline-after-installation assistant for user-owned or trusted Python code. Its process limits reduce accidental damage but do not constitute a security sandbox.

## 2. Delivery principles

1. Deterministic tools are the source of facts; the SLM explains evidence and proposes bounded edits.
2. Every command operates on exactly one explicitly supplied source file.
3. No project files, imported local modules, tests, Git history, or tool configuration are discovered automatically.
4. Model output never directly executes commands or writes source files.
5. Every edit is schema-validated, applied to a temporary copy, differentially checked, displayed, confirmed, and hash-checked before replacement.
6. Execution is limited and monitored, but is offered only for trusted code.
7. Reports distinguish structural validity, failure removal, successful execution, and behavioural verification.
8. Complexity reports distinguish time, auxiliary space, and output space.
9. Profiling reports Python allocations separately from approximate process-tree RSS.
10. Unsupported or ambiguous cases produce an explicit limitation or abstention.

## 3. Project-wide scope

### In scope

- Windows 10 and Windows 11.
- One Python source file per command.
- Python version selected and documented during Phase 1.
- Commands: `info`, `detect`, `analyse`, `debug`, `fix`, `complexity`, and `profile`.
- Terminal and versioned JSON output.
- Extension, shebang, and parser-based Python detection.
- Syntax checking, AST facts, isolated Ruff diagnostics, controlled execution, and traceback parsing.
- Local SLM diagnosis and structured edit proposals through one inference backend.
- User-confirmed single-file patch application.
- Static complexity estimation and function-level profiling.
- Offline use after packages, Ruff, model, and inference runtime are installed.

### Not in scope

- Repository-wide or multi-file analysis and repair.
- Automatic inspection of imported local modules, tests, configuration, or Git history.
- Dependency installation or resolution.
- Framework-specific debugging or project-wide test execution.
- Secure execution of hostile code, filesystem isolation, or network isolation.
- Automatic patch application without confirmation.
- Multiple inference backends in the MVP.
- Concurrent runner or profiling jobs.
- Guaranteed logical-bug detection, patch correctness, or complexity inference.
- WSL2, AppContainer, Windows Sandbox, container, or virtual-machine execution.

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

A task is done only when its implementation, tests, type hints, error paths, and relevant documentation are complete; its tests pass on the selected Windows/Python matrix; and it does not weaken the single-file, trust, or write-confirmation boundaries.

### Testing conventions

- Unit tests use `pytest` and avoid model or network dependencies.
- Integration tests use controlled fixture files and deterministic fake inference responses where possible.
- Windows-specific process tests are marked `windows` and run on native Windows CI or the target laptop.
- Tests must not write into fixture source directories unless the task explicitly tests writes.
- Temporary artifacts use pytest temporary directories and are checked for cleanup.
- JSON fixtures are validated against the current schema version.
- Model-quality evaluation is separate from deterministic correctness tests.

### Planned quality commands

```powershell
python -m pytest
python -m pytest -m windows
ruff check --isolated .
python -m mypy localdev
```

Exact supported versions and CI commands are frozen in Task P1-T1.

## 6. Phase summary and dependency order

| Phase | Outcome | Depends on |
|---|---|---|
| 1 | Frozen specification, environment, schemas, and model decision | None |
| 2 | CLI foundation, file boundary, sessions, and reporters | Phase 1 |
| 3 | Language detection and adapter architecture | Phase 2 |
| 4 | Python syntax, AST, and isolated Ruff evidence | Phase 3 |
| 5 | Basic Windows execution and traceback evidence | Phase 2, Phase 4 |
| 6 | Job Objects and resource reporting | Phase 5 |
| 7 | Evidence-grounded SLM diagnosis | Phase 4, Phase 5 |
| 8 | Structured patches and guarded application | Phase 7 |
| 9 | Differential patch validation | Phase 8 |
| 10 | Static complexity analysis | Phase 4 |
| 11 | Function profiling | Phase 5, Phase 10 selectors |
| 12 | Evaluation, optimisation, documentation, and release demo | All prior phases |

---

# Phase 1 — Specification, environment, schemas, and model benchmark

## P1-T1 — Freeze the MVP contract and toolchain

**Definition:** Record the exact product claim, supported Windows/Python versions, dependency versions, CLI surface, trust model, configuration defaults, and non-goals. Establish a reproducible Python package with development dependencies and test markers.

**Files:** `pyproject.toml`, `README.md`, `LICENSE`, `docs/security.md`, `localdev/__init__.py`, `localdev/constants.py`, `tests/conftest.py`.

**In scope:** Package metadata; console entry point; supported-version statement; Ruff and pytest setup; Windows test marker; trusted-code warning text; default limits; offline-after-installation wording.

**Not in scope:** Implementing commands; installing a model for the user; CI for non-Windows platforms; claims of secure sandboxing.

**Testing:** Build a wheel and install it into a clean virtual environment; assert `localdev --help` resolves; run an import smoke test; verify test marker registration; add a documentation test that the trusted-code warning and single-file limitation appear in `README.md` and `docs/security.md`.

**Acceptance:** A clean checkout can be installed with documented commands, and all version and trust decisions are unambiguous.

## P1-T2 — Define versioned internal and external schemas

**Definition:** Implement typed representations and validation for diagnostics, runtime results, traceback frames, AST facts, diagnoses, edit proposals, validation reports, complexity reports, profile reports, and the top-level JSON envelope.

**Files:** `localdev/schemas.py`, `localdev/errors.py`, `tests/unit/test_schemas.py`, `tests/fixtures/schema/`.

**In scope:** Schema version `1.0`; required/optional fields; enums for statuses, confidence, and validation levels; path serialization policy; rejection of unknown or malformed model-controlled fields.

**Not in scope:** Schema migration tooling; database persistence; model prompting; terminal formatting.

**Testing:** Round-trip every schema through JSON; reject missing required fields, invalid enums, invalid line ranges, negative durations, and unknown schema versions; snapshot one valid example of each report; assert model edit structures cannot contain commands or additional target files.

**Acceptance:** Every subsystem can exchange validated typed data without relying on unstructured dictionaries.

## P1-T3 — Benchmark and select the local inference configuration

**Definition:** Compare one 1.5B-class and one 3B-class quantized model through Ollama on the target laptop, then select the MVP model and fallback using measured quality, RAM, load time, and latency.

**Files:** `docs/evaluation.md`, `tests/bug_samples/initial/`, `tests/fixtures/model_responses/`, `localdev/config.py`.

**In scope:** Offline inference verification; 2,048-token context target; 600-token output cap; cold/warm latency; peak RAM; a small representative diagnosis set; recorded model/runtime versions.

**Not in scope:** Fine-tuning; exhaustive model search; cloud APIs; simultaneous model loading; second production inference backend.

**Testing:** Run both candidates on the same prompts at least three times; measure peak system/process RAM and response validity; disconnect network after model installation and repeat a smoke prompt; record pass/fail against the target laptop’s memory budget.

**Acceptance:** One default and one fallback are documented with measurements and a reasoned selection; no unmeasured hardware-compatibility claim remains.

---

# Phase 2 — CLI, target validation, sessions, and reporting

## P2-T1 — Build command parsing and stable exit semantics

**Definition:** Implement the CLI skeleton for `info`, `detect`, `analyse`, `debug`, `fix`, `complexity`, and `profile`, including common output options and stable exit codes.

**Files:** `localdev/cli.py`, `localdev/reporting/exit_codes.py`, `localdev/errors.py`, `tests/unit/test_cli_parsing.py`, `tests/integration/test_cli_help.py`.

**In scope:** Exactly one positional target; `--json`; `--stdin-file`; argument separator `--`; selector syntax; actionable usage errors; no stack traces for expected user errors.

**Not in scope:** Performing analysis, execution, patching, or profiling; shell command strings; multiple source targets.

**Testing:** Parameterize valid and invalid command lines; test missing target, two targets, unknown command, malformed selector, and arguments after `--`; snapshot help text; assert each expected failure maps to its documented exit code.

**Acceptance:** All command forms parse into typed requests and reject ambiguous target selection before any file is read or process started.

## P2-T2 — Enforce the single-file target and preserve file metadata facts

**Definition:** Resolve and validate the explicit target while collecting its absolute path, size, SHA-256, encoding, BOM state, newline style, final-newline state, attributes, and reparse-point status.

**Files:** `localdev/agent/permissions.py`, `localdev/agent/session.py`, `localdev/constants.py`, `tests/unit/test_target_validation.py`, `tests/boundary_samples/`.

**In scope:** Regular-file checks; maximum source size; readable text; paths with spaces and Unicode; encoding detection consistent with Python source declarations; read-only and symlink/reparse reporting.

**Not in scope:** Directory traversal; glob expansion; following a symlink for later writes; modifying permissions; reading sibling files.

**Testing:** Cover nonexistent paths, directories, empty files, oversized files, Unicode names, space-containing names, UTF-8, UTF-8 BOM, declared supported encodings, CRLF/LF, no final newline, read-only files, and symlinks/reparse points. Assert exactly one target is opened and hashes match known fixtures.

**Acceptance:** Every later subsystem receives a validated immutable target record, and unsafe write targets are identified early.

## P2-T3 — Manage isolated session directories and cleanup

**Definition:** Create a unique `localdev`-managed temporary workspace per command, store only session artifacts there, and clean it on success, expected failure, interruption, and timeout unless diagnostic retention is explicitly enabled.

**Files:** `localdev/agent/session.py`, `localdev/config.py`, `tests/unit/test_session.py`, `tests/integration/test_session_cleanup.py`.

**In scope:** Session ID; manifest; temporary source copy; bounded artifact names; cleanup lifecycle; optional debug-retention flag; ownership marker preventing accidental cleanup of arbitrary directories.

**Not in scope:** Long-term history; cloud synchronization; cleanup outside directories created and marked by `localdev`.

**Testing:** Assert unique sessions; simulate exceptions and Ctrl+C cleanup; verify retained sessions only when configured; insert unrelated neighboring files and prove they remain untouched; ensure a missing ownership marker prevents automated deletion.

**Acceptance:** No normal command leaves temporary artifacts, and cleanup cannot target a directory not created and marked by the application.

## P2-T4 — Implement terminal and JSON reporters

**Definition:** Render consistent human-readable results and a machine-readable versioned JSON envelope for all command outcomes.

**Files:** `localdev/reporting/terminal.py`, `localdev/reporting/json_reporter.py`, `localdev/schemas.py`, `tests/unit/test_terminal_reporter.py`, `tests/unit/test_json_reporter.py`.

**In scope:** Status, evidence, limitations, warnings, source locations, units, truncation flags, validation level, and stable JSON field names; Windows console Unicode fallback.

**Not in scope:** HTML reports; ANSI-only semantics; logs containing entire prompts or unrelated environment values.

**Testing:** Snapshot success, error, timeout, abstention, and truncated-output reports; parse JSON output and validate schema; test redirection to a file; test a console encoding that cannot represent a symbol and confirm graceful fallback.

**Acceptance:** Terminal output is concise and JSON output is deterministic, valid, and sufficient for automation.

---

# Phase 3 — Language detection and adapter architecture

## P3-T1 — Define the language adapter contract

**Definition:** Create an abstract adapter interface for detection confidence, syntax checking, diagnostics, execution, complexity, and patch validation, plus a registry used by the orchestrator.

**Files:** `localdev/languages/base.py`, `localdev/languages/python/adapter.py`, `localdev/agent/orchestrator.py`, `tests/unit/test_adapter_contract.py`.

**In scope:** Typed method contracts; capability reporting; one Python adapter skeleton; dependency injection for test doubles; orchestration that does not import Python internals directly.

**Not in scope:** A second language; dynamic plugin loading; actual analyser implementations.

**Testing:** Use a fake adapter to exercise each orchestrator path; verify incomplete adapter implementations cannot instantiate; assert orchestrator selection depends only on the registry and contract.

**Acceptance:** Python-specific behaviour is behind the adapter boundary, and a future language could be added without rewriting command orchestration.

## P3-T2 — Implement layered language detection

**Definition:** Combine extension, shebang, and parser-based signals into a confidence-scored result, with Python as the only supported MVP language.

**Files:** `localdev/languages/detector.py`, `localdev/languages/python/adapter.py`, `tests/unit/test_language_detector.py`, `tests/boundary_samples/languages/`.

**In scope:** `.py` and `.pyw`; standard Python shebang forms; parse confirmation; reason codes; conflict handling; non-executing detection.

**Not in scope:** Guessing from arbitrary prose; executing a file to detect its language; treating unsupported languages as Python solely because parsing happens to succeed.

**Testing:** Cover valid Python, syntax-invalid `.py`, extensionless Python with shebang, misleading extensions, binary input, unsupported JavaScript/text, and conflicting signals; assert detection never starts a subprocess.

**Acceptance:** `localdev detect` reliably identifies supported Python inputs and reports unsupported or uncertain inputs without execution.

## P3-T3 — Wire `info` and `detect` end to end

**Definition:** Connect parsing, target validation, sessions, detection, and both reporters for the first usable commands.

**Files:** `localdev/cli.py`, `localdev/agent/orchestrator.py`, `localdev/reporting/`, `tests/integration/test_info_command.py`, `tests/integration/test_detect_command.py`.

**In scope:** File facts safe to report; detection confidence and reasons; schema version; correct exit status; cleanup.

**Not in scope:** AST, Ruff, inference, or execution.

**Testing:** Invoke installed console commands against valid, invalid, Unicode-path, and unsupported-language fixtures; compare terminal snapshots and JSON schema; verify target contents and directory remain unchanged.

**Acceptance:** The two commands work from PowerShell on the selected Windows version and form a stable vertical slice.

---

# Phase 4 — Python syntax, AST, and isolated Ruff evidence

## P4-T1 — Decode and syntax-check Python without source-side effects

**Definition:** Decode using Python source-encoding rules and validate with `compile()`/AST parsing without importing, executing, or generating bytecode.

**Files:** `localdev/languages/python/syntax.py`, `localdev/languages/python/adapter.py`, `tests/unit/test_python_syntax.py`, `tests/bug_samples/syntax/`.

**In scope:** Syntax and indentation errors; filename and line/column ranges; source excerpts; supported encodings; disabling bytecode writes.

**Not in scope:** Semantic correctness; imports; executing annotations or top-level code; auto-formatting.

**Testing:** Cover valid files, malformed syntax, indentation errors, encoding declarations, BOM, CRLF, no final newline, and code that would create a marker if imported; assert no marker or `__pycache__` appears.

**Acceptance:** Syntax evidence is precise, deterministic, and side-effect free.

## P4-T2 — Extract bounded AST facts and source ranges

**Definition:** Discover module functions, async functions, classes, methods, nesting, parameters, calls, loops, branches, returns, assignments, and exact source spans needed by later context, complexity, and selector logic.

**Files:** `localdev/languages/python/ast_analyser.py`, `localdev/languages/python/selectors.py`, `tests/unit/test_ast_analyser.py`, `tests/complexity_samples/ast/`.

**In scope:** Stable fact schema; qualified names; line ranges; target function lookup; capped source excerpts; syntax-invalid short circuit.

**Not in scope:** Cross-file name resolution; type inference; executing decorators; full call graphs; nested-function profiling support.

**Testing:** Use functions, async functions, nested definitions, classes, decorators, multiline signatures, comprehensions, and duplicate method names; compare facts to expected fixtures and verify every reported line lies inside the target.

**Acceptance:** Later stages can select relevant file-local code without reparsing ad hoc or sending the entire file unnecessarily.

## P4-T3 — Run Ruff in isolated single-file mode and normalize diagnostics

**Definition:** Invoke Ruff with argument lists, `shell=False`, isolated configuration, JSON output, and one explicit target; normalize its output into internal diagnostics.

**Files:** `localdev/languages/python/diagnostics.py`, `localdev/languages/python/adapter.py`, `tests/unit/test_ruff_parser.py`, `tests/integration/test_ruff_isolation.py`.

**In scope:** Ruff executable discovery; pinned/tested version; timeout; malformed-output handling; locations and rule codes; baseline capture; no project configuration.

**Not in scope:** Ruff auto-fix; scanning directories; reading `pyproject.toml`; treating all warnings as fatal.

**Testing:** Place a conflicting `pyproject.toml` next to a fixture and prove it is ignored; cover clean files, multiple findings, Unicode paths, missing Ruff, timeout, and malformed JSON via a fake process; assert the command contains exactly one target and isolation flags.

**Acceptance:** Identical source produces equivalent normalized diagnostics regardless of surrounding project configuration.

## P4-T4 — Deliver the `analyse` workflow

**Definition:** Combine syntax, AST facts, and isolated Ruff results into one evidence report with deterministic short-circuit and error handling.

**Files:** `localdev/agent/orchestrator.py`, `localdev/agent/evidence.py`, `localdev/cli.py`, `tests/integration/test_analyse_command.py`.

**In scope:** Syntax-first ordering; AST summary; normalized diagnostics; terminal/JSON output; no model required.

**Not in scope:** Runtime execution; diagnosis prose from the SLM; patch proposals.

**Testing:** End-to-end test valid-clean, valid-with-findings, syntax-invalid, oversized, and unsupported files; assert syntax-invalid files do not invoke Ruff/AST stages that require valid syntax; verify offline operation.

**Acceptance:** `localdev analyse file.py` produces useful deterministic evidence and never modifies or executes the target.

---

# Phase 5 — Basic Windows execution and traceback parsing

## P5-T1 — Build controlled execution requests and environments

**Definition:** Construct a Python subprocess request using an argument list, `shell=False`, a session copy, controlled working directory, explicit stdin/arguments, and a minimal documented environment.

**Files:** `localdev/execution/runner.py`, `localdev/execution/environment.py`, `localdev/execution/limits.py`, `tests/unit/test_execution_request.py`.

**In scope:** Selected Python executable; `PYTHONDONTWRITEBYTECODE`; target-copy execution; argument fidelity; stdin text/file; environment allowlist/overrides; trusted-code acknowledgement.

**Not in scope:** Shell pipelines; arbitrary validation commands; filesystem or network isolation; dependency installation.

**Testing:** Inspect generated commands without running them; cover quotes, spaces, Unicode, empty args, `--` args, stdin, and hostile shell metacharacters; prove metacharacters remain literal arguments and `shell` is false.

**Acceptance:** Execution input is explicit, reproducible, and cannot turn CLI arguments into an unintended shell command.

## P5-T2 — Capture output with timeout and byte limits

**Definition:** Execute while incrementally draining stdout/stderr, enforcing wall-clock timeout and a combined/configured output cap, retaining partial output and status facts.

**Files:** `localdev/execution/runner.py`, `localdev/execution/output_capture.py`, `localdev/execution/limits.py`, `tests/windows/test_runner_limits.py`, `tests/bug_samples/runtime/`.

**In scope:** Concurrent pipe draining; duration; exit code; timeout flag; truncation flag; bounded memory; stdin closure; cleanup trigger.

**Not in scope:** Perfect CPU accounting; terminal emulation; interactive programs; unlimited capture.

**Testing:** Run normal output, nonzero exit, infinite loop, large stdout, large stderr, mixed output, blocking stdin, and partial-output-before-timeout fixtures; assert the parent never deadlocks and stored bytes stay within configured tolerance.

**Acceptance:** Controlled examples terminate predictably, partial evidence is preserved, and output cannot grow without bound.

## P5-T3 — Terminate the process tree with a psutil fallback

**Definition:** Discover descendants and terminate the ordinary process tree after timeout, output-limit breach, cancellation, or parent failure, then report cleanup status.

**Files:** `localdev/execution/process_tree.py`, `localdev/execution/runner.py`, `tests/windows/test_process_tree.py`, `tests/boundary_samples/processes/`.

**In scope:** Child/grandchild discovery; graceful-then-forced termination policy; bounded wait; already-exited races; cleanup reporting.

**Not in scope:** Claiming escape-proof containment; killing unrelated processes; Job Objects (Phase 6).

**Testing:** Spawn child and grandchild marker processes; trigger timeout/cancellation; verify recorded PIDs exit and unrelated sentinel processes survive; repeat to expose races; assert failures are reported rather than silently ignored.

**Acceptance:** Ordinary descendant fixtures do not survive failed or cancelled runs, without affecting unrelated processes.

## P5-T4 — Parse tracebacks and deliver `debug` execution evidence

**Definition:** Parse Python tracebacks into target and external frames, construct an original-error signature, and expose deterministic `debug` output before SLM integration.

**Files:** `localdev/languages/python/traceback_parser.py`, `localdev/agent/evidence.py`, `localdev/agent/orchestrator.py`, `tests/unit/test_traceback_parser.py`, `tests/integration/test_debug_command.py`.

**In scope:** Chained exceptions; syntax tracebacks; target-frame matching by resolved/session-copy identity; external-frame labeling; stdout/stderr/exit metadata.

**Not in scope:** Inspecting external frame source; diagnosing native crashes; claiming root cause from parsing alone.

**Testing:** Cover single, chained, nested, external-library, Windows-path, Unicode-path, and malformed tracebacks; end-to-end run success, exception, and timeout fixtures; assert external sources are never opened.

**Acceptance:** `localdev debug` returns bounded runtime evidence and clearly identifies what can and cannot be analysed within the target file.

---

# Phase 6 — Windows Job Objects and memory reporting

## P6-T1 — Add a Windows Job Object lifecycle wrapper

**Definition:** Wrap the required Windows APIs with explicit ownership, handle cleanup, process assignment, full-job termination, and clear error translation.

**Files:** `localdev/execution/windows_job.py`, `localdev/errors.py`, `tests/windows/test_windows_job.py`.

**In scope:** `ctypes` or one selected Windows binding; kill-on-job-close; assignment; active-process limit where supported; deterministic handle closure.

**Not in scope:** AppContainer tokens; ACL changes; filesystem/network isolation; non-Windows implementation.

**Testing:** Assign a controlled process; close/terminate the job and verify descendants exit; exercise invalid handles and assignment failure through wrappers/fakes; run leak checks over repeated jobs.

**Acceptance:** Job lifetime is reliable and failures are explicit, with no inflated sandbox claim.

## P6-T2 — Integrate Job Objects with safe fallback policy

**Definition:** Prefer Job Objects for eligible runs, fall back to psutil only under documented conditions, and report which control backend was active.

**Files:** `localdev/execution/runner.py`, `localdev/execution/windows_job.py`, `localdev/config.py`, `tests/windows/test_job_runner_integration.py`.

**In scope:** Backend selection; setup failure handling; full-job timeout cleanup; optional active-process/job-memory limits; fail-closed configuration option.

**Not in scope:** Silently continuing with weaker controls when configuration requires Job Objects; security equivalence between backends.

**Testing:** Force successful setup, unavailable API, assignment failure, timeout, child/grandchild creation, and active-process breach; verify selected backend and limitation text in reports.

**Acceptance:** Descendant cleanup improves under Job Objects, while fallback behaviour remains observable and configurable.

## P6-T3 — Measure and label process-tree memory

**Definition:** Sample main-process and descendant RSS at a configured interval, calculate an approximate peak, and identify the measurement backend and limitations.

**Files:** `localdev/execution/process_tree.py`, `localdev/execution/runner.py`, `localdev/schemas.py`, `tests/windows/test_process_memory.py`.

**In scope:** 20 ms default sample interval; process churn; access-denied handling; peak aggregate RSS; units; missing-sample status.

**Not in scope:** Exact instantaneous peak guarantees; GPU/model RAM attribution; equating RSS with Python allocations.

**Testing:** Run known allocation patterns in parent and child processes; verify peak increases by a reasonable tolerance, never goes negative, and remains labeled approximate; simulate access denial and process-exit races.

**Acceptance:** Runtime reports provide useful approximate memory evidence without overstating precision.

---

# Phase 7 — Evidence-grounded local SLM diagnosis

## P7-T1 — Implement the inference abstraction and Ollama client

**Definition:** Provide one production inference client with model availability checks, bounded request settings, timeout/cancellation, structured response retrieval, and dependency injection.

**Files:** `localdev/inference/base.py`, `localdev/inference/ollama_client.py`, `localdev/config.py`, `tests/unit/test_ollama_client.py`, `tests/integration/test_ollama_smoke.py`.

**In scope:** Local endpoint only; selected model; context/output caps; one retry category for transport/transient failure; clear offline/missing-model errors.

**Not in scope:** Downloading models automatically; cloud fallback; arbitrary endpoint credentials; parallel generations.

**Testing:** Fake success, timeout, connection failure, malformed response, cancellation, and missing model; optional marked smoke test against installed Ollama; assert no request exceeds configured token/output bounds.

**Acceptance:** Inference failures degrade to actionable reports and never bypass deterministic workflows.

## P7-T2 — Build compact, boundary-safe model context

**Definition:** Select the relevant error, target frames, relevant function/method, nearby lines, Ruff findings, and limited AST/runtime facts without reading unrelated files or exceeding configured budgets.

**Files:** `localdev/agent/context_builder.py`, `localdev/agent/evidence.py`, `localdev/inference/prompts.py`, `tests/unit/test_context_builder.py`.

**In scope:** Prioritized truncation; line-numbered excerpts; explicit evidence IDs; untrusted-code delimiters; omission/truncation metadata; function-first large-file handling.

**Not in scope:** Full repository context; imported source; hidden prompt expansion; secrets discovery; blindly sending a full large file.

**Testing:** Cover small/large files, traceback-local selection, no target frame, many diagnostics, prompt-injection-like comments, and Unicode; assert every excerpt originates in the explicit target/evidence and budgets are enforced deterministically.

**Acceptance:** The model sees enough evidence to reason while the analysis and token boundaries remain measurable and testable.

## P7-T3 — Validate diagnosis responses and evidence grounding

**Definition:** Parse model JSON into the diagnosis schema, verify evidence references against supplied facts, constrain confidence/limitations, retry once for invalid structure, and otherwise abstain safely.

**Files:** `localdev/inference/response_validator.py`, `localdev/schemas.py`, `localdev/inference/prompts.py`, `tests/unit/test_diagnosis_validator.py`.

**In scope:** Schema validation; evidence-ID matching; issue types; confidence; limitations; structured-output retry; hallucinated-evidence rejection.

**Not in scope:** Proving semantic truth of free-form prose; executing model-suggested commands; accepting paths or facts absent from context.

**Testing:** Feed valid, malformed, extra-field, invented-evidence, overlong, conflicting, and abstention responses; assert invalid output cannot request execution or edits and yields a clear limitation after retry exhaustion.

**Acceptance:** Only schema-valid, evidence-linked diagnoses reach reporters or patch generation.

## P7-T4 — Integrate static and runtime diagnosis flows

**Definition:** Add SLM explanation to static-only and execution-backed debugging while retaining deterministic evidence and explicit unsupported-case behaviour.

**Files:** `localdev/agent/orchestrator.py`, `localdev/cli.py`, `localdev/reporting/terminal.py`, `tests/integration/test_diagnosis_flow.py`.

**In scope:** Syntax/name/type/index/key/value/common file-local logic cases; no-bug recognition; cross-file abstention; concise terminal summary.

**Not in scope:** Automatic fixes; guarantees for logical bugs; external source inspection.

**Testing:** Use fake deterministic model responses for orchestration; evaluate real model separately on initial bug samples; cover no-bug and unsupported cross-file cases; assert deterministic evidence remains visible when inference is unavailable.

**Acceptance:** Common errors receive grounded explanations, and unsupported cases are qualified rather than invented.

---

# Phase 8 — Structured edit generation and safe application

## P8-T1 — Define and validate bounded edit proposals

**Definition:** Accept structured line edits only when the target path, ranges, expected text, overlap rules, edit count, and changed-line count satisfy policy.

**Files:** `localdev/patching/edit_schema.py`, `localdev/patching/edit_validator.py`, `localdev/inference/response_validator.py`, `tests/unit/test_edit_validator.py`.

**In scope:** One canonical target; maximum 8 edits/80 changed lines by default; exact expected-text match; ordered non-overlapping ranges; reason field.

**Not in scope:** Arbitrary unified diffs from the model; file creation/deletion/rename; edits to imports in another file; binary patches.

**Testing:** Cover valid insert/replace/delete cases and reject wrong target, traversal, absolute alternate path, overlap, reversed/out-of-range lines, stale expected text, excessive edits, excessive changed lines, and mixed newline payloads.

**Acceptance:** No malformed or boundary-violating model proposal can reach an applier.

## P8-T2 — Apply edits to a temporary copy and render a diff

**Definition:** Apply validated edits to an in-memory/session copy, preserve representation facts, and generate the unified diff locally for review.

**Files:** `localdev/patching/applier.py`, `localdev/patching/diff_renderer.py`, `tests/unit/test_patch_applier.py`, `tests/unit/test_diff_renderer.py`.

**In scope:** Deterministic multi-edit application; original encoding/BOM/newline/final-newline; contextual unified diff; no source mutation.

**Not in scope:** Applying directly to the target; model-generated diff parsing; formatting unrelated lines.

**Testing:** Golden tests for insert/replace/delete/multiple edits; UTF-8 BOM, supported legacy encoding, CRLF, and no-final-newline fixtures; byte-compare the original before/after proposal and snapshot diffs.

**Acceptance:** The temporary candidate exactly represents the approved structured edits and the source remains byte-for-byte unchanged.

## P8-T3 — Implement confirmed atomic replacement and optional backup

**Definition:** After validation and explicit confirmation, re-resolve the target, reject reparse points, recheck its SHA-256, optionally create a backup, and atomically replace only that file while preserving supported metadata.

**Files:** `localdev/patching/atomic_write.py`, `localdev/patching/backup.py`, `localdev/agent/permissions.py`, `tests/windows/test_atomic_write.py`.

**In scope:** Interactive confirmation; noninteractive `--apply` only when explicitly supplied; immediate hash check; same-directory temporary replacement; backup policy; read-only and replacement-failure reporting.

**Not in scope:** Permission escalation; overwriting editor changes; following symlinks/reparse points; modifying a second source file; silent fallback to non-atomic overwrite.

**Testing:** Confirm yes/no paths; mutate target between proposal and apply; test read-only target, symlink/reparse rejection, backup creation, replacement failure, CRLF/encoding preservation, and simulated crash before replacement; verify only expected files change.

**Acceptance:** A source write occurs only with current content matching the analysed hash and explicit authority, with failures leaving the original recoverable.

## P8-T4 — Deliver `fix` and `--propose-fix` workflows

**Definition:** Connect diagnosis, structured proposal, validation, temporary application, diff display, and guarded apply under a maximum-attempt policy.

**Files:** `localdev/agent/orchestrator.py`, `localdev/cli.py`, `localdev/reporting/terminal.py`, `tests/integration/test_fix_workflow.py`.

**In scope:** Finding selection; up to two proposal attempts; display reasons and limitations; default proposal-only behaviour; confirmation path.

**Not in scope:** Fully autonomous repair loops; applying an unvalidated edit; choosing among multiple source files.

**Testing:** End-to-end fake-model cases for valid edit, invalid-then-valid retry, two invalid responses, declined confirmation, stale hash, wrong finding, and unsupported issue; assert target writes occur only in the accepted valid case.

**Acceptance:** Users can safely review a minimal proposal, while model failure never becomes a source mutation.

---

# Phase 9 — Differential patch validation

## P9-T1 — Implement structural and Ruff baseline comparison

**Definition:** Validate candidate syntax/compilation and compare normalized Ruff findings before and after, accounting for shifted locations and distinguishing new, removed, and unchanged diagnostics.

**Files:** `localdev/patching/applier.py`, `localdev/languages/python/diagnostics.py`, `localdev/agent/evidence.py`, `tests/unit/test_diagnostic_diff.py`.

**In scope:** Validation Level A; original finding identity; rule/message/location matching policy; pre-existing-warning tolerance; new-diagnostic reporting.

**Not in scope:** Requiring an imperfect file to become Ruff-clean; suppressing newly introduced findings; behaviour claims.

**Testing:** Cover unchanged baselines, removed target finding, shifted unchanged finding, new finding, syntax regression, and duplicate diagnostics; prove a patch can pass with unrelated pre-existing warnings but not with a new regression.

**Acceptance:** Structural validity is based on a reproducible before/after comparison rather than absolute cleanliness.

## P9-T2 — Compare runtime failure signatures on the candidate

**Definition:** Re-execute the temporary candidate under identical inputs/limits and determine whether the original failure disappeared, changed, timed out, or execution succeeded.

**Files:** `localdev/agent/orchestrator.py`, `localdev/languages/python/traceback_parser.py`, `localdev/schemas.py`, `tests/integration/test_runtime_patch_validation.py`.

**In scope:** Validation Levels B and C; normalized exception type/message/top target frame; new exception detection; same limits and args/stdin; candidate-only execution.

**Not in scope:** Treating exit zero as correct output; executing the original source after it is overwritten; loosening limits to make a patch pass.

**Testing:** Fixtures where the original exception is removed, unchanged, replaced by another exception, replaced by timeout, and fully succeeds; assert classifications and that original source bytes remain unchanged throughout validation.

**Acceptance:** Reports accurately separate “original failure removed” from “execution succeeds.”

## P9-T3 — Add explicit behavioural checks and validation-level reporting

**Definition:** Support expected stdout equality/substring and documented exit expectations, then calculate the highest justified validation level A–D with evidence.

**Files:** `localdev/agent/orchestrator.py`, `localdev/schemas.py`, `localdev/reporting/terminal.py`, `tests/integration/test_behaviour_validation.py`.

**In scope:** Expected stdout/substring options; output normalization policy; Level D only when an explicit expectation passes; regression and limitation summaries.

**Not in scope:** Automatic project test discovery; arbitrary shell validation commands; claiming behavioural verification without an oracle.

**Testing:** Cover exact pass/fail, substring pass/fail, newline normalization, wrong exit code, truncated output, and absent expectation; assert Level D is impossible without a provided expectation and Level C wording does not imply correctness.

**Acceptance:** Every patch report states precisely what was checked and never overstates validation strength.

---

# Phase 10 — Static time, auxiliary-space, and output-space analysis

## P10-T1 — Define the restricted cost model and complexity result contract

**Definition:** Encode the supported operations, assumptions, target classes, confidence levels, and abstention reasons for file-local Python functions.

**Files:** `localdev/languages/python/complexity.py`, `localdev/schemas.py`, `docs/architecture.md`, `tests/unit/test_complexity_cost_model.py`.

**In scope:** `O(1)`, `O(log n)`, `O(n)`, `O(n log n)`, `O(n²)`, `O(n³)`, `O(nm)`; list/set membership distinctions; sort, slicing, collection growth, output generation; named input dimensions.

**Not in scope:** Arbitrary symbolic algebra; data-dependent guarantees; cross-file callees; silently assuming all calls are constant time.

**Testing:** Table-driven operation tests; verify every rule emits its assumptions and source location; reject unknown/dynamic operations or lower confidence rather than fabricating a class.

**Acceptance:** Complexity results use an explicit, reviewable model instead of unexplained model guesses.

## P10-T2 — Analyse loops, nesting, built-ins, and collection growth

**Definition:** Traverse AST facts to combine sequential and nested costs, recognize common logarithmic/linear operations, and track auxiliary versus returned/output allocation.

**Files:** `localdev/languages/python/complexity.py`, `tests/unit/test_complexity_iterative.py`, `tests/complexity_samples/iterative/`.

**In scope:** Constant/linear ranges; independent and dependent nested loops; sorting; slicing; membership; comprehensions; string concatenation; generator use; returned collections.

**Not in scope:** Precise average-case container analysis; external function bodies; optimization claims based on runtime profiling.

**Testing:** Golden fixtures for every target class and space distinction; cases for `list` vs `set` membership, returned list vs temporary list, generator vs materialized collection, sequential vs nested loops, and ambiguous bounds; compare class, assumptions, confidence, and cited lines.

**Acceptance:** Basic iterative functions are classified consistently, with time, auxiliary space, and output space reported separately.

## P10-T3 — Add bounded recursion analysis and abstention

**Definition:** Detect simple direct linear, divide-and-conquer, and fixed binary recursion patterns; account for call-stack space; abstain on unsupported mutual/dynamic recursion.

**Files:** `localdev/languages/python/complexity.py`, `tests/unit/test_complexity_recursion.py`, `tests/complexity_samples/recursive/`.

**In scope:** Base case recognition; argument progress; recurrence templates; recursion-depth space; confidence reduction for uncertain branches.

**Not in scope:** General recurrence solving; mutual recursion across functions/files; proof of termination.

**Testing:** Factorial/linear recursion, binary recursion, binary search, merge-sort-like structure, missing base case, mutual recursion, and data-dependent recursion; assert unsupported forms abstain with a reason.

**Acceptance:** Supported recursive patterns receive conservative results and all other patterns fail honestly.

## P10-T4 — Deliver the `complexity` command

**Definition:** Support whole-file summaries and `file.py::function_name`/class-method selectors with source-linked evidence, assumptions, confidence, and JSON output.

**Files:** `localdev/languages/python/selectors.py`, `localdev/agent/orchestrator.py`, `localdev/cli.py`, `tests/integration/test_complexity_command.py`.

**In scope:** Module functions and class methods; ambiguity errors; syntax-first behaviour; deterministic analysis without SLM dependency.

**Not in scope:** Nested-function selectors in MVP; imported call analysis; dynamic profiling.

**Testing:** Invoke file-wide, function, method, missing, duplicate/ambiguous, nested, and syntax-invalid cases; validate JSON and terminal wording; prove the command neither imports nor executes the target.

**Acceptance:** Users receive actionable complexity estimates with visible assumptions and safe abstention.

---

# Phase 11 — Function profiling

## P11-T1 — Parse selectors and load targets in an isolated worker

**Definition:** Resolve supported module functions and basic class methods inside a disposable subprocess, measure import separately, and return clear unsupported/side-effect results.

**Files:** `localdev/profiling/loader.py`, `localdev/profiling/worker.py`, `localdev/languages/python/selectors.py`, `tests/unit/test_profile_selector.py`, `tests/windows/test_profile_loader.py`.

**In scope:** `file.py::function`; documented class-method form; worker protocol; import stdout/stderr; import duration; exception/timeout; controlled session copy.

**Not in scope:** Nested functions; arbitrary object construction; suppressing unavoidable import-time code; importing local project dependencies by discovery.

**Testing:** Profile module function, static/class method as supported, missing/ambiguous/nested selector, slow import, import exception, import output, and import-time child process; verify worker cleanup and separated import results.

**Acceptance:** Target loading cannot destabilize the CLI process and import limitations are explicit.

## P11-T2 — Define JSON inputs and recreate them for each run

**Definition:** Parse a bounded JSON input file into positional/keyword arguments and produce a fresh deep reconstruction for every warm-up and measured invocation.

**Files:** `localdev/profiling/benchmark.py`, `localdev/profiling/worker.py`, `tests/unit/test_profile_inputs.py`, `tests/profiling_samples/inputs/`.

**In scope:** Documented `{ "args": [], "kwargs": {} }` shape; size/depth limits; JSON-native values; mutation isolation; validation errors.

**Not in scope:** Pickle; arbitrary object deserialization; Python expressions; custom constructors; secret/input-file discovery.

**Testing:** Cover valid args/kwargs, missing keys, invalid JSON, oversized/deep input, mutable nested structures, and a function that mutates input; assert every run sees an equivalent fresh value.

**Acceptance:** Profiling inputs are safe to parse and repeated timings are not contaminated by prior mutation.

## P11-T3 — Measure repeated function time and Python allocations

**Definition:** Run configured warm-ups and measured invocations, report median and dispersion, and use `tracemalloc` to report Python allocation peaks separately.

**Files:** `localdev/profiling/timer.py`, `localdev/profiling/python_memory.py`, `localdev/profiling/worker.py`, `tests/windows/test_function_timing.py`.

**In scope:** Two warm-ups/seven measured runs by default; high-resolution timer; per-run fresh input; function exception/timeout; median and individual samples; Python allocation label.

**Not in scope:** Benchmark-grade CPU isolation; hiding system variability; counting native allocations as Python allocations; concurrent workers.

**Testing:** Use stable sleep/busy-work fixtures with tolerant bounds, mutation cases, exception on a later run, and allocation growth; verify warm-ups are excluded and statistics match raw samples.

**Acceptance:** Timing is repeatable enough for controlled examples and all sources of measurement are clearly labeled.

## P11-T4 — Add parent-side process RSS sampling and deliver `profile`

**Definition:** Monitor the worker/process tree externally while it imports and invokes the target, integrate import time, function timing, Python allocations, and approximate RSS into one report.

**Files:** `localdev/profiling/process_memory.py`, `localdev/profiling/benchmark.py`, `localdev/agent/orchestrator.py`, `localdev/cli.py`, `tests/integration/test_profile_command.py`.

**In scope:** Same process controls as debug; backend/sample interval; peak worker-tree RSS; selector/input CLI; terminal/JSON output; one job at a time.

**Not in scope:** Growing-input curve fitting in MVP; complexity inference from a single benchmark; GPU memory.

**Testing:** End-to-end pure, mutating, stateful, allocation-heavy, exception, timeout, slow-import, class-method, and unsupported nested-function fixtures; assert metrics remain distinct and descendants are cleaned up.

**Acceptance:** `localdev profile` produces bounded, transparent measurements without conflating setup, invocation, Python allocations, and process RSS.

---

# Phase 12 — Evaluation, optimisation, documentation, and release

## P12-T1 — Build the evaluation datasets and harness

**Definition:** Create independently runnable datasets and a harness that measures deterministic correctness, real-model usefulness, abstention, patch safety, complexity accuracy, profiling stability, and process cleanup.

**Files:** `tests/bug_samples/`, `tests/complexity_samples/`, `tests/profiling_samples/`, `tests/boundary_samples/`, `tools/evaluate.py`, `docs/evaluation.md`.

**In scope:** Approximately 60–80 debugging, 35–50 complexity, representative profiling, Windows process, and file-preservation cases; expected labels; deterministic-tools baseline; SLM comparison; smaller-vs-larger model comparison.

**Not in scope:** Training on evaluation answers; hiding failures; reducing unsupported cases merely to improve headline accuracy.

**Testing:** Validate dataset manifests and uniqueness; run harness twice for reproducibility; manually audit a sample from each category; ensure correct/no-bug and unsupported cases are included; schema-check all raw result artifacts.

**Acceptance:** Every published metric can be reproduced from versioned cases and raw outputs.

## P12-T2 — Measure quality, safety, performance, and resource budgets

**Definition:** Execute the complete evaluation on the target Windows laptop and report accuracy, false positives, abstention, patch validity, boundary violations, latency, RAM, timeout cleanup, and profiling stability.

**Files:** `docs/evaluation.md`, `docs/security.md`, generated evaluation artifacts excluded or retained per repository policy.

**In scope:** Cold/warm model latency; peak total RAM; time to diagnosis; patch attempts; new-diagnostic rate; file-preservation failures; process cleanup; exact and acceptable complexity accuracy.

**Not in scope:** Unsupported generalization claims; benchmark comparisons without equivalent settings; editing implementation solely to hide an adverse metric.

**Testing:** Cross-check aggregate metrics against raw cases; deliberately inject one known failure to verify harness sensitivity; rerun flaky timing/process cases; require zero single-file boundary violations before release.

**Acceptance:** Strong product claims are backed by measured results, and known weaknesses are documented plainly.

## P12-T3 — Optimize within the 8 GB laptop budget

**Definition:** Tune context selection, process overlap, model lifecycle, output caps, and profiling scheduling based on measurements without relaxing correctness or safety boundaries.

**Files:** `localdev/config.py`, `localdev/agent/context_builder.py`, `localdev/inference/ollama_client.py`, `localdev/execution/limits.py`, `docs/evaluation.md`.

**In scope:** 2,048-token default context; short structured responses; one model process; one runner/profiler at a time; 1.5B fallback; prompt/source caps; prompt construction benchmarks.

**Not in scope:** Loading two models simultaneously; reducing validation to gain speed; claiming support until measured on the target machine.

**Testing:** Repeat representative cold/warm workflows before and after changes; compare correctness and schema validity; stress near maximum source/output/input limits; verify peak RAM leaves a documented safety margin.

**Acceptance:** Standard evaluation workflows complete on the selected laptop without memory exhaustion and without measurable safety regression.

## P12-T4 — Complete user, architecture, security, and operations documentation

**Definition:** Document installation, offline setup, command examples, architecture, configuration, validation semantics, trust limitations, troubleshooting, evaluation method, and future work.

**Files:** `README.md`, `docs/architecture.md`, `docs/security.md`, `docs/evaluation.md`, `docs/demo.md`.

**In scope:** PowerShell commands; model setup; supported versions; trusted-code warning; analysis/model/execution boundaries; JSON examples; Level A–D explanation; known limitations; uninstall/cleanup guidance.

**Not in scope:** Marketing claims beyond measured capability; describing Job Objects as a sandbox; undocumented flags.

**Testing:** Follow installation and demo instructions in a clean Windows environment; run command snippets; link-check local docs; verify every CLI flag appears in docs and every limitation appears in both README/security docs where appropriate.

**Acceptance:** A new user can install, understand, safely operate, and accurately describe the tool without private project knowledge.

## P12-T5 — Rehearse and sign off the final demonstration

**Definition:** Prepare one safe, dependency-free Python example with a reproducible `TypeError`, explicit expected output, an `O(n²)` function, and an `if __name__ == "__main__":` entry point; rehearse the complete product story.

**Files:** `examples/demo.py`, `examples/benchmark.json`, `docs/demo.md`, `tests/integration/test_demo_sequence.py`.

**In scope:** Detect; analyse; execute failure; deterministic evidence; SLM diagnosis; minimal proposal; Level A–D validation; confirmation; re-run; complexity; profile; unsupported-case demonstration.

**Not in scope:** Hand-editing between recorded steps; network use; external dependencies; concealing limitations or failed checks.

**Testing:** Automate the sequence with a disposable demo copy; verify expected outputs and reports; perform one manual PowerShell rehearsal on the target laptop; confirm the original demo fixture remains reproducible after the run.

**Acceptance:** The demonstration succeeds from a clean setup, shows user control and evidence before inference, and includes an honest limitation case.

---

## 7. Cross-cutting test matrix

| Area | Required cases |
|---|---|
| CLI | Missing/multiple target, invalid option, `--` args, JSON output, stable exit codes |
| Paths | Spaces, Unicode, long practical paths, relative/absolute paths, nonexistent paths |
| File preservation | UTF-8, BOM, supported encoding declaration, CRLF, no final newline, read-only, reparse point |
| Static analysis | Clean, syntax error, multiple Ruff findings, isolated config, no bytecode |
| Runtime | Success, nonzero exit, timeout, stdout/stderr flood, blocking stdin, child/grandchild |
| Diagnosis | Grounded answer, malformed response, invented evidence, no bug, cross-file abstention |
| Patching | Wrong target, stale hash, overlap, invalid range, declined apply, atomic failure, backup |
| Validation | Pre-existing warning, new warning, same/new exception, success, explicit expected output |
| Complexity | All target classes, auxiliary/output split, recursion, ambiguous/dynamic abstention |
| Profiling | Pure/mutating/stateful function, import side effect, exception, timeout, memory growth |
| Resources | 1.5B/3B model, cold/warm run, max context, max output, single-process enforcement |

## 8. Release gates

The MVP must not be declared complete until all gates pass:

1. Exactly one source target is enforced across every command.
2. Static-only commands never execute or import target code.
3. Ruff isolation is verified against conflicting local configuration.
4. Runtime limits retain partial output and clean ordinary descendant fixtures.
5. Reports state the actual process-control and memory-measurement backend.
6. Invalid model responses cannot execute commands, propose unvalidated edits, or write files.
7. No patch touches the source before temporary application, validation, display, and confirmation.
8. Hash changes and reparse-point targets are rejected at apply time.
9. Encoding, BOM, newline style, and final-newline state pass preservation fixtures.
10. Validation levels A–D are assigned only from their defined evidence.
11. Complexity reports separate time, auxiliary space, and output space and can abstain.
12. Profiling separates import time, function time, Python allocations, and approximate RSS.
13. Evaluation contains correct programs and unsupported cases, not only known bugs.
14. Standard cases run within the measured memory budget on the selected Windows laptop.
15. README and security documentation prominently state “user-owned or trusted code only” and “not a security sandbox.”

## 9. Scope-reduction order

If schedule pressure requires reducing the MVP, defer work in this order:

1. Growing-input profiling (already excluded from the core task list).
2. Class-method profiling.
3. Job Objects, retaining the explicitly labeled psutil prototype.
4. Non-default Ruff rule customization.
5. Complexity classes beyond the basic supported set.
6. Custom behavioural validation beyond stdout/exit expectations.

The following may not be removed: single-file enforcement, trusted-code warning, temporary patch application, structured response validation, confirmation before write, immediate hash recheck, file representation preservation, honest validation levels, and explicit abstention.

## 10. Principal risks and mitigations

| Risk | Mitigation and owning tasks |
|---|---|
| Model exceeds RAM | Benchmark/fallback and bounded context in P1-T3, P7-T2, P12-T3 |
| Model invents evidence | Evidence IDs and rejection in P7-T2/P7-T3 |
| Malformed or broad edits | Structured limits in P8-T1 and temporary application in P8-T2 |
| Existing warnings reject useful patch | Differential baseline in P9-T1 |
| Descendants survive timeout | psutil cleanup P5-T3 and Job Objects P6-T1/P6-T2 |
| Output exhausts memory | Incremental bounded capture P5-T2 |
| Memory results are misread | Backend and approximation labels P6-T3/P11-T4 |
| Profile import causes side effects | Disposable controlled worker P11-T1 |
| Timing is unstable | Warm-ups, repeated runs, raw samples P11-T3 |
| Editor changes are overwritten | Immediate SHA-256 recheck P8-T3 |
| Encoding/newlines change | Representation-aware temporary and atomic writes P8-T2/P8-T3 |
| User assumes sandboxing | Repeated trust warning and documentation P1-T1/P12-T4 |
| Schedule is too broad | Apply the defined scope-reduction order without removing release gates |

## 11. Completion statement

The project is complete when all task acceptance criteria and release gates pass on the selected Windows laptop; the evaluation report contains reproducible evidence; the complete demo works offline after installation; and the documentation accurately limits the tool to one user-owned or trusted Python source file per command.
