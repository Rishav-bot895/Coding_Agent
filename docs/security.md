# Security Policy and Trust Model: localdev

> **Personal Portfolio Project Scope:**
> `localdev` is a personal portfolio project demonstrating high engineering rigor, clean Windows systems integration, and reproducible local AI agent orchestration. It is **not intended to be production-grade infrastructure** or enterprise multi-tenant software.
>
> **Supported Platform:** **Windows 11 x64 only**. All other operating systems and legacy Windows releases are explicitly unsupported.

---

## 1. Core Trust Model and Non-Sandbox Boundary

### User-Owned / Trusted Python Code Only
`localdev` is an offline development assistant intended exclusively for **user-owned or trusted Python code**. Subprocess execution occurs under the operating system identity and privileges of the invoking user.

### Explicit Non-Sandbox Disclaimer
> [!CAUTION]
> **`localdev` is NOT a security sandbox.**
> Operational process limits (Windows Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, execution wall-clock timeouts, and output byte caps) are designed to prevent accidental runaway loops, unbounded memory growth, and runaway child processes.
>
> **They do not protect against malicious code designed to compromise the host.**
> Malicious scripts could read, overwrite, or delete accessible files, access local network interfaces, or execute system binaries within the invoking user's permissions. Do NOT run untrusted or hostile Python scripts through `localdev`.

---

## 2. Single-Target Scope vs Runtime Execution

### Intentional Single-Target Scope
- `localdev` operates on **exactly one explicitly supplied Python source target file** per command for inspection, analysis, diagnosis, patching, and validation.
- Secondary user-supplied inputs (e.g., `--input` JSON file for profiling or `--expected-stdout` strings for validation) provide execution or assertion data and do not act as additional source targets.
- **No Automatic Discovery:** `localdev` never automatically discovers, crawls, parses, or mutates sibling files, parent directories, local packages, project configuration files (`pyproject.toml`, `setup.cfg`), or `.git` repositories.

### Runtime Import Semantics
- The project is intentionally **single-target, not necessarily "single-file at runtime."**
- When execution occurs (during `debug`, candidate patch validation, or `profile`), the target code is executed as real Python by the selected interpreter.
- Target scripts are free to import installed third-party packages, standard library modules, or local sibling modules reachable through standard Python runtime search mechanisms.
- Sibling files or imported dependencies are never inspected, analyzed, diagnosed, or patched. Only the single explicit target is subject to analysis and mutation.

---

## 3. Subprocess Execution Environment and Isolation Policy

When `localdev` executes target code, it enforces a strict execution policy:

### 1. Interpreter Launch Flags: `-E -B -P`
- `-E`: Ignores all ambient `PYTHON*` environment variables (such as `PYTHONPATH`, `PYTHONHOME`, `PYTHONDEBUG`), preventing host environment variable poisoning.
- `-B`: Sets `PYTHONDONTWRITEBYTECODE=1`, preventing the interpreter from creating `.pyc` files or `__pycache__` directories in the workspace.
- `-P`: (`PYTHONSAFEPATH` in Python 3.11+): Prevents Python from automatically prepending the script's directory or current working directory into `sys.path`.

### 2. Why Isolated Mode (`-I`) is NOT Used
Python provides an isolated mode flag (`-I`), but `localdev` deliberately avoids it because:
- `-I` implicitly enables `-s` (disabling user site-packages).
- `-I` isolates `sys.path` in ways that break virtual environments and prevent legitimate third-party packages installed in the active environment from loading.
- Instead, `localdev` pairs `-E -B -P` with an explicit OS environment variable allowlist. This achieves operational isolation while allowing trusted user scripts to utilize installed packages.

### 3. Environment Variable Allowlist
Child processes inherit only essential operating system environment variables required for basic runtime stability:
- `SYSTEMROOT`
- `SYSTEMDRIVE`
- `PATH`
- `PATHEXT`
- `TEMP`
- `TMP`
- `COMSPEC`
- `USERNAME`

All other host environment variables are stripped before process launch.

### 4. Working Directory (`cwd`) and `__file__` Relocation Semantics
- **Working Directory (`cwd`):** Defaults to the user's invocation working directory (where `localdev` was run), **NOT** the session directory. This preserves relative file path lookups (e.g., `open("data.csv")`) so user scripts behave as expected.
- **Relocated Session Copy:** During execution, `localdev` runs a temporary copy of the target staged at `%TEMP%\localdev\session_<id>\session_target.py`.
- **`__file__` Limitations:** At runtime, `__file__` points to the relocated session copy. Consequently:
  - Code looking up resources relative to `Path(__file__).parent` will resolve to the temporary session directory, not the original file's folder.
  - Sibling resources beside the original source file will not be found via `__file__` unless located relative to `cwd`.
  - `localdev` intentionally does not copy surrounding project trees or reconstruct package hierarchies.
  - For user reports, `localdev` normalizes frame references in tracebacks and diagnostic reports back to the canonical user target path.

---

## 4. Guarded Patch Lifecycle and Atomic File Replacement

Every model-proposed patch passes through rigorous, non-bypassable safety gates:

### 1. Schema Validation
- Model proposals must conform strictly to `EditProposalRecord`.
- Enforces 1-based, inclusive line ranges (`start_line`, `end_line`), explicit operation types (`replace`, `insert`, `delete`), and non-empty `expected_text`.
- Proposals are capped at a maximum of **8 edits** and **80 changed lines**. Proposals containing shell commands, file creation outside the target, or malformed ranges are rejected.

### 2. Temporary Candidate Validation
- Proposed edits are applied strictly to an isolated temporary candidate copy.
- The original target file is **never modified in-place**.
- Candidate code undergoes differential static analysis (AST parsing, Ruff linter) and execution validation (Levels A–D) before presenting to the user.

### 3. Same-Volume Staging Requirement
- Win32 `ReplaceFileW` kernel constraints require the replacement candidate, target file, and backup file to reside on the **same filesystem volume**.
- General session metadata and execution copies reside in `%TEMP%\localdev\session_<id>` (typically drive `C:`).
- If the target file resides on another volume (e.g., `D:\project\target.py`), `localdev` establishes candidate staging and backup on volume `D:` (e.g., `D:\.localdev_staging\...` and `D:\project\target.py.bak`).
- Any attempt to replace files across different filesystem volumes fails closed.

### 4. Compare-Before-Replace SHA-256 Stale-Edit Detection
- Immediately before initiating replacement, `localdev` computes the SHA-256 hash of the target file and compares it to the baseline hash captured when the command began.
- If the hash differs (e.g., the user edited the file in an external editor or IDE during analysis), replacement aborts immediately to prevent overwriting unreviewed changes.
- **Technical Distinction:** This SHA-256 check provides stale-edit detection against external modifications; it is not an atomic hardware Compare-And-Swap (CAS) across race windows.

### 5. Atomic File Replacement via Win32 `ReplaceFileW`
- Final mutation invokes native Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)`.
- If a backup file is requested, `ReplaceFileW` creates the native backup atomically alongside the swap. If backup creation fails, replacement aborts fail-closed.
- **Durability Boundary:** `ReplaceFileW` guarantees atomic directory-entry swapping under normal OS operation (eliminating partial or corrupt writes). Hardware-level power-loss flush durability is outside the MVP.

### 6. Noninteractive `--apply` Authority Invariants
- The `--apply` flag grants write authority without presenting an interactive terminal prompt.
- **Safety Gate Preservation:** `--apply` **never** bypasses SHA-256 hash checks, static validation (Level A), reparse-point protections, or same-volume invariants.
- **No `--force` Flag:** There is no flag to bypass safety gates or force overwrite invalid candidates.

### 7. Reparse Points and Symlinks
- Symlinks and NTFS junctions are detected and **strictly rejected as write targets** to prevent symlink redirection attacks.

---

## 5. Terminal Display Sanitization vs Raw JSON Preservation

### Terminal Output Sanitization
- All user source text, stdout, stderr, exception tracebacks, and model-generated text are sanitized prior to rendering in the terminal.
- Sanitization strips or escapes:
  - ANSI escape sequences (`\x1b[...]`)
  - Virtual Terminal (VT) command sequences (CSI, OSC, cursor positioning, screen clearing)
  - Non-printable control characters (`\x00`–`\x08`, `\x0b`–`\x1f`, `\x7f`), while preserving `\n` and `\t`.
- Prevents malicious terminal escape injection that could conceal output, spoof prompts, or alter terminal state.

### Raw JSON Data Preservation
- When `--json` is supplied, raw strings (including tracebacks, stdout, stderr) are preserved verbatim per RFC 8259 JSON encoding rules, allowing downstream tooling to inspect unaltered data.

