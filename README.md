# localdev — Native Windows Single-File Offline Python Coding Agent

> [!IMPORTANT]
> **Personal Portfolio / Resume Project Scope:**
> `localdev` is a personal portfolio project engineered to demonstrate systems-level Windows integration, deterministic static analysis, defensive subprocess containment, and reproducible local Small Language Model (SLM) orchestration. It is **not intended to be production-grade enterprise software** or multi-tenant infrastructure.
>
> **Supported Platform:** Supported exclusively on **Windows 11 x64 only**. All other operating systems and legacy Windows versions are intentionally unsupported.

---

## 1. Safety Boundaries, Trust Model, and Invariants

### 1.1 Trust Boundary & Non-Sandbox Disclaimer

> [!CAUTION]
> **`localdev` is NOT a security sandbox.**
> - **User-Owned / Trusted Code Only:** `localdev` is an offline development assistant designed exclusively for inspecting, executing, and modifying Python code that you own or trust.
> - **Subprocess Execution Identity:** Child subprocesses execute with the full operating system identity and access permissions of the invoking Windows user.
> - **Operational Limits vs Security Sandboxing:** Operational limits (Windows Job Objects with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, 10-second default execution timeouts, and 512 KB combined stdout/stderr pipe limits) exist to terminate accidental runaway tasks, infinite loops, and fork-bombs. **They do not protect against malicious or hostile code.** Untrusted Python scripts could read, modify, or delete accessible files, access local network interfaces, or spawn arbitrary Windows processes.

### 1.2 Strict Single Source Target Scope

- `localdev` operates on **exactly one explicit Python source target file** per command.
- **Single-Target, Not "Single-File at Runtime":** The target file is the sole subject of inspection, static analysis, diagnosis, and patching. However, runtime execution behaves like standard Python and is free to import third-party packages installed in the virtual environment or read local filesystem assets required by the target script.
- **No Automatic Discovery:** `localdev` never automatically crawls, traverses, parses, or mutates sibling files, parent directory contents, project manifests (`pyproject.toml`, `setup.cfg`), or `.git` repositories.
- **Secondary Inputs:** Ancillary inputs (e.g., `--input` JSON file for profiling or `--expected-stdout` strings for behavioral validation) supply execution arguments or assertion oracles and do not act as additional source targets.

### 1.3 Runtime and Isolation Contract

When executing target code (during `debug`, candidate patch validation, or `profile`), `localdev` enforces a deterministic isolation contract:

1. **Interpreter Flags (`python.exe -E -B -P`):**
   - `-E`: Ignores ambient `PYTHON*` environment variables (`PYTHONPATH`, `PYTHONHOME`, `PYTHONDEBUG`), preventing ambient host variable poisoning.
   - `-B`: Suppresses bytecode compilation (`PYTHONDONTWRITEBYTECODE=1`), leaving workspace directories clean of `.pyc` files and `__pycache__` folders.
   - `-P`: (`PYTHONSAFEPATH` in Python 3.11+): Prevents Python from automatically prepending the script's directory or the current working directory into `sys.path`.
2. **Why Isolated Mode (`-I`) is NOT Used:**
   Python's `-I` flag implicitly activates `-s` (disabling user site-packages) and isolates `sys.path` in ways that break virtual environments and prevent legitimate third-party packages installed in the active environment from loading. `localdev` achieves operational isolation via `-E -B -P` while allowing user virtualenv dependencies to function normally.
3. **Environment Variable Allowlist:**
   Subprocesses inherit only an explicit allowlist of essential operating system environment variables:
   `SYSTEMROOT`, `SYSTEMDRIVE`, `PATH`, `PATHEXT`, `TEMP`, `TMP`, `COMSPEC`, and `USERNAME`. All other ambient environment variables are stripped prior to subprocess launch.
4. **Working Directory (`cwd`):**
   Subprocesses execute with `cwd` set to the user's invocation working directory (where `localdev` was invoked), **NOT** the temporary session directory. This preserves natural relative path resolution (e.g., `open("data.csv")`) for user scripts.
5. **`__file__` Relocation Semantics & Limitations:**
   Target scripts execute from a temporary session copy located at `%TEMP%\localdev\session_<id>\session_target.py`. Consequently:
   - At runtime, `__file__` evaluates to the session copy path. Code that resolves sibling assets relative to `Path(__file__).parent` will search the temporary session directory rather than the original source folder. Assets must be referenced relative to `cwd`.
   - Tracebacks and terminal diagnostic reports normalize frame paths back to the user's canonical target path.

### 1.4 Guarded Atomic Replacement and Same-Volume Staging

File mutations are strictly guarded to prevent data loss, partial writes, or race conditions:

- **Same-Volume Staging Architecture:** Win32 `ReplaceFileW` kernel semantics require that the replacement candidate, the target file, and the backup file reside on the **same filesystem volume**. While session metadata lives in `%TEMP%` (typically drive `C:`), candidate staging and backup files are allocated on the target file's volume (e.g., `D:\.localdev_staging\...` and `<target>.bak`). Cross-volume replacement attempts fail closed.
- **Compare-Before-Replace SHA-256 Check:** Immediately before file replacement, `localdev` recalculates the target file's SHA-256 hash. If the hash differs from the baseline hash captured at command start (e.g., due to concurrent edits in an IDE), replacement immediately aborts with [`StaleEditError`](file:///d:/Project/Coding_Agent/localdev/errors.py#L41) to protect user edits.
- **Native Atomic Replacement (`ReplaceFileW`):** File replacement invokes Win32 `ReplaceFileW(lpReplacedFileName, lpReplacementFileName, lpBackupFileName, 0, NULL, NULL)`. This performs an atomic directory-entry swap under NTFS, guaranteeing zero partial writes. If requested, a `.bak` backup file is created atomically alongside the replacement. (Hardware power-loss flush durability across sudden power cuts is outside MVP scope).
- **Noninteractive `--apply` Safety Invariant:** The `--apply` flag authorizes write actions without an interactive confirmation prompt. **It never bypasses SHA-256 hash verification, Level A static validation gates, or reparse-point protections.** There is no `--force` flag.
- **Reparse-Point Rejection:** Symbolic links and NTFS junction points are detected and rejected as write targets to prevent symlink redirection attacks.

### 1.5 Terminal Display Sanitization vs Raw JSON Preservation

- **Terminal Output Sanitization:** All source code, tracebacks, subprocess output, and model text rendered to the console are stripped of ANSI escape sequences (`\x1b[...]`), Virtual Terminal (VT) command sequences (CSI, OSC), and non-printable control characters (`\x00`–`\x08`, `\x0b`–`\x1f`, `\x7f`), preserving `\n` and `\t`. This prevents terminal injection, cursor spoofing, or display corruption.
- **Raw JSON Preservation:** The machine-readable JSON envelope (`--json`, `schema_version: "1.0"`) preserves underlying stdout, stderr, and tracebacks verbatim per RFC 8259.

---

## 2. Empirical Validation Levels (Levels A–D)

When `localdev fix` generates a candidate patch, it evaluates the candidate against four empirical validation levels:

| Level | Name | Technical Criteria | Proof of Correctness? |
|---|---|---|---|
| **Level A** | **Static Validity** | Candidate parses cleanly into a valid AST (`ast.PyCF_ONLY_AST`) and introduces **zero new syntax errors or isolated Ruff linter diagnostics**. If the baseline target had a static diagnostic, that diagnostic is eliminated. (Runtime-only repairs pass Level A if the candidate remains statically valid without needing a pre-existing Ruff finding to remove). | **No** (static syntax and lint validity only). |
| **Level B** | **Failure Reproduction Removed** | Under identical execution parameters, the original runtime error signature `(exception_type, normalized_message, file, line)` is **no longer observed**. | **NO.** Explicitly does NOT prove correctness. Replacing an `IndexError` with a `TypeError` satisfies Level B but fails Level C. |
| **Level C** | **Clean Execution** | The candidate script executes under controlled runtime limits (`-E -B -P`) and exits cleanly with exit code `0`. | **Weak.** Proves absence of unhandled exceptions, but does not verify logical correctness. |
| **Level D** | **Behavioral Correctness** | Candidate satisfies explicit user-supplied behavioural assertions (`--expected-stdout`, `--expected-exit`). Cannot pass without an explicit user oracle. | **Strong.** Oracle-verified behavioral correctness against user assertions. |

---

## 3. Hot-Process Profiling & Separated Memory Accounting

`localdev profile` measures function and method execution under hot-process semantics while maintaining strict separation between distinct memory dimensions:

- **Hot-Process Semantics:** Target module import occurs once in an isolated disposable worker subprocess. The target function is executed across warm-up runs (default: 2) to eliminate JIT/caching transients, followed by measured runs (default: 7) where module state remains persistent.
- **Separated Timing Metrics:** Import latency (module load time) is tracked and reported separately from function invocation latency (median, min, max, mean, standard deviation).
- **Separated Memory Metrics:** Memory accounting never conflates distinct memory domains:
  1. **Python Heap Allocations (`tracemalloc`):** Pure Python object allocations allocated by CPython's allocator during the function call (peak and cumulative bytes).
  2. **Worker Process Tree RSS (`psutil`):** External resident set size (working set) of the worker subprocess and any spawned child processes, sampled externally at 20 ms intervals (50 Hz).
  3. **External Ollama Model Residency:** Memory occupied by the external Ollama daemon (~2.18 GB for 3B, ~1.15 GB for 1.5B). Inference models are explicitly unloaded before profiling begins to ensure maximum physical RAM headroom.
  4. **System Committed RAM:** Global system-wide memory commit state.

---

## 4. CLI Command Reference & Flags

`localdev` provides seven single-target inspection, analysis, and repair commands:

```text
usage: localdev [-h] [--json] [--keep-session] [--version] <command> ...
```

### Global Options

| Option | Description |
|---|---|
| `-h`, `--help` | Display command help and exit. |
| `--json` | Output structured RFC 8259 JSON envelope (`schema_version: "1.0"`) instead of human-readable text. |
| `--keep-session` | Retain temporary session directory after command completion for debugging. |
| `--version` | Display application version (`0.1.0`) and exit. |

### Commands

#### 1. `localdev info <target>`
Inspect single target file metadata without executing code.
- **Options:** Standard global options (`--json`, `--keep-session`).
- **Data Reported:** Absolute path, volume root, file size in bytes, line count, SHA-256 hash, PEP 263 source encoding, UTF-8/UTF-16 BOM presence, newline style (CRLF / LF), and read-only attribute.

#### 2. `localdev detect <target>`
Perform non-executing language detection via file extension, shebang line, and AST compilation.
- **Options:** Standard global options (`--json`, `--keep-session`).
- **Data Reported:** Language (`python` or `unsupported`), confidence tier (`CERTAIN`, `PROBABLE`, `UNSUPPORTED`), and detection reasons.

#### 3. `localdev analyse <target>`
Run deterministic static analysis: syntax validation, AST fact extraction, and isolated Ruff diagnostics.
- **Options:**
  - `--diagnose`: Request evidence-grounded bug diagnosis from local SLM alongside static analysis.
  - `--model <id>`: Override SLM model identifier for diagnosis.
  - `--fallback`: Use low-memory fallback SLM (`qwen2.5-coder:1.5b-instruct-q4_K_M`) for 8 GB RAM budgets.
- **Data Reported:** Syntax validity, syntax diagnostics, functions/classes/imports extracted, normalized Ruff diagnostics, and optional model diagnosis.

#### 4. `localdev debug <target>`
Execute target in controlled runtime (`-E -B -P`) and parse runtime tracebacks.
- **Options:**
  - `--diagnose`: Request evidence-grounded bug diagnosis from local SLM alongside runtime traceback.
  - `--model <id>`: Override SLM model identifier for diagnosis.
  - `--fallback`: Use low-memory fallback SLM (`qwen2.5-coder:1.5b-instruct-q4_K_M`).
  - `--stdin-file <path>`: Path to file supplying stdin for execution.
  - `--args ...`: Positional arguments to pass to the target script (or use `-- arg1 arg2`).
  - `--timeout <sec>`: Subprocess execution timeout in seconds (default: 10.0s).
  - `--fail-on-job-failure`: Abort execution if Windows Job Object creation or assignment fails.
- **Data Reported:** Exit code, stdout/stderr, execution duration, peak process RSS, error signature, normalized traceback frames, and optional model diagnosis.

#### 5. `localdev fix <target>`
Diagnose failure with local SLM, propose guarded structured patch, validate across Levels A–D, and atomically apply.
- **Options:**
  - `--apply`: Apply validated patch to disk without interactive confirmation prompt.
  - `--propose-only`: Display proposed patch and validation results without prompting to apply.
  - `--model <id>`: Override SLM model identifier.
  - `--fallback`: Use low-memory fallback SLM (`qwen2.5-coder:1.5b-instruct-q4_K_M`).
  - `--stdin-file <path>`: Stdin file for baseline execution and candidate validation.
  - `--args ...`: Target arguments for execution.
  - `--timeout <sec>`: Timeout for execution runs (default: 10.0s).
  - `--expected-stdout <str>`: Exact expected stdout string for Level D behavioral validation.
  - `--expected-stdout-contains <str>`: Substring expected in stdout for Level D behavioral validation.
  - `--expected-exit <code>`: Expected integer exit code for Level D behavioral validation.
  - `--fail-on-job-failure`: Abort execution if Windows Job Object fails.
- **Data Reported:** Diagnosis, structured line edits, unified diff, validation level outcomes (A–D), and patch application status.

#### 6. `localdev complexity <target>`
Perform static Big-O time, auxiliary-space, and output-space analysis grounded in CPython runtime semantics with mandatory abstention.
- **Options:**
  - `--selector <name>`: Target specific function or method (`function_name` or `ClassName.method_name`).
- **Data Reported:** Time complexity, auxiliary space, output space, confidence tier, and sound abstention reason if indeterminate.

#### 7. `localdev profile <target>`
Profile function import cost, execution latency, tracemalloc heap allocations, and process tree RSS in a disposable worker.
- **Options:**
  - `--selector <name>`: **Required.** Function or method selector string.
  - `--input <path>`: Path to JSON input file supplying function arguments `{"args": [...], "kwargs": {...}}`.
  - `--warmup <n>`: Warm-up invocation count (default: 2).
  - `--measured <n>`: Measured invocation count (default: 7).
  - `--timeout <sec>`: Worker execution timeout in seconds (default: 10.0s).
  - `--fail-on-job-failure`: Abort execution if Windows Job Object fails.
- **Data Reported:** Import duration, invocation latency statistics (min, max, median, mean, stddev), tracemalloc heap allocations, and peak process tree RSS.

---

## 5. Stable Exit Codes

Every CLI execution exits with a deterministic, documented exit code:

| Exit Code | Constant | Meaning | Typical Causes |
|---|---|---|---|
| **0** | `EXIT_SUCCESS` | Command completed successfully. | Target is valid, execution clean, patch successfully applied, or analysis completed without error. |
| **1** | `EXIT_TARGET_FAILURE` | Target code failed execution or static checks. | Syntax error, runtime exception, non-zero target exit, or failed candidate validation. |
| **2** | `EXIT_CLI_USAGE_ERROR` | Command-line usage or argument syntax error. | Missing required arguments, multiple target files supplied, or malformed selectors. |
| **3** | `EXIT_TARGET_IO_ERROR` | File I/O, permission, or target validation error. | Target file not found, permission denied, invalid PEP 263 encoding, or stale edit detected. |
| **4** | `EXIT_TIMEOUT_RESOURCE_BREACH` | Subprocess resource limit breached. | Execution wall-clock timeout exceeded (> 10s) or combined output byte cap breached (> 512 KB). |
| **5** | `EXIT_INFERENCE_ERROR` | Local inference service failure. | Ollama service not running (`127.0.0.1:11434`), model not pulled, or inference connection failed. |
| **6** | `EXIT_ABSTENTION` | Command soundly abstained. | Complexity analysis encountered dynamic loop bounds, or prompt budget exceeded during diagnosis. |

---

## 6. Versioned JSON Envelope Specification (`--json`)

When `--json` is supplied, `localdev` emits a standard machine-readable JSON envelope conforming to RFC 8259 with `schema_version: "1.0"`:

```json
{
  "schema_version": "1.0",
  "command": "debug",
  "success": false,
  "target_path": "D:\\Project\\Coding_Agent\\target.py",
  "data": {
    "exit_code": 1,
    "stdout": "",
    "stderr": "Traceback (most recent call last):\n  File \"D:\\Project\\Coding_Agent\\target.py\", line 5, in <module>\n    raise ValueError(\"invalid data\")\nValueError: invalid data\n",
    "duration_seconds": 0.082,
    "approximate_peak_process_tree_rss_bytes": 9437184,
    "timed_out": false,
    "error_signature": {
      "exception_type": "ValueError",
      "normalized_message": "invalid data",
      "fault_file": "D:\\Project\\Coding_Agent\\target.py",
      "fault_line": 5,
      "fault_function": "<module>"
    },
    "diagnosis": null,
    "inference_metadata": null
  },
  "errors": [
    "ValueError: invalid data"
  ],
  "limitations": [],
  "metadata": {
    "python_version": "3.12.10",
    "platform": "win32",
    "execution_time_utc": "2026-09-28T10:00:00Z"
  }
}
```

---

## 7. Installation & Getting Started (From Scratch)

> [!NOTE]
> **Fresh Windows 11 Clone Assumptions:**
> This guide assumes you have just cloned the repository on a fresh Windows 11 x64 machine without existing development toolchains, virtual environments, or AI model daemons pre-configured.

### 7.1 System Requirements

- **Operating System:** Windows 11 x64 (Build 22621+ recommended).
- **Architecture:** `x86_64` (ARM64 Windows is outside MVP scope).
- **RAM:**
  - **8 GB RAM minimum:** Supports the lightweight 1.5B fallback model (`qwen2.5-coder:1.5b-instruct-q4_K_M` or `localdev-qwen-coder:1.5b`) with ~5.8 GB total system memory commit.
  - **16 GB RAM recommended:** Allows running the primary 3B model (`qwen2.5-coder:3b-instruct-q4_K_M` or `localdev-qwen-coder:3b`) alongside IDEs and developer workflows.
- **Disk Space:** ~10 GB free space (Python virtualenv, dev dependencies, and quantized SLM weights).

---

### 7.2 Toolchain Installation (Prerequisites)

If you do not yet have Python, Git, or Ollama installed, you can install all three quickly using the native Windows Package Manager (`winget`):

```powershell
# 1. Install standard 64-bit Python 3.12 (with PATH automatically enabled)
winget install Python.Python.3.12 --override "/passive PrependPath=1"

# 2. Install Git for Windows
winget install Git.Git

# 3. Install Ollama for Windows (runs as a Windows background tray application)
winget install Ollama.Ollama
```

> [!TIP]
> **Manual Download Links:**
> - **Python 3.12 or 3.11 (64-bit):** [python.org/downloads](https://www.python.org/downloads/) *(Ensure "Add python.exe to PATH" is checked during installation)*
> - **Git for Windows:** [git-scm.com](https://git-scm.com/)
> - **Ollama for Windows:** [ollama.com/download](https://ollama.com/download)

---

### 7.3 Step-by-Step Repository Setup

#### Step 1: Configure PowerShell Script Execution Policy
By default, Windows 11 blocks script execution under the `Restricted` policy, which prevents virtual environment activation scripts (`Activate.ps1`) from running. Open a PowerShell terminal and permit script execution for your session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

#### Step 2: Clone and Navigate to Repository
```powershell
git clone https://github.com/example/Coding_Agent.git d:\Project\Coding_Agent
cd d:\Project\Coding_Agent
```

#### Step 3: Create and Activate Virtual Environment
Use Python 3.12 (or 3.11):

```powershell
# Create isolated virtual environment
py -3.12 -m venv .venv

# If 'py' launcher is unavailable, invoke python directly:
# python -m venv .venv

# Activate the virtual environment
.\.venv\Scripts\Activate.ps1
```
*(Your command prompt will display `(.venv)` once activated).*

#### Step 4: Install Dependencies & Editable Package
Upgrade packaging tools and install `localdev` with all development, test, and evaluation dependencies:

```powershell
# Upgrade pip, wheel, and setuptools
python -m pip install --upgrade pip setuptools wheel

# Install localdev in editable mode with development tools
pip install -e ".[dev]"
```

This installs:
- **Core Runtime:** [`pydantic`](https://docs.pydantic.dev/) (schemas), [`psutil`](https://psutil.readthedocs.io/) (process tree RSS accounting), [`ruff`](https://docs.astral.sh/ruff/) (isolated AST linting), [`httpx`](https://www.python-httpx.org/) (Ollama client).
- **Development & Testing:** `pytest`, `pytest-mock`, `mypy`.
- **Evaluation & SLM Tooling:** `torch`, `transformers`, `peft`, `datasets`.

---

### 7.4 Ollama Service Configuration & Model Registration

`localdev` operates entirely offline using local Small Language Models (SLMs) served via Ollama over `http://127.0.0.1:11434`.

#### Step 1: Ensure Ollama Daemon is Running
The Ollama installer automatically runs an icon in your Windows system tray. If the daemon is not running, launch it from the Windows Start menu or run in a separate terminal:

```powershell
ollama serve
```

#### Step 2 (Optional): Configure Custom Model Storage Location
By default, Ollama stores model weights in `C:\Users\<username>\.ollama\models`. If your primary drive `C:` has limited capacity and you wish to store models on another drive (e.g., `D:\OllamaModels`), set the `OLLAMA_MODELS` environment variable:

```powershell
# Set persistently for your Windows user profile:
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\OllamaModels", "User")

# Set for your active PowerShell session:
$env:OLLAMA_MODELS = "D:\OllamaModels"
```
> [!IMPORTANT]
> If Ollama is already running in your system tray, right-click the Ollama tray icon, select **Quit Ollama**, and restart it for the new model storage path to take effect.

Verify the local daemon is reachable:
```powershell
curl.exe http://127.0.0.1:11434/api/tags
```

#### Step 3: Pull Base Quantized Models
Download the official quantized base models from the Ollama library:

```powershell
# Primary 3B base model (~1.9 GB download)
ollama pull qwen2.5-coder:3b-instruct-q4_K_M

# Fallback 1.5B base model (~986 MB download, for 8 GB RAM budgets)
ollama pull qwen2.5-coder:1.5b-instruct-q4_K_M
```

#### Step 4: Register Domain-Specific Fine-Tuned Models
The repository includes pre-configured Modelfiles in [`models/finetune/`](file:///d:/Project/Coding_Agent/models/finetune/) with domain-specific system prompts, repair schemas, and deterministic sampling parameters. Register them in your local Ollama daemon:

```powershell
# Register the primary 3B domain SLM (takes < 1s, reuses cached base layers)
ollama create localdev-qwen-coder:3b -f models/finetune/Modelfile

# Register the fallback 1.5B domain SLM
ollama create localdev-qwen-coder:1.5b -f models/finetune/Modelfile.1.5b
```

#### Step 5: Verify Model Registration
List all registered models:

```powershell
ollama list
```

Expected output:
```text
NAME                                  ID              SIZE      MODIFIED
localdev-qwen-coder:3b                ...             1.9 GB    ...
localdev-qwen-coder:1.5b              ...             986 MB    ...
qwen2.5-coder:3b-instruct-q4_K_M      ...             1.9 GB    ...
qwen2.5-coder:1.5b-instruct-q4_K_M    ...             986 MB    ...
```

---

### 7.5 Verification & Smoke Testing

Confirm your setup by running the following health checks:

#### 1. Verify CLI Interface
```powershell
localdev --version
localdev --help
```

#### 2. Test Single-Target Inspection & Analysis
Run deterministic static analysis against an included sample without modifying files:
```powershell
# Inspect file metadata (SHA-256, encoding, newlines, line count)
localdev info tests/bug_samples/initial/01_index_error.py

# Run deterministic AST and Ruff static lint analysis
localdev analyse tests/bug_samples/initial/01_index_error.py
```

#### 3. Test Runtime Execution & SLM Diagnosis
Execute the target script under controlled limits (`-E -B -P`) and obtain an evidence-grounded diagnosis:
```powershell
localdev debug tests/bug_samples/initial/01_index_error.py --diagnose
```

#### 4. Test Automated Repair Proposal & Validation
Generate a structured patch and validate it against Level A–D criteria without altering the disk:
```powershell
localdev fix tests/bug_samples/initial/01_index_error.py --propose-only
```

#### 5. Run Automated Test Suite
```powershell
# Run the fast unit test suite (616 tests)
python -m pytest tests/unit/ -q

# Run native Windows API tests (Windows Job Objects, ReplaceFileW, staging)
python -m pytest -m windows -q
```

#### 6. Run Benchmark Evaluation Harness
```powershell
# Fast benchmark run across evaluation bug samples
python tools/evaluate.py --fast

# Benchmark using the registered 3B domain model
python tools/evaluate.py --model localdev-qwen-coder:3b --fast
```

---

## 8. Repository Layout

```text
localdev/
├── pyproject.toml              # PEP 518/621 package metadata and pinned dependencies
├── README.md                   # Complete user guide, safety boundaries, and CLI reference
├── plan.md                     # Comprehensive phase-by-phase implementation plan
├── results.md                  # Benchmark results, 703 test statistics & model scorecards
├── LICENSE                     # MIT License
├── docs/
│   ├── architecture.md         # Detailed architectural blueprint & invariants
│   ├── security.md             # Threat model, isolation contract & boundaries
│   ├── evaluation.md           # Benchmark methodology, datasets & 8 GB budget report
│   ├── finetuning.md           # LoRA fine-tuning pipeline, schemas & Ollama packaging
│   └── demo.md                 # 4-part release demonstration script & narrative
├── models/
│   └── finetune/               # Domain-specific Modelfiles and LoRA adapters
│       ├── Modelfile           # Ollama Modelfile for localdev-qwen-coder:3b
│       └── Modelfile.1.5b      # Ollama Modelfile for localdev-qwen-coder:1.5b
├── tools/
│   ├── evaluate.py             # Empirical benchmark & evaluation harness
│   ├── build_manifests.py      # Automated fixture manifest generator
│   └── finetune/               # Dataset preparation, training & GGUF export scripts
├── datasets/                   # Synthetic and curated fine-tuning training pairs
├── localdev/
│   ├── __init__.py             # Package version and docstrings
│   ├── cli.py                  # CLI argument parsing and command routing
│   ├── config.py               # LocaldevConfig dataclass and environment defaults
│   ├── constants.py            # Frozen operational parameters, limits, and exit codes
│   ├── errors.py               # Strongly typed exception hierarchy
│   ├── schemas.py              # Versioned Pydantic schemas (schema_version: "1.0")
│   ├── agent/                  # Orchestration, context builder, session lifecycle, permissions
│   ├── languages/              # Language adapter contract, Python AST analyser, complexity
│   ├── execution/              # Windows Job Objects, limits, runner, process tree, pipe draining
│   ├── inference/              # Ollama HTTP client, prompt budgets, validator, retry logic
│   ├── patching/               # Line-oriented edit schema, candidate staging, ReplaceFileW
│   ├── profiling/              # Hot-process loader, tracemalloc, external RSS sampling
│   └── reporting/              # Sanitized terminal formatting and JSON envelope
└── tests/
    ├── conftest.py             # Pytest configuration and Windows markers
    ├── unit/                   # Unit test suite (CLI parsing, context, AST, schemas, diffs)
    ├── integration/            # Multi-component integration tests (flows, 8 GB budget)
    ├── windows/                # Native Windows Job Object & ReplaceFileW tests
    ├── bug_samples/            # 72 versioned bug fixtures with manifest.json
    ├── complexity_samples/     # 42 algorithmic complexity fixtures with manifest.json
    ├── profiling_samples/      # 17 profiling benchmark fixtures with inputs
    └── boundary_samples/       # Path, Unicode, symlink, and resource boundary fixtures
```

---

## 9. Documentation Index

For in-depth technical documentation, refer to the accompanying guides:

- **Architectural Specification:** [`docs/architecture.md`](file:///d:/Project/Coding_Agent/docs/architecture.md) — Subprocess containment, Windows Job Objects, `ReplaceFileW` atomic staging, and memory accounting.
- **Security & Threat Model:** [`docs/security.md`](file:///d:/Project/Coding_Agent/docs/security.md) — Non-sandbox trust boundary, execution flags (`-E -B -P`), prompt injection defenses, and safe path resolution.
- **Evaluation & Benchmarks:** [`docs/evaluation.md`](file:///d:/Project/Coding_Agent/docs/evaluation.md) — Empirical methodology, 8 GB RAM budget compliance, and regression datasets.
- **Model Fine-Tuning Pipeline:** [`docs/finetuning.md`](file:///d:/Project/Coding_Agent/docs/finetuning.md) — QLoRA adapter training, dataset schema validation, GGUF export, and Ollama packaging.
- **Empirical Scorecards & Statistics:** [`results.md`](file:///d:/Project/Coding_Agent/results.md) — Head-to-head scorecards across 703 test samples comparing base vs fine-tuned 3B and 1.5B tiers.
- **Release Walkthrough & Demo:** [`docs/demo.md`](file:///d:/Project/Coding_Agent/docs/demo.md) — 4-part release script showcasing real-world debugging, repair, complexity analysis, and hot-process profiling.

