"""Utility script to build and validate dataset manifests for localdev evaluation."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_bug_manifest() -> None:
    base = ROOT / "tests" / "bug_samples"
    manifest: list[dict[str, str | None]] = []

    # Initial SLM bugs
    initial_map = {
        "01_off_by_one.py": ("IndexError", "Off by one list indexing error"),
        "02_zero_division.py": ("ZeroDivisionError", "Division by zero in percentage calculation"),
        "03_type_mismatch.py": ("TypeError", "String and integer concatenation type error"),
        "04_key_error.py": ("KeyError", "Missing key in dictionary lookup"),
        "05_attribute_error.py": ("AttributeError", "Calling strip() on None object"),
        "06_unbound_local.py": ("UnboundLocalError", "Referencing local before assignment in function scope"),
        "07_recursion_error.py": ("RecursionError", "Infinite recursive call exceeding maximum depth"),
        "08_empty_sequence.py": ("IndexError", "Accessing first element of empty sequence"),
        "09_name_error.py": ("NameError", "Accessing undefined variable in calculation"),
        "10_value_error.py": ("ValueError", "Parsing invalid numeric string literal with int()"),
    }
    for f, (err, desc) in sorted(initial_map.items()):
        p = base / "initial" / f
        assert p.is_file(), f"Missing file: {p}"
        manifest.append({
            "id": f"bug_initial_{f[:2]}",
            "path": f"initial/{f}",
            "category": "initial_slm",
            "expected_status": "runtime_exception",
            "error_type": err,
            "description": desc,
        })

    # Runtime limit faults
    runtime_map = {
        "blocking_stdin.py": ("BlockingIOError", "Attempting blocking sys.stdin read"),
        "clean_exit.py": (None, "Clean runtime process exit with code 0"),
        "infinite_loop.py": ("TimeoutError", "Infinite loop breaching timeout limits"),
        "mixed_output.py": (None, "Emitting mixed stdout and stderr streams"),
        "nonzero_exit.py": ("SystemExit", "Explicit sys.exit(42) with non-zero exit code"),
        "stderr_flood.py": ("OutputOverflow", "Emitting high-volume stderr stream exceeding buffer thresholds"),
        "stdout_flood.py": ("OutputOverflow", "Emitting high-volume stdout stream exceeding buffer thresholds"),
    }
    for f, (runtime_err, runtime_desc) in sorted(runtime_map.items()):
        p = base / "runtime" / f
        assert p.is_file(), f"Missing file: {p}"
        if runtime_err == "OutputOverflow":
            status = "output_overflow"
        elif runtime_err == "TimeoutError":
            status = "timeout"
        elif runtime_err is None:
            status = "clean_success"
        else:
            status = "runtime_exception"
        manifest.append({
            "id": f"bug_runtime_{f.removesuffix('.py')}",
            "path": f"runtime/{f}",
            "category": "runtime_fault",
            "expected_status": status,
            "error_type": runtime_err,
            "description": runtime_desc,
        })

    # Syntax samples
    syntax_map = {
        "crlf_newlines.py.sample": (None, "Valid syntax with CRLF newlines"),
        "indent_unexpected.py.sample": ("IndentationError", "Unexpected indentation level"),
        "indent_unindent.py.sample": ("IndentationError", "Unindent does not match any outer indentation level"),
        "pep263_latin1.py.sample": (None, "Valid syntax with Latin-1 PEP 263 encoding"),
        "pep263_unknown.py.sample": ("SyntaxError", "Unknown PEP 263 source encoding declaration"),
        "side_effects_dangerous.py.sample": ("RuntimeError", "File with dangerous top-level runtime side effects"),
        "syntax_invalid_token.py.sample": ("SyntaxError", "Invalid token in binary expression"),
        "syntax_missing_colon.py.sample": ("SyntaxError", "Missing colon after function signature"),
        "syntax_unclosed_paren.py.sample": ("SyntaxError", "Unclosed opening parenthesis"),
        "utf8_bom.py.sample": (None, "Valid syntax with UTF-8 BOM signature"),
        "valid_syntax.py.sample": (None, "Clean valid Python syntax AST fixture"),
    }
    for f, (syntax_err, desc) in sorted(syntax_map.items()):
        p = base / "syntax" / f
        assert p.is_file(), f"Missing file: {p}"
        manifest.append({
            "id": f"bug_syntax_{f.split('.')[0]}",
            "path": f"syntax/{f}",
            "category": "syntax_error",
            "expected_status": "syntax_error" if syntax_err in ("SyntaxError", "IndentationError") else ("runtime_exception" if syntax_err else "clean_success"),
            "error_type": syntax_err,
            "description": desc,
        })

    # Standard exceptions
    std_map = {
        "attribute_error_none.py": ("AttributeError", "AttributeError on NoneType method call"),
        "file_not_found_read.py": ("FileNotFoundError", "FileNotFoundError opening nonexistent file"),
        "index_error_empty_pop.py": ("IndexError", "IndexError popping from empty list"),
        "key_error_nested_dict.py": ("KeyError", "KeyError accessing nonexistent nested key"),
        "name_error_typo.py": ("NameError", "NameError due to misspelled variable name"),
        "type_error_not_callable.py": ("TypeError", "TypeError calling non-callable integer"),
        "type_error_unsubscriptable.py": ("TypeError", "TypeError subscripting NoneType object"),
        "type_error_unsupported_operand.py": ("TypeError", "TypeError adding int and str"),
        "unbound_local_reassign.py": ("UnboundLocalError", "UnboundLocalError reassigning global without declaration"),
        "value_error_int_literal.py": ("ValueError", "ValueError parsing non-numeric string as int"),
        "value_error_unpack_mismatch.py": ("ValueError", "ValueError unpacking sequence of wrong length"),
        "zero_division_modulo.py": ("ZeroDivisionError", "ZeroDivisionError calculating modulo zero"),
    }
    for f, (err, desc) in sorted(std_map.items()):
        p = base / "standard_exceptions" / f
        assert p.is_file(), f"Missing file: {p}"
        manifest.append({
            "id": f"bug_std_{f.removesuffix('.py')}",
            "path": f"standard_exceptions/{f}",
            "category": "standard_exception",
            "expected_status": "runtime_exception",
            "error_type": err,
            "description": desc,
        })

    # Logic bugs
    logic_map = {
        "accidental_tuple.py": ("AssertionError", "Trailing comma converts scalar to single-element tuple"),
        "early_return_in_loop.py": ("AssertionError", "Return inside loop body prematurely terminates iteration"),
        "float_equality.py": ("AssertionError", "Exact floating-point equality check fails IEEE-754 representation"),
        "incorrect_accumulator.py": ("AssertionError", "Multiplication accumulator initialized to 0 instead of 1"),
        "inverted_boolean.py": ("AssertionError", "Inverted boolean logical operator in authorization check"),
        "mutable_default_arg.py": ("AssertionError", "Mutable default argument accumulates state across calls"),
        "off_by_one_range.py": ("AssertionError", "Range bounds off-by-one skipping last element in sequence"),
        "reversed_sort_flag.py": ("AssertionError", "Sort flag reverse=False when descending order intended"),
        "shadowing_builtin.py": ("AssertionError", "Variable name shadows builtin function name"),
        "shallow_copy_mutation.py": ("AssertionError", "Shallow copy mutates nested lists in original data"),
        "string_immutable_replace.py": ("AssertionError", "String replace return value discarded"),
        "wrong_comparison_operator.py": ("AssertionError", "Object identity operator is used instead of equality =="),
    }
    for f, (err, desc) in sorted(logic_map.items()):
        p = base / "logic" / f
        assert p.is_file(), f"Missing file: {p}"
        manifest.append({
            "id": f"bug_logic_{f.removesuffix('.py')}",
            "path": f"logic/{f}",
            "category": "logic_bug",
            "expected_status": "assertion_failure",
            "error_type": err,
            "description": desc,
        })

    # Clean programs
    clean_map = {
        "binary_search_clean.py": "Iterative binary search algorithm returning element index",
        "caesar_cipher_clean.py": "Caesar cipher shift encryption and symmetric decryption",
        "csv_row_clean.py": "CSV header and row tokenizer producing structured dictionaries",
        "fibonacci_clean.py": "Iterative Fibonacci number calculation",
        "json_parser_clean.py": "JSON record serialization, deserialization, and schema validation",
        "lru_cache_clean.py": "Generic Least Recently Used (LRU) bounded cache",
        "matrix_transpose_clean.py": "2D list matrix transpose transformation",
        "prime_sieve_clean.py": "Sieve of Eratosthenes prime number generator",
        "quicksort_clean.py": "Deterministic quicksort list ordering",
        "stack_data_structure_clean.py": "Object-oriented generic Stack data structure",
        "stats_clean.py": "Mean, count, and standard deviation summary calculations",
        "string_palindrome_clean.py": "Alphanumeric palindrome validator with case normalization",
    }
    for f, desc in sorted(clean_map.items()):
        p = base / "clean" / f
        assert p.is_file(), f"Missing file: {p}"
        manifest.append({
            "id": f"clean_{f.removesuffix('.py')}",
            "path": f"clean/{f}",
            "category": "clean_program",
            "expected_status": "clean_success",
            "error_type": None,
            "description": desc,
        })

    # External dependencies
    ext_map = {
        "broken_helper_runtime.py": ("RuntimeError", "Helper module raising RuntimeError on import"),
        "circular_import_a.py": ("ImportError", "Circular module import dependency cycle A"),
        "circular_import_b.py": ("ImportError", "Circular module import dependency cycle B"),
        "import_runtime_error.py": ("RuntimeError", "Importing module with top-level initialization failure"),
        "missing_attribute.py": ("AttributeError", "Accessing nonexistent attribute on standard library module"),
        "missing_from_import.py": ("ImportError", "Importing nonexistent symbol from existing module"),
        "missing_module.py": ("ModuleNotFoundError", "Importing nonexistent external third-party module"),
        "relative_import_error.py": ("ImportError", "Attempting relative import beyond top-level package"),
    }
    for f, (err, desc) in sorted(ext_map.items()):
        p = base / "external_dependencies" / f
        assert p.is_file(), f"Missing file: {p}"
        manifest.append({
            "id": f"bug_ext_{f.removesuffix('.py')}",
            "path": f"external_dependencies/{f}",
            "category": "external_dependency",
            "expected_status": "import_error",
            "error_type": err,
            "description": desc,
        })

    out_file = base / "manifest.json"
    doc = {
        "dataset": "bug_samples",
        "version": "1.0.0",
        "total_samples": len(manifest),
        "samples": manifest,
    }
    out_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Generated {out_file} with {len(manifest)} verified samples.")


def build_complexity_manifest() -> None:
    base = ROOT / "tests" / "complexity_samples"
    manifest: list[dict[str, str | bool | None]] = []

    # Iterative loops
    loop_samples = [
        ("constant_operations", "O(1)", "O(1)", "O(1)", False, None, "Constant primitive arithmetic operations"),
        ("constant_range_loop", "O(1)", "O(1)", "O(1)", False, None, "Fixed constant bound loop (range(10))"),
        ("single_linear_loop", "O(n)", "O(1)", "O(1)", False, None, "Single linear iteration over sequence"),
        ("sequential_loops", "O(n)", "O(1)", "O(1)", False, None, "Sequential linear loops (O(n) + O(n) = O(n))"),
        ("append_loop", "O(n)", "O(1)", "O(n)", False, None, "Linear loop appending to output list"),
        ("builtin_sorted", "O(n log n)", "O(n)", "O(n)", False, None, "Builtin sorted() function call"),
        ("in_place_sort", "O(n log n)", "O(1)", "O(1)", False, None, "In-place list sort() method call"),
        ("nested_same_dimension", "O(n^2)", "O(1)", "O(1)", False, None, "Nested loops over identical dimension"),
        ("nested_distinct_dimensions", "O(nm)", "O(1)", "O(1)", False, None, "Nested loops over distinct dimensions"),
        ("triple_nested_loop", "O(n^3)", "O(1)", "O(1)", False, None, "Triple nested loop"),
        ("four_nested_loops", None, None, None, True, "OUT_OF_VOCABULARY", "Quadruple nested loop exceeding supported classes"),
    ]
    for sel, t, aux, out, abs_flag, abs_cat, desc in loop_samples:
        manifest.append({
            "id": f"comp_loop_{sel}",
            "path": "iterative/loops.py",
            "selector": sel,
            "target": f"iterative/loops.py::{sel}",
            "expected_time": t,
            "expected_aux_space": aux,
            "expected_output_space": out,
            "should_abstain": abs_flag,
            "abstention_category": abs_cat,
            "description": desc,
        })

    # Membership
    membership_samples = [
        ("membership_in_set", "O(n)", "O(1)", "O(1)", False, None, "Hash table set membership test inside loop"),
        ("membership_in_local_set", "O(n)", "O(n)", "O(1)", False, None, "Local set construction followed by membership test"),
        ("membership_in_list", "O(nm)", "O(1)", "O(1)", False, None, "Linear scan sequence membership test inside loop"),
        ("membership_in_local_list", "O(n^2)", "O(n)", "O(1)", False, None, "Local list allocation and linear membership test"),
        ("membership_in_dict", "O(n)", "O(1)", "O(1)", False, None, "Dictionary key hash lookup inside loop"),
    ]
    for sel, t, aux, out, abs_flag, abs_cat, desc in membership_samples:
        manifest.append({
            "id": f"comp_member_{sel}",
            "path": "iterative/membership.py",
            "selector": sel,
            "target": f"iterative/membership.py::{sel}",
            "expected_time": t,
            "expected_aux_space": aux,
            "expected_output_space": out,
            "should_abstain": abs_flag,
            "abstention_category": abs_cat,
            "description": desc,
        })

    # Space allocation
    space_samples = [
        ("returned_list_comprehension", "O(n)", "O(1)", "O(n)", False, None, "Returned list comprehension in output space"),
        ("temporary_list_comprehension_consumed", "O(n)", "O(n)", "O(1)", False, None, "Intermediate list comprehension consumed internally"),
        ("generator_expression_consumed", "O(n)", "O(1)", "O(1)", False, None, "Intermediate generator expression consumed lazily"),
        ("returned_generator_expression", "O(1)", "O(1)", "O(1)", False, None, "Returned lazy generator expression iterator"),
        ("generator_function_yield", "O(n)", "O(1)", "O(1)", False, None, "Generator function yielding elements lazily"),
        ("temporary_slice", "O(n)", "O(n)", "O(1)", False, None, "Temporary slice allocated in auxiliary space"),
        ("returned_slice", "O(n)", "O(1)", "O(n)", False, None, "Returned slice allocated in output space"),
    ]
    for sel, t, aux, out, abs_flag, abs_cat, desc in space_samples:
        manifest.append({
            "id": f"comp_space_{sel}",
            "path": "iterative/space_allocation.py",
            "selector": sel,
            "target": f"iterative/space_allocation.py::{sel}",
            "expected_time": t,
            "expected_aux_space": aux,
            "expected_output_space": out,
            "should_abstain": abs_flag,
            "abstention_category": abs_cat,
            "description": desc,
        })

    # Recursive patterns
    recursive_samples = [
        ("factorial", "O(n)", "O(n)", "O(1)", False, None, "Linear recursion with single decrement"),
        ("linear_traversal", "O(n)", "O(n)", "O(1)", False, None, "Linear recursion slicing sequence"),
        ("halving_rec", "O(log n)", "O(log n)", "O(1)", False, None, "Divide-and-conquer logarithmic halving recursion"),
        ("binary_search_rec", "O(log n)", "O(log n)", "O(1)", False, None, "Recursive binary search halving intervals"),
        ("fibonacci_naive", "O(2^n)", "O(n)", "O(1)", False, None, "Binary branching exponential recursion"),
        ("missing_base_case", None, None, None, True, "DYNAMIC_RECURSION", "Recursive function lacking static base case"),
        ("mutual_even", None, None, None, True, "DYNAMIC_RECURSION", "Mutually recursive function pair even"),
        ("mutual_odd", None, None, None, True, "DYNAMIC_RECURSION", "Mutually recursive function pair odd"),
        ("dynamic_collatz", None, None, None, True, "DYNAMIC_RECURSION", "Dynamic step recursion (Collatz sequence)"),
        ("loop_recursion", None, None, None, True, "DYNAMIC_RECURSION", "Dynamic branching loop recursion"),
    ]
    for sel, t, aux, out, abs_flag, abs_cat, desc in recursive_samples:
        manifest.append({
            "id": f"comp_rec_{sel}",
            "path": "recursive/patterns.py",
            "selector": sel,
            "target": f"recursive/patterns.py::{sel}",
            "expected_time": t,
            "expected_aux_space": aux,
            "expected_output_space": out,
            "should_abstain": abs_flag,
            "abstention_category": abs_cat,
            "description": desc,
        })

    # More patterns
    more_samples = [
        ("while_linear_decrement", None, None, None, True, "DYNAMIC_BOUNDS", "While loop with linear integer decrement abstains soundly"),
        ("while_log_halving", None, None, None, True, "DYNAMIC_BOUNDS", "While loop with logarithmic division halving abstains soundly"),
        ("dict_comprehension_materialized", "O(n)", "O(1)", "O(n)", False, None, "Dictionary comprehension materializing output mapping"),
        ("set_comprehension_materialized", "O(n)", "O(1)", "O(n)", False, None, "Set comprehension materializing output set"),
        ("matrix_multiplication", "O(n^3)", "O(1)", "O(n^2)", False, None, "Matrix multiplication triple loop"),
        ("dynamic_break_while", None, None, None, True, "DYNAMIC_BOUNDS", "While loop with dynamic arithmetic break"),
    ]
    for sel, t, aux, out, abs_flag, abs_cat, desc in more_samples:
        manifest.append({
            "id": f"comp_more_{sel}",
            "path": "iterative/more_patterns.py",
            "selector": sel,
            "target": f"iterative/more_patterns.py::{sel}",
            "expected_time": t,
            "expected_aux_space": aux,
            "expected_output_space": out,
            "should_abstain": abs_flag,
            "abstention_category": abs_cat,
            "description": desc,
        })

    # Ast functions and classes
    ast_samples = [
        ("simple_utility", "O(1)", "O(1)", "O(1)", False, None, "Simple utility with constant return value"),
        ("Calculator.add", "O(1)", "O(1)", "O(1)", False, None, "Calculator method performing constant field addition"),
        ("Calculator.is_positive", "O(1)", "O(1)", "O(1)", False, None, "Static method evaluating constant comparison"),
    ]
    for sel, t, aux, out, abs_flag, abs_cat, desc in ast_samples:
        manifest.append({
            "id": f"comp_ast_{sel.replace('.', '_')}",
            "path": "ast/functions_and_classes.py",
            "selector": sel,
            "target": f"ast/functions_and_classes.py::{sel}",
            "expected_time": t,
            "expected_aux_space": aux,
            "expected_output_space": out,
            "should_abstain": abs_flag,
            "abstention_category": abs_cat,
            "description": desc,
        })

    out_file = base / "manifest.json"
    doc = {
        "dataset": "complexity_samples",
        "version": "1.0.0",
        "total_samples": len(manifest),
        "samples": manifest,
    }
    out_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Generated {out_file} with {len(manifest)} verified complexity functions.")


def build_profiling_manifest() -> None:
    base = ROOT / "tests" / "profiling_samples"
    manifest: list[dict[str, str | int | bool | None]] = []

    profiling_benchmarks = [
        ("timing_samples.py", "sleep_fifteen_ms", "empty.json", "pure_timing", True, 2, 5, "Controlled monotonic sleep benchmark"),
        ("timing_samples.py", "compute_squares", "limit_input.json", "pure_timing", True, 2, 5, "List comprehension mathematical square calculation"),
        ("timing_samples.py", "quick_add", "two_args.json", "pure_timing", True, 2, 5, "Primitive addition fast path invocation"),
        ("stateful_samples.py", "record_history", "text_input.json", "stateful", True, 2, 5, "Persistent global list mutation under hot-process"),
        ("stateful_samples.py", "get_history_length", "empty.json", "stateful", True, 2, 5, "Read persisted global list length"),
        ("stateful_samples.py", "cached_heavy_computation", "two_args.json", "stateful", True, 2, 5, "functools.lru_cache hot-process persistence"),
        ("memory_samples.py", "allocate_two_mb", "empty.json", "memory_intensive", True, 2, 3, "2 MB bytearray tracemalloc heap allocation"),
        ("memory_samples.py", "allocate_five_mb", "empty.json", "memory_intensive", True, 2, 3, "5 MB bytearray tracemalloc heap allocation"),
        ("memory_samples.py", "minimal_allocation", "empty.json", "minimal_memory", True, 2, 5, "Near-zero heap allocation baseline"),
        ("mutating_samples.py", "in_place_sort", "sort_input.json", "mutating", True, 2, 5, "In-place list sort with fresh argument regeneration"),
        ("mutating_samples.py", "pop_all", "sort_input.json", "mutating", True, 2, 5, "Destructive list pop with fresh argument regeneration"),
        ("mutating_samples.py", "mutate_dict", "dict_input.json", "mutating", True, 2, 5, "Destructive dictionary in-place mutation"),
        ("pure_math_samples.py", "hash_string", "text_input.json", "pure_computation", True, 2, 5, "Deterministic SHA-256 cryptographic string hashing"),
        ("pure_math_samples.py", "sum_primes", "limit_input.json", "pure_computation", True, 2, 5, "Deterministic prime sieve accumulation"),
        ("slow_import_samples.py", "fast_add", "two_args.json", "slow_import", True, 2, 5, "Fast function execution with high import duration"),
        ("failing_samples.py", "raise_zero_division", "empty.json", "failing_target", False, 1, 1, "Function raising ZeroDivisionError during execution"),
        ("failing_samples.py", "raise_value_error", "empty.json", "failing_target", False, 1, 1, "Function raising ValueError during execution"),
    ]

    for target_file, sel, input_f, cat, should_succ, warmup, measured, desc in profiling_benchmarks:
        target_path = base / "targets" / target_file
        assert target_path.is_file(), f"Missing target file: {target_path}"
        input_path = base / "inputs" / input_f
        assert input_path.is_file(), f"Missing input file: {input_path}"

        manifest.append({
            "id": f"prof_{target_file.removesuffix('.py')}_{sel}",
            "target_file": f"targets/{target_file}",
            "selector": sel,
            "target": f"targets/{target_file}::{sel}",
            "input_file": f"inputs/{input_f}",
            "category": cat,
            "expected_success": should_succ,
            "warmup_runs": warmup,
            "measured_runs": measured,
            "description": desc,
        })

    out_file = base / "manifest.json"
    doc = {
        "dataset": "profiling_samples",
        "version": "1.0.0",
        "total_samples": len(manifest),
        "samples": manifest,
    }
    out_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Generated {out_file} with {len(manifest)} verified profiling benchmarks.")


def build_boundary_manifest() -> None:
    base = ROOT / "tests" / "boundary_samples"
    manifest: list[dict[str, str | bool | None]] = []

    boundary_items = [
        ("crlf.py", "line_endings", "CRLF windows line endings (\r\n)"),
        ("declared_iso8859.py", "encodings", "PEP 263 Latin-1 (iso-8859-1) declaration"),
        ("declared_utf8.py", "encodings", "PEP 263 UTF-8 declaration"),
        ("empty.py", "filesystem", "Zero-byte empty file"),
        ("mixed_newlines.py", "line_endings", "Mixed CRLF and LF newlines in same file"),
        ("no_trailing_newline.py", "line_endings", "File without trailing newline character"),
        ("spaces and unicode alpha.py", "filenames", "Filename containing space characters and Unicode Greek alpha"),
        ("utf8_bom.py", "encodings", "UTF-8 with Byte Order Mark (BOM) signature"),
        ("utf8_plain.py", "encodings", "Plain standard UTF-8 without BOM"),
        ("readonly.py", "permissions", "Target with read-only file attribute"),
        ("deep_path/level1/level2/level3/nested_target.py", "filenames", "Deeply nested directory path structure"),
        ("languages/binary.bin", "detection", "Binary file with null bytes (unsupported)"),
        ("languages/conflicting_shebang.py", "detection", ".py extension with conflicting bash shebang (unsupported)"),
        ("languages/empty.py", "detection", "Empty .py file (supported)"),
        ("languages/empty_no_ext", "detection", "Empty file without extension (unsupported)"),
        ("languages/misleading_ext.txt", "detection", "Text extension without shebang (unsupported)"),
        ("languages/misleading_with_shebang.txt", "detection", "Text extension with python shebang (probable)"),
        ("languages/page.html", "detection", "HTML file (unsupported)"),
        ("languages/script.js", "detection", "JavaScript file (unsupported)"),
        ("languages/shebang_broken", "detection", "Broken shebang syntax (unsupported)"),
        ("languages/shebang_script", "detection", "Extensionless script with python shebang (certain)"),
        ("languages/valid.py", "detection", "Valid python script (certain)"),
        ("processes/spawn_child.py", "processes", "Subprocess spawning child process"),
        ("processes/spawn_tree.py", "processes", "Subprocess spawning hierarchical process tree"),
    ]

    for rel_path, cat, desc in boundary_items:
        file_path = base / rel_path
        assert file_path.is_file(), f"Missing boundary file: {file_path}"
        manifest.append({
            "id": f"bound_{rel_path.replace('/', '_').replace(' ', '_').replace('.', '_')}",
            "path": rel_path,
            "category": cat,
            "description": desc,
        })

    out_file = base / "manifest.json"
    doc = {
        "dataset": "boundary_samples",
        "version": "1.0.0",
        "total_samples": len(manifest),
        "samples": manifest,
    }
    out_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"Generated {out_file} with {len(manifest)} verified boundary fixtures.")


if __name__ == "__main__":
    build_bug_manifest()
    build_complexity_manifest()
    build_profiling_manifest()
    build_boundary_manifest()
