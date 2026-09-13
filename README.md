# localdev — Native Windows Single-File Offline Python Coding Agent

> **Project Framing & Portfolio Scope:**
> This repository is a **personal resume/portfolio project** created to demonstrate systems-level Windows integration, deterministic static analysis, defensive subprocess containment, and reproducible local SLM orchestration. It is **not intended to be production-grade enterprise software** or multi-user infrastructure.
>
> **Target Platform:** Supported exclusively on **Windows 11 x64 only**. All other operating systems and legacy Windows versions are intentionally out of scope.

---

## 1. Safety Boundaries, Trust Model, and Invariants

### Trust Boundary & Non-Sandbox Disclaimer
- **User-Owned / Trusted Code Only:** `localdev` is an offline development assistant intended exclusively for inspecting, running, and modifying Python code you own or trust.
- **NOT a Security Sandbox:** Operational limits (Windows Job Objects with `KILL_ON_JOB_CLOSE`, execution timeouts, combined output byte limits) are designed to terminate accidental runaway tasks, infinite loops, and fork-bombs. **They do not constitute a security boundary against hostile or malicious code.** Subprocesses execute with the operating system privileges of the invoking user.

### Strict Single Source Target Scope
- `localdev` accepts **exactly one explicit Python source target file** per command.
- The project is intentionally **single-target, not necessarily "single-file at runtime."** Runtime execution behaves like normal Python and is free to import third-party packages or read local filesystem resources required by the target.
- Other source files in the project, workspace, or sibling directories are **never automatically discovered, traversed, inspected, diagnosed, or patched**.
- Secondary user-supplied inputs (e.g., `--input` JSON file for profiling or `--expected-stdout` strings for validation) provide execution or assertion data and do not act as additional source targets.

### Runtime and Isolation Contract
When target code is executed (during `debug`, patch validation, or `profile`), it runs under a strictly governed environment:
- **Interpreter Flags:** Launched with `python.exe -E -B -P`:
  - `-E`: Ignores ambient `PYTHON*` environment variables (`PYTHONPATH`, `PYTHONHOME`, etc.).
  - `-B`: Suppresses bytecode compilation (`.pyc` files and `__pycache__` directories).
  - `-P`: Suppresses automatic prepending of ambient script or current working directories into `sys.path`.
- **Why Isolated Mode (`-I`) is NOT Used:** Python's `-I` flag implicitly activates `-s` (disabling user site-packages) and alters `sys.path` in ways that break virtual environments and prevent legitimate third-party dependencies from loading. `localdev` provides operational isolation via `-E -B -P` while allowing installed virtualenv dependencies to function normally.
- **Environment Allowlist:** Subprocesses inherit only essential OS environment variables: `SYSTEMROOT`, `SYSTEMDRIVE`, `PATH`, `PATHEXT`, `TEMP`, `TMP`, `COMSPEC`, and `USERNAME`. All others are stripped.
- **Working Directory (`cwd`):** Child processes execute with `cwd` set to the user's invocation directory (preserving standard relative file path lookups like `open("data.csv")`).
- **`__file__` Relocation Semantics:** Subprocess execution targets a temporary session copy located at `%TEMP%\localdev\session_<id>\session_target.py`. While terminal reports normalize paths back to the canonical user target, `__file__` at runtime points to the session copy. Sibling resources accessed relative to `Path(__file__).parent` will not resolve unless explicitly placed relative to `cwd`. This is an intentional limitation of the single-target model.

### Guarded Atomic Replacement and Same-Volume Staging
- **Same-Volume Staging:** Win32 `ReplaceFileW` strictly requires the replacement candidate, target file, and backup file to reside on the **same filesystem volume**. General session metadata lives in `%TEMP%`, but candidate staging and backup (`<target>.bak`) are allocated on the target file's volume (e.g., `D:\.localdev_staging\...`). Cross-volume attempts fail closed.
- **Compare-Before-Replace SHA-256:** Immediate hash verification guards against stale edits and external file modifications prior to replacement.
- **Native Atomic Replacement:** File mutation uses `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)`. Power-loss durability across sudden hardware failure is outside the MVP scope.
- **Noninteractive `--apply` Safety:** The `--apply` flag authorizes write actions without an interactive confirmation prompt, but **never bypasses hash checks, static validation gates, or reparse-point protections**. No `--force` bypass flag exists.

### Display Sanitization vs Raw JSON Preservation
- **Terminal Output:** Sanitized against ANSI/VT escape sequences, CSI/OSC control codes, and non-printable control characters to prevent terminal display corruption or cursor spoofing.
- **Machine-Readable Output:** The versioned JSON envelope (`--json`, `schema_version: "1.0"`) preserves underlying raw data verbatim per RFC 8259.

---

## 2. CLI Command Surface

Every command operates on exactly one explicit source target:

| Command | Purpose | Code Execution? |
|---|---|---|
| `localdev info <target>` | Inspect file size, lines, SHA-256 hash, PEP 263 encoding, BOM, newline style, and read-only attributes. | **No** (static metadata) |
| `localdev detect <target>` | Detect Python language confidence via file extension, shebang, and non-executing AST parse. | **No** (static AST) |
| `localdev analyse <target>` | Static syntax check, bounded AST fact extraction, and isolated Ruff diagnostics with zero cache pollution. | **No** (deterministic static) |
| `localdev debug <target>` | Controlled runtime execution (`-E -B -P`), pipe-capped output draining, process tree termination, and traceback parsing. | **Yes** (controlled subprocess) |
| `localdev fix <target>` | Evidence-grounded local SLM diagnosis (Ollama) and guarded atomic patch proposal. Supports `--apply`. | **Yes** (candidate validation) |
| `localdev complexity <target>` | Static Big-O time, auxiliary-space, and output-space analysis under CPython runtime semantics with mandatory abstention. | **No** (static AST) |
| `localdev profile <target>` | Function/method profiling in disposable worker: separate import cost, execution latency, tracemalloc allocations, and RSS. | **Yes** (isolated worker) |

---

## 3. Pinned Toolchain & Evaluation Environment

For reproducible evaluation on native Windows 11 x64:
- **Python:** 3.12.10 (64-bit) (tested compatibility matrix: 3.11 and 3.12)
- **Pydantic:** 2.8.2
- **Ruff:** 0.5.0
- **psutil:** 6.0.0
- **Ollama:** 0.3.0
- **Primary SLM:** `qwen2.5-coder:3b-instruct-q4_K_M`
- **Fallback SLM (8 GB RAM):** `qwen2.5-coder:1.5b-instruct-q4_K_M`

---

## 4. Installation & Quickstart

### Prerequisites
- Windows 11 x64
- Python 3.11 or 3.12 (64-bit)
- PowerShell 7+ or Windows Terminal

### Setup
```powershell
# Clone and enter the repository
cd d:\Project\Coding_Agent

# Create a clean virtual environment using Python 3.12
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Upgrade pip and install package in editable mode with development dependencies
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# Verify CLI installation
localdev --help
```

### Running Quality Checks
```powershell
# Run unit and integration tests
python -m pytest

# Run native Windows tests
python -m pytest -m windows

# Run Ruff linter in isolated mode (zero cache pollution)
ruff check --isolated --no-cache .

# Run strict Mypy type validation
python -m mypy --strict localdev
```

---

## 5. Repository Layout

```text
localdev/
├── pyproject.toml              # PEP 518/621 package metadata and pinned dependencies
├── README.md                   # Project overview, scope, and safety boundaries
├── LICENSE                     # MIT License
├── docs/
│   ├── architecture.md         # Detailed architectural blueprint & invariants
│   ├── security.md             # Threat model, isolation contract & boundaries
│   ├── evaluation.md           # Benchmark methodology and memory budget
│   └── demo.md                 # 4-part release demonstration script
├── localdev/
│   ├── __init__.py             # Version and package docstring
│   ├── cli.py                  # CLI argument parsing and command routing
│   ├── constants.py            # Frozen operational constants and exit codes
│   ├── errors.py               # Strongly typed exception hierarchy
│   ├── schemas.py              # Versioned Pydantic schemas (schema_version: "1.0")
│   ├── agent/                  # Orchestration, session lifecycle, permissions
│   ├── languages/              # Language adapter contract and Python adapter
│   ├── execution/              # Windows Job Objects, limits, runner, process tree
│   ├── inference/              # Ollama HTTP client, prompt budgets, validator
│   ├── patching/               # Schema-validated patching, diffs, ReplaceFileW
│   ├── profiling/              # Hot-process profiling, tracemalloc, RSS accounting
│   └── reporting/              # Sanitized terminal formatting and JSON envelope
└── tests/
    ├── conftest.py             # Pytest configuration and Windows markers
    ├── unit/                   # Unit test suite
    ├── integration/            # Multi-component integration tests
    └── windows/                # Native Windows Job Object & ReplaceFileW tests
```
