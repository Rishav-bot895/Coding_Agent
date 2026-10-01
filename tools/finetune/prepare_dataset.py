"""Instruction-tuning dataset preparation, validation, and token-filtering pipeline.

Implements Phase 13 Task P13-T1:
- Deterministic synthetic generation & bootstrapping of Python repair pairs.
- Multi-stage validation: Pydantic schemas, evidence manifest grounding, and patch application.
- Strict token budgeting: <= 1,200 prompt, <= 600 completion, <= 2,048 total via Qwen2.5 tokenizer.
- Stratified 80/10/10 train/val/test splits.
- Production manifest serialization to datasets/finetune/manifest.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from localdev.patching.edit_schema import (
    EditOperation,
    EditOperationType,
    EditProposalRecord,
)
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisAbstention,
    DiagnosisAbstentionReason,
    DiagnosisRecord,
)
from tools.finetune.schema_templates import (
    DatasetSampleType,
    InstructionPair,
    QwenTokenCounter,
    build_abstention_prompt_messages,
    build_diagnosis_prompt_messages,
    build_edit_proposal_prompt_messages,
    validate_instruction_pair,
)

logger = logging.getLogger("prepare_dataset")

DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent / "datasets" / "finetune"
)
TOTAL_SAMPLES_TARGET: Final[int] = 3800
DEFAULT_SEED: Final[int] = 42


# =============================================================================
# Deterministic Domain and Name Vocabularies
# =============================================================================

DOMAINS: list[dict[str, Any]] = [
    {
        "domain": "finance",
        "terms": ["transaction", "balance", "fee", "portfolio", "dividend", "yield_rate", "asset", "margin"],
        "fn_prefixes": ["calculate", "compute", "rebalance", "process", "audit", "estimate"],
    },
    {
        "domain": "telemetry",
        "terms": ["reading", "sensor_val", "voltage", "latency", "packet", "metric", "signal", "bandwidth"],
        "fn_prefixes": ["aggregate", "monitor", "record", "poll", "filter", "normalize"],
    },
    {
        "domain": "ecommerce",
        "terms": ["order", "cart", "discount", "item_price", "shipping", "coupon", "tax_rate", "subtotal"],
        "fn_prefixes": ["apply", "checkout", "adjust", "compute", "validate", "summarize"],
    },
    {
        "domain": "analytics",
        "terms": ["score", "datapoint", "frequency", "variance", "percentile", "weight", "sample", "std_dev"],
        "fn_prefixes": ["evaluate", "analyze", "scale", "extrapolate", "cluster", "measure"],
    },
    {
        "domain": "inventory",
        "terms": ["stock", "quantity", "sku", "warehouse", "shelf", "reorder_point", "batch", "replenish"],
        "fn_prefixes": ["update", "track", "audit", "replenish", "allocate", "dispatch"],
    },
    {
        "domain": "network",
        "terms": ["endpoint", "hop_count", "retry", "timeout", "header", "payload", "socket", "gateway"],
        "fn_prefixes": ["route", "send", "connect", "parse", "forward", "handshake"],
    },
    {
        "domain": "users",
        "terms": ["profile", "account_id", "tier", "permission", "session_token", "credit", "quota", "role"],
        "fn_prefixes": ["authenticate", "verify", "assign", "elevate", "refresh", "authorize"],
    },
    {
        "domain": "ml_pipeline",
        "terms": ["loss", "epoch", "embedding", "learning_rate", "gradient", "activation", "token", "tensor"],
        "fn_prefixes": ["optimize", "backprop", "clip", "step", "project", "quantize"],
    },
]


# =============================================================================
# Generator Core & Builders
# =============================================================================


def find_snippet_line(code: str, snippet: str) -> int:
    """Find 1-based start line of exact snippet in code."""
    lines = code.splitlines()
    first_snippet_line = snippet.splitlines()[0]
    for idx, line in enumerate(lines, start=1):
        if line == first_snippet_line or line.strip() == first_snippet_line.strip():
            return idx
    raise ValueError(f"Snippet line '{first_snippet_line}' not found in code:\n{code}")


class DatasetGenerator:
    """Deterministic generator producing verified instruction pairs."""

    def __init__(self, seed: int = DEFAULT_SEED, tokenizer_path: Path | None = None) -> None:
        self.rng = random.Random(seed)
        self.token_counter = QwenTokenCounter(tokenizer_path)

    # -------------------------------------------------------------------------
    # Bug Type 1: ZeroDivisionError
    # -------------------------------------------------------------------------
    def gen_zero_division_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term1 = self.rng.choice(domain["terms"])
        term2 = self.rng.choice(domain["terms"])
        if term1 == term2:
            term2 = f"{term2}_count"
        fn_name = f"{self.rng.choice(domain['fn_prefixes'])}_{term1}_ratio_{idx}"
        target_file = f"{domain['domain']}_{term1}.py"

        variant = idx % 5
        if variant == 0:
            # Average on list
            code = (
                f'"""Calculates {term1} metrics for {domain["domain"]}."""\n\n\n'
                f"def {fn_name}({term1}_list: list[float]) -> float:\n"
                f'    """Compute arithmetic mean of {term1}_list."""\n'
                f"    total = sum({term1}_list)\n"
                f"    # Line below causes ZeroDivisionError when {term1}_list is empty\n"
                f"    return total / len({term1}_list)\n"
            )
            fault_line = 7
            expected = f"    return total / len({term1}_list)"
            replacement = (
                f"    if not {term1}_list:\n"
                f"        return 0.0\n"
                f"    return total / len({term1}_list)"
            )
            bug_desc = f"ZeroDivisionError in `{fn_name}` when `{term1}_list` is empty."
            root_cause = f"The expression `total / len({term1}_list)` attempts division by zero when the input list contains zero elements."
            rationale = f"Dividing the total sum by `len({term1}_list)` without an emptiness check fails on empty inputs."
            fix_expl = f"Add empty list guard returning 0.0 before performing division in `{fn_name}`."

        elif variant == 1:
            # Ratio with denominator parameter
            code = (
                f'"""Calculates {term1} proportion for {domain["domain"]}."""\n\n\n'
                f"def {fn_name}({term1}: float, {term2}: float) -> float:\n"
                f'    """Compute ratio of {term1} relative to {term2}."""\n'
                f"    scale = 100.0\n"
                f"    # Division without verifying non-zero denominator\n"
                f"    return ({term1} / {term2}) * scale\n"
            )
            fault_line = 7
            expected = f"    return ({term1} / {term2}) * scale"
            replacement = (
                f"    if {term2} == 0.0:\n"
                f"        return 0.0\n"
                f"    return ({term1} / {term2}) * scale"
            )
            bug_desc = f"ZeroDivisionError in `{fn_name}` when `{term2}` is zero."
            root_cause = f"The denominator `{term2}` is passed directly to the division operator without a zero-check."
            rationale = f"Input parameter `{term2}` can be 0.0, causing an immediate ZeroDivisionError exception at runtime."
            fix_expl = f"Guard against zero denominator `{term2}` by returning 0.0."

        elif variant == 2:
            # Modulo operation
            code = (
                f'"""Partitions {term1} into {term2} buckets."""\n\n\n'
                f"def {fn_name}(total_{term1}: int, bucket_size: int) -> int:\n"
                f'    """Compute remainder bucket offset for {term1}."""\n'
                f"    base_offset = 1\n"
                f"    # Modulo by zero when bucket_size is 0\n"
                f"    remainder = total_{term1} % bucket_size\n"
                f"    return base_offset + remainder\n"
            )
            fault_line = 7
            expected = f"    remainder = total_{term1} % bucket_size"
            replacement = (
                f"    if bucket_size <= 0:\n"
                f"        return base_offset\n"
                f"    remainder = total_{term1} % bucket_size"
            )
            bug_desc = f"ZeroDivisionError (integer modulo by zero) in `{fn_name}`."
            root_cause = f"The modulo operator `total_{term1} % bucket_size` triggers ZeroDivisionError when `bucket_size` is zero."
            rationale = "Bucket size parameter `bucket_size` is not validated before the modulo operation."
            fix_expl = "Add check for `bucket_size <= 0` before computing modulo."

        elif variant == 3:
            # Min-Max Normalization
            code = (
                f'"""Feature normalization for {domain["domain"]}."""\n\n\n'
                f"def {fn_name}(val: float, min_val: float, max_val: float) -> float:\n"
                f'    """Rescale {term1} value into [0, 1] interval."""\n'
                f"    diff = max_val - min_val\n"
                f"    # Division by zero when min_val equals max_val\n"
                f"    normalized = (val - min_val) / diff\n"
                f"    return max(0.0, min(1.0, normalized))\n"
            )
            fault_line = 7
            expected = "    normalized = (val - min_val) / diff"
            replacement = (
                "    if diff == 0.0:\n"
                "        return 0.0\n"
                "    normalized = (val - min_val) / diff"
            )
            bug_desc = f"ZeroDivisionError during min-max scaling in `{fn_name}`."
            root_cause = "`max_val - min_val` evaluates to zero when bounds are identical, resulting in division by zero."
            rationale = "Constant or uniform input ranges lead to `diff == 0.0`, triggering ZeroDivisionError."
            fix_expl = "Add guard checking if `diff == 0.0` before performing normalization division."

        else:
            # Rate / duration calculation
            code = (
                f'"""Calculates throughput rate for {domain["domain"]}."""\n\n\n'
                f"def {fn_name}(total_events: int, elapsed_seconds: float) -> float:\n"
                f'    """Compute {term1} processing rate per second."""\n'
                f"    overhead_factor = 1.0\n"
                f"    # Division by zero when elapsed time is 0.0\n"
                f"    rate = (total_events * overhead_factor) / elapsed_seconds\n"
                f"    return rate\n"
            )
            fault_line = 7
            expected = "    rate = (total_events * overhead_factor) / elapsed_seconds"
            replacement = (
                "    if elapsed_seconds <= 0.0:\n"
                "        return 0.0\n"
                "    rate = (total_events * overhead_factor) / elapsed_seconds"
            )
            bug_desc = f"ZeroDivisionError in `{fn_name}` when `elapsed_seconds` is zero."
            root_cause = "`elapsed_seconds` can be zero when timer resolution is instantaneous or uninitialized."
            rationale = "Dividing events by elapsed time without non-zero validation causes ZeroDivisionError."
            fix_expl = "Return 0.0 when `elapsed_seconds <= 0.0` before dividing."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 2: IndexError
    # -------------------------------------------------------------------------
    def gen_index_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"{self.rng.choice(domain['fn_prefixes'])}_{term}_sequence_{idx}"
        target_file = f"{domain['domain']}_{term}_idx.py"

        variant = idx % 4
        if variant == 0:
            # Off-by-one loop bound
            code = (
                f'"""Process indexed {term} sequence."""\n\n\n'
                f"def {fn_name}(records: list[int]) -> int:\n"
                f'    """Sum all items in records using loop."""\n'
                f"    accumulator = 0\n"
                f"    # Bug: range bound is len(records) + 1, indexing out of bounds\n"
                f"    for i in range(len(records) + 1):\n"
                f"        accumulator += records[i]\n"
                f"    return accumulator\n"
            )
            fault_line = 7
            expected = "    for i in range(len(records) + 1):"
            replacement = "    for i in range(len(records)):"
            bug_desc = f"IndexError: list index out of range in loop in `{fn_name}`."
            root_cause = "The loop range `range(len(records) + 1)` exceeds valid 0-based indices by 1."
            rationale = "On the final loop iteration `i == len(records)`, accessing `records[i]` raises IndexError."
            fix_expl = "Correct loop upper bound to `len(records)`."

        elif variant == 1:
            # Pop on empty sequence
            code = (
                f'"""Manages {term} stack buffer."""\n\n\n'
                f"def {fn_name}(stack: list[dict[str, Any]]) -> dict[str, Any]:\n"
                f'    """Retrieve and remove most recent {term} entry."""\n'
                f"    status_flag = True\n"
                f"    # Pop from empty list raises IndexError\n"
                f"    latest = stack.pop()\n"
                f"    return latest\n"
            )
            fault_line = 7
            expected = "    latest = stack.pop()"
            replacement = (
                "    if not stack:\n"
                "        return {}\n"
                "    latest = stack.pop()"
            )
            bug_desc = f"IndexError: pop from empty list in `{fn_name}`."
            root_cause = "`stack.pop()` is called without first checking if the list contains any elements."
            rationale = "When the stack is empty, calling `pop()` immediately triggers an IndexError."
            fix_expl = "Add empty check returning empty dict if `not stack` before calling `pop()`."

        elif variant == 2:
            # Direct first element lookup
            code = (
                f'"""Selects primary {term} record."""\n\n\n'
                f"def {fn_name}(entries: list[str]) -> str:\n"
                f'    """Return primary {term} identifier."""\n'
                f"    prefix = 'id_'\n"
                f"    # Indexing entries[0] without verifying list is non-empty\n"
                f"    primary = entries[0]\n"
                f"    return prefix + primary\n"
            )
            fault_line = 7
            expected = "    primary = entries[0]"
            replacement = (
                "    if not entries:\n"
                "        return prefix + 'unknown'\n"
                "    primary = entries[0]"
            )
            bug_desc = f"IndexError: list index out of range when accessing first item in `{fn_name}`."
            root_cause = "Unchecked 0-index subscripting `entries[0]` on an empty list."
            rationale = "When `entries` is empty, index 0 is out of bounds, causing IndexError."
            fix_expl = "Check `if not entries:` before subscripting `entries[0]`."

        else:
            # Multi-token extraction
            code = (
                f'"""Parses secondary {term} token from delimited string."""\n\n\n'
                f"def {fn_name}(raw_line: str) -> str:\n"
                f'    """Extract secondary field from CSV {term} line."""\n'
                f"    tokens = raw_line.strip().split(',')\n"
                f"    # Accessing tokens[1] fails if raw_line has no comma\n"
                f"    secondary = tokens[1]\n"
                f"    return secondary.strip()\n"
            )
            fault_line = 6
            expected = "    secondary = tokens[1]"
            replacement = (
                "    if len(tokens) < 2:\n"
                "        return ''\n"
                "    secondary = tokens[1]"
            )
            bug_desc = f"IndexError: list index out of range in token extraction in `{fn_name}`."
            root_cause = "Assuming at least 2 tokens exist after splitting string on comma."
            rationale = "Single-element split results raise IndexError when indexing index 1."
            fix_expl = "Verify `len(tokens) < 2` before extracting secondary token."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 3: KeyError
    # -------------------------------------------------------------------------
    def gen_key_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"lookup_{term}_config_{idx}"
        target_file = f"{domain['domain']}_{term}_map.py"

        variant = idx % 3
        if variant == 0:
            key_name = f"{term}_id"
            code = (
                f'"""Configuration lookup for {domain["domain"]} {term}."""\n\n\n'
                f"def {fn_name}(config: dict[str, Any]) -> str:\n"
                f'    """Retrieve configured {key_name} from dictionary."""\n'
                f"    prefix = 'CFG_'\n"
                f"    # Direct subscript raises KeyError if key is missing\n"
                f"    value = config['{key_name}']\n"
                f"    return f'{{prefix}}{{value}}'\n"
            )
            fault_line = 7
            expected = f"    value = config['{key_name}']"
            replacement = f"    value = config.get('{key_name}', 'default')"
            bug_desc = f"KeyError: '{key_name}' missing from config mapping in `{fn_name}`."
            root_cause = f"Direct dictionary subscripting `config['{key_name}']` without membership check or default."
            rationale = "Missing key in dictionary parameter causes Python to raise KeyError."
            fix_expl = f"Use `config.get('{key_name}', 'default')` to provide a safe fallback."

        elif variant == 1:
            key_name = f"max_{term}_limit"
            code = (
                f'"""Threshold validator for {term}."""\n\n\n'
                f"def {fn_name}(settings: dict[str, int]) -> int:\n"
                f'    """Fetch numerical limit for {term}."""\n'
                f"    multiplier = 2\n"
                f"    # Key lookup without checking existence\n"
                f"    limit = settings['{key_name}']\n"
                f"    return limit * multiplier\n"
            )
            fault_line = 7
            expected = f"    limit = settings['{key_name}']"
            replacement = (
                f"    if '{key_name}' not in settings:\n"
                f"        return 0\n"
                f"    limit = settings['{key_name}']"
            )
            bug_desc = f"KeyError: '{key_name}' not found in settings dictionary in `{fn_name}`."
            root_cause = f"`settings` dictionary is accessed directly without verifying '{key_name}' presence."
            rationale = f"Callers passing partial configurations trigger KeyError on missing '{key_name}'."
            fix_expl = f"Add membership check `if '{key_name}' not in settings: return 0`."

        else:
            key_name = f"cached_{term}"
            code = (
                f'"""Cache eviction for {domain["domain"]}."""\n\n\n'
                f"def {fn_name}(cache: dict[str, Any], key: str) -> None:\n"
                f'    """Delete key from cache."""\n'
                f"    audit_log = True\n"
                f"    # del raises KeyError if key does not exist\n"
                f"    del cache[key]\n"
            )
            fault_line = 7
            expected = "    del cache[key]"
            replacement = "    cache.pop(key, None)"
            bug_desc = f"KeyError in `{fn_name}` when deleting non-existent cache key."
            root_cause = "`del cache[key]` raises KeyError when `key` is absent from the dictionary."
            rationale = "Deleting absent dictionary keys with `del` raises KeyError."
            fix_expl = "Replace `del cache[key]` with `cache.pop(key, None)` for idempotent removal."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 4: TypeError
    # -------------------------------------------------------------------------
    def gen_type_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"format_{term}_summary_{idx}"
        target_file = f"{domain['domain']}_{term}_fmt.py"

        variant = idx % 4
        if variant == 0:
            # String + int concatenation
            code = (
                f'"""Summary formatter for {domain["domain"]}."""\n\n\n'
                f"def {fn_name}(label: str, count: int) -> str:\n"
                f'    """Format label and count into header string."""\n'
                f"    prefix = 'STATUS: '\n"
                f"    # Concatenating string and integer raises TypeError\n"
                f"    header = prefix + label + ' count: ' + count\n"
                f"    return header\n"
            )
            fault_line = 7
            expected = "    header = prefix + label + ' count: ' + count"
            replacement = "    header = prefix + label + ' count: ' + str(count)"
            bug_desc = f"TypeError: can only concatenate str (not 'int') to str in `{fn_name}`."
            root_cause = "The `+` operator cannot concatenate string operand with integer `count`."
            rationale = "Python does not perform implicit string conversion during string addition."
            fix_expl = "Wrap `count` in `str(count)` or use f-string formatting."

        elif variant == 1:
            # Calling non-callable
            code = (
                f'"""Calculates {term} tally."""\n\n\n'
                f"def {fn_name}(total_count: int) -> int:\n"
                f'    """Return adjusted total."""\n'
                f"    base = 10\n"
                f"    # Calling integer variable as function raises TypeError\n"
                f"    result = base + total_count()\n"
                f"    return result\n"
            )
            fault_line = 7
            expected = "    result = base + total_count()"
            replacement = "    result = base + total_count"
            bug_desc = f"TypeError: 'int' object is not callable in `{fn_name}`."
            root_cause = "`total_count` is an integer but is invoked with parentheses `total_count()`."
            rationale = "Integers do not implement the call protocol `__call__`."
            fix_expl = "Remove unnecessary function call parentheses from `total_count`."

        elif variant == 2:
            # None comparison
            code = (
                f'"""Evaluates {term} threshold."""\n\n\n'
                f"def {fn_name}(metric: float | None, threshold: float) -> bool:\n"
                f'    """Check if metric exceeds target threshold."""\n'
                f"    is_active = True\n"
                f"    # Comparing None < float raises TypeError in Python 3\n"
                f"    if metric > threshold:\n"
                f"        return True\n"
                f"    return False\n"
            )
            fault_line = 7
            expected = "    if metric > threshold:"
            replacement = (
                "    if metric is not None and metric > threshold:"
            )
            bug_desc = f"TypeError: '>' not supported between instances of 'NoneType' and 'float' in `{fn_name}`."
            root_cause = "`metric` can be None; relational comparison between None and float is invalid."
            rationale = "Python 3 strictly disallows ordering comparisons between NoneType and numbers."
            fix_expl = "Add `metric is not None` guard before relational comparison."

        else:
            # None arithmetic
            code = (
                f'"""Calculates {term} bonus."""\n\n\n'
                f"def {fn_name}(base: float, bonus: float | None) -> float:\n"
                f'    """Add optional bonus to base amount."""\n'
                f"    multiplier = 1.05\n"
                f"    # Adding float and None raises TypeError\n"
                f"    total = base + bonus\n"
                f"    return total * multiplier\n"
            )
            fault_line = 7
            expected = "    total = base + bonus"
            replacement = (
                "    actual_bonus = 0.0 if bonus is None else bonus\n"
                "    total = base + actual_bonus"
            )
            bug_desc = f"TypeError: unsupported operand type(s) for +: 'float' and 'NoneType' in `{fn_name}`."
            root_cause = "`bonus` argument can be None; binary addition with float fails."
            rationale = "Adding NoneType to float triggers TypeError at runtime."
            fix_expl = "Substitute 0.0 when `bonus` is None before addition."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 5: AttributeError
    # -------------------------------------------------------------------------
    def gen_attribute_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"parse_{term}_attribute_{idx}"
        target_file = f"{domain['domain']}_{term}_attr.py"

        variant = idx % 3
        if variant == 0:
            # Calling strip() on None
            code = (
                f'"""Cleans raw {term} string token."""\n\n\n'
                f"def {fn_name}(raw_token: str | None) -> str:\n"
                f'    """Sanitize input {term} token."""\n'
                f"    default_val = 'N/A'\n"
                f"    # AttributeError when raw_token is None\n"
                f"    cleaned = raw_token.strip()\n"
                f"    return cleaned.upper()\n"
            )
            fault_line = 7
            expected = "    cleaned = raw_token.strip()"
            replacement = (
                "    if raw_token is None:\n"
                "        return default_val\n"
                "    cleaned = raw_token.strip()"
            )
            bug_desc = f"AttributeError: 'NoneType' object has no attribute 'strip' in `{fn_name}`."
            root_cause = "`raw_token.strip()` invoked on None reference."
            rationale = "None does not provide string methods such as `strip()`."
            fix_expl = "Add check `if raw_token is None: return default_val`."

        elif variant == 1:
            # Method typo: append_all instead of extend
            code = (
                f'"""Merges {term} collections."""\n\n\n'
                f"def {fn_name}(base_items: list[str], new_items: list[str]) -> list[str]:\n"
                f'    """Append all new items to base collection."""\n'
                f"    result = list(base_items)\n"
                f"    # List has no attribute 'append_all'\n"
                f"    result.append_all(new_items)\n"
                f"    return result\n"
            )
            fault_line = 7
            expected = "    result.append_all(new_items)"
            replacement = "    result.extend(new_items)"
            bug_desc = f"AttributeError: 'list' object has no attribute 'append_all' in `{fn_name}`."
            root_cause = "Python standard `list` uses `.extend()`, not `.append_all()`."
            rationale = "Calling a non-existent method name on a built-in list raises AttributeError."
            fix_expl = "Replace `append_all` with standard method `extend`."

        else:
            # Method typo: lower_case instead of lower
            code = (
                f'"""Standardizes {term} identifier."""\n\n\n'
                f"def {fn_name}(identifier: str) -> str:\n"
                f'    """Convert {term} identifier to lowercase."""\n'
                f"    prefix = 'id_'\n"
                f"    # String has no attribute 'lower_case'\n"
                f"    lowered = identifier.lower_case()\n"
                f"    return prefix + lowered\n"
            )
            fault_line = 7
            expected = "    lowered = identifier.lower_case()"
            replacement = "    lowered = identifier.lower()"
            bug_desc = f"AttributeError: 'str' object has no attribute 'lower_case' in `{fn_name}`."
            root_cause = "Invalid string method `lower_case` called instead of standard `lower`."
            rationale = "String objects implement `lower()`, while `lower_case()` does not exist."
            fix_expl = "Change `identifier.lower_case()` to `identifier.lower()`."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 6: ValueError
    # -------------------------------------------------------------------------
    def gen_value_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"convert_{term}_value_{idx}"
        target_file = f"{domain['domain']}_{term}_val.py"

        variant = idx % 3
        if variant == 0:
            # int() on invalid string
            code = (
                f'"""Parses integer {term} setting."""\n\n\n'
                f"def {fn_name}(raw_str: str) -> int:\n"
                f'    """Convert raw input string to integer port/id."""\n'
                f"    fallback = -1\n"
                f"    # Non-digit string causes ValueError: invalid literal for int()\n"
                f"    parsed = int(raw_str)\n"
                f"    return parsed\n"
            )
            fault_line = 7
            expected = "    parsed = int(raw_str)"
            replacement = (
                "    try:\n"
                "        parsed = int(raw_str)\n"
                "    except ValueError:\n"
                "        return fallback"
            )
            bug_desc = f"ValueError: invalid literal for int() in `{fn_name}`."
            root_cause = "`int(raw_str)` fails when `raw_str` contains non-numeric characters."
            rationale = "Unvalidated user or file inputs raise ValueError during integer conversion."
            fix_expl = "Wrap integer conversion in try/except ValueError block returning fallback."

        elif variant == 1:
            # Unpacking mismatch
            code = (
                f'"""Extracts {term} pair from coordinate tuple."""\n\n\n'
                f"def {fn_name}(coords: tuple[int, ...]) -> tuple[int, int]:\n"
                f'    """Unpack 2D coordinates."""\n'
                f"    offset = 0\n"
                f"    # ValueError: too many/few values to unpack if coords length != 2\n"
                f"    x, y = coords\n"
                f"    return x + offset, y + offset\n"
            )
            fault_line = 7
            expected = "    x, y = coords"
            replacement = (
                "    if len(coords) < 2:\n"
                "        return 0, 0\n"
                "    x, y = coords[0], coords[1]"
            )
            bug_desc = f"ValueError: unpack mismatch in `{fn_name}`."
            root_cause = "Direct 2-variable unpacking fails if tuple has length not equal to 2."
            rationale = "Tuples with 0, 1, or 3+ items trigger ValueError during 2-variable assignment."
            fix_expl = "Explicitly check `len(coords) < 2` and index coordinates safely."

        else:
            # list.remove on absent element
            code = (
                f'"""Removes expired {term} from list."""\n\n\n'
                f"def {fn_name}(queue: list[str], target_item: str) -> None:\n"
                f'    """Evict target item from active queue."""\n'
                f"    log_action = True\n"
                f"    # list.remove(x) raises ValueError if x not in list\n"
                f"    queue.remove(target_item)\n"
            )
            fault_line = 7
            expected = "    queue.remove(target_item)"
            replacement = (
                "    if target_item in queue:\n"
                "        queue.remove(target_item)"
            )
            bug_desc = f"ValueError: list.remove(x): x not in list in `{fn_name}`."
            root_cause = "`queue.remove()` invoked without verifying element membership."
            rationale = "Calling `.remove()` on absent element raises ValueError."
            fix_expl = "Guard removal with `if target_item in queue:`."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 7: NameError
    # -------------------------------------------------------------------------
    def gen_name_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"compute_{term}_metric_{idx}"
        target_file = f"{domain['domain']}_{term}_name.py"

        variant = idx % 2
        if variant == 0:
            # Variable typo
            code = (
                f'"""Aggregates {term} totals."""\n\n\n'
                f"def {fn_name}(items: list[int]) -> int:\n"
                f'    """Sum elements and return total."""\n'
                f"    total_sum = 0\n"
                f"    for itm in items:\n"
                f"        total_sum += itm\n"
                f"    # Typo: total_sun instead of total_sum\n"
                f"    return total_sun\n"
            )
            fault_line = 9
            expected = "    return total_sun"
            replacement = "    return total_sum"
            bug_desc = f"NameError: name 'total_sun' is not defined in `{fn_name}`."
            root_cause = "Typographical error referencing undeclared identifier `total_sun` instead of `total_sum`."
            rationale = "Variable `total_sun` was never declared or imported in function scope."
            fix_expl = "Correct identifier typo to `total_sum`."

        else:
            # Missing stdlib import (math)
            code = (
                f'"""Calculates {term} circle radius area."""\n\n\n'
                f"def {fn_name}(radius: float) -> float:\n"
                f'    """Compute area using math.pi."""\n'
                f"    scale = 1.0\n"
                f"    # Missing import math raises NameError\n"
                f"    return math.pi * (radius ** 2) * scale\n"
            )
            fault_line = 7
            expected = "    return math.pi * (radius ** 2) * scale"
            replacement = (
                "    import math\n"
                "    return math.pi * (radius ** 2) * scale"
            )
            bug_desc = f"NameError: name 'math' is not defined in `{fn_name}`."
            root_cause = "The standard library module `math` was used without an `import math` statement."
            rationale = "Accessing module attributes without importing the module raises NameError."
            fix_expl = "Add import statement `import math` before using `math.pi`."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 8: RecursionError
    # -------------------------------------------------------------------------
    def gen_recursion_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"traverse_{term}_tree_{idx}"
        target_file = f"{domain['domain']}_{term}_rec.py"

        code = (
            f'"""Recursive calculation for {domain["domain"]}."""\n\n\n'
            f"def {fn_name}(depth: int) -> int:\n"
            f'    """Recurse down depth levels."""\n'
            f"    # Missing base case: recurses indefinitely for any input\n"
            f"    return 1 + {fn_name}(depth - 1)\n"
        )
        fault_line = 6
        expected = f"    return 1 + {fn_name}(depth - 1)"
        replacement = (
            f"    if depth <= 0:\n"
            f"        return 0\n"
            f"    return 1 + {fn_name}(depth - 1)"
        )
        bug_desc = f"RecursionError: maximum recursion depth exceeded in `{fn_name}`."
        root_cause = f"`{fn_name}` lacks a termination base case, causing unbounded recursive calls."
        rationale = "Without `if depth <= 0: return 0`, recursion never halts, blowing the Python call stack."
        fix_expl = "Add base condition `if depth <= 0: return 0` to terminate recursion."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 9: SyntaxError / IndentationError
    # -------------------------------------------------------------------------
    def gen_syntax_error_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"validate_{term}_syntax_{idx}"
        target_file = f"{domain['domain']}_{term}_syn.py"

        variant = idx % 2
        if variant == 0:
            # Missing colon on if statement
            code = (
                f'"""Validates {term} payload."""\n\n\n'
                f"def {fn_name}(value: int) -> bool:\n"
                f'    """Check if value is non-negative."""\n'
                f"    # Missing colon at end of if statement\n"
                f"    if value >= 0\n"
                f"        return True\n"
                f"    return False\n"
            )
            fault_line = 6
            expected = "    if value >= 0"
            replacement = "    if value >= 0:"
            bug_desc = f"SyntaxError: expected ':' at line 6 in `{fn_name}`."
            root_cause = "The `if value >= 0` statement is missing a terminating colon."
            rationale = "Python grammar mandates a colon at the end of conditional statement headers."
            fix_expl = "Append missing colon to the `if` header."

        else:
            # Unclosed parenthesis
            code = (
                f'"""Calculates {term} adjusted index."""\n\n\n'
                f"def {fn_name}(base: int, factor: int) -> int:\n"
                f'    """Multiply base and factor with offset."""\n'
                f"    offset = 5\n"
                f"    # Unclosed parenthesis on line below\n"
                f"    result = (base * factor + offset\n"
                f"    return result\n"
            )
            fault_line = 7
            expected = "    result = (base * factor + offset"
            replacement = "    result = (base * factor + offset)"
            bug_desc = f"SyntaxError: closing parenthesis ')' does not match opening parenthesis in `{fn_name}`."
            root_cause = "Parenthesis opened on line 7 is never closed, producing invalid syntax."
            rationale = "Unbalanced parentheses cause the Python parser to halt with SyntaxError."
            fix_expl = "Close parenthesis at the end of line 7."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Bug Type 10: Logic Bugs (Wrong accumulator, mutable default, inverted flag)
    # -------------------------------------------------------------------------
    def gen_logic_bug_case(self, idx: int) -> tuple[str, str, int, str, str, str, str, str, str]:
        domain = self.rng.choice(DOMAINS)
        term = self.rng.choice(domain["terms"])
        fn_name = f"evaluate_{term}_logic_{idx}"
        target_file = f"{domain['domain']}_{term}_logic.py"

        variant = idx % 4
        if variant == 0:
            # Accidental tuple (trailing comma)
            code = (
                f'"""Calculates total {term}."""\n\n\n'
                f"def {fn_name}(quantities: list[int]) -> int:\n"
                f'    """Compute sum of all quantities."""\n'
                f"    total = sum(quantities)\n"
                f"    # Trailing comma accidentally converts return value to 1-tuple\n"
                f"    return total,\n"
            )
            fault_line = 7
            expected = "    return total,"
            replacement = "    return total"
            bug_desc = f"Logic bug: `{fn_name}` returns tuple instead of int due to trailing comma."
            root_cause = "A trailing comma after `return total,` produces a 1-tuple `(total,)` instead of scalar integer."
            rationale = "The caller expects an integer, but receives a tuple, causing subsequent arithmetic or type assertions to fail."
            fix_expl = "Remove the accidental trailing comma after `total`."

        elif variant == 1:
            # Inverted boolean condition
            code = (
                f'"""Checks {term} eligibility."""\n\n\n'
                f"def {fn_name}(balance: float, min_required: float) -> bool:\n"
                f'    """Return True if balance meets or exceeds required minimum."""\n'
                f"    # Inverted comparison operator returns False when balance is sufficient\n"
                f"    return balance < min_required\n"
            )
            fault_line = 5
            expected = "    return balance < min_required"
            replacement = "    return balance >= min_required"
            bug_desc = f"Logic bug: inverted comparison condition in `{fn_name}`."
            root_cause = "Operator `<` used instead of `>=` causes inverted eligibility decisions."
            rationale = "Eligibility requires meeting or exceeding the minimum (`balance >= min_required`)."
            fix_expl = "Change comparison operator to `balance >= min_required`."

        elif variant == 2:
            # Mutable default argument
            code = (
                f'"""Appends item to {term} batch."""\n\n\n'
                f"def {fn_name}(item: str, batch: list[str] = []) -> list[str]:\n"
                f'    """Collect items into batch list."""\n'
                f"    batch.append(item)\n"
                f"    return batch\n"
            )
            fault_line = 4
            expected = f"def {fn_name}(item: str, batch: list[str] = []) -> list[str]:"
            replacement = (
                f"def {fn_name}(item: str, batch: list[str] | None = None) -> list[str]:\n"
                f"    if batch is None:\n"
                f"        batch = []"
            )
            bug_desc = f"Logic bug: mutable default argument `batch=[]` in `{fn_name}`."
            root_cause = "The default list `[]` is created once at function definition time and mutated across calls."
            rationale = f"Subsequent calls to `{fn_name}` share the same list instance, leaking state between invocations."
            fix_expl = "Use `None` as default argument and instantiate a fresh list inside the function."

        else:
            # Early return inside loop
            code = (
                f'"""Sums positive {term} values."""\n\n\n'
                f"def {fn_name}(numbers: list[int]) -> int:\n"
                f'    """Accumulate positive integers in numbers."""\n'
                f"    total = 0\n"
                f"    for num in numbers:\n"
                f"        if num > 0:\n"
                f"            total += num\n"
                f"        # Bug: return statement inside loop terminates after first iteration\n"
                f"        return total\n"
                f"    return total\n"
            )
            fault_line = 10
            expected = "        return total"
            replacement = "        pass"
            bug_desc = f"Logic bug: premature return inside loop in `{fn_name}`."
            root_cause = "`return total` is placed inside the `for` loop body, exiting on the first element."
            rationale = "The loop terminates immediately after checking the first element, ignoring the rest of the list."
            fix_expl = "Remove early return from inside the loop body."

        return (
            code,
            target_file,
            fault_line,
            expected,
            replacement,
            bug_desc,
            root_cause,
            rationale,
            fix_expl,
        )

    # -------------------------------------------------------------------------
    # Generator for Abstention Cases
    # -------------------------------------------------------------------------
    def gen_abstention_case(self, idx: int) -> tuple[str, str, str, str, list[tuple[str, str]], list[str], str]:
        variant = idx % 4
        if variant == 0:
            # Clean Python code with exit code 0
            domain = self.rng.choice(DOMAINS)
            term = self.rng.choice(domain["terms"])
            target_file = f"{domain['domain']}_{term}_clean.py"
            code = (
                f'"""Verified clean {term} processing algorithm."""\n\n\n'
                f"def process_{term}_clean(items: list[int]) -> int:\n"
                f'    """Safely compute prefix sum of non-negative elements."""\n'
                f"    if not items:\n"
                f"        return 0\n"
                f"    total = 0\n"
                f"    for itm in items:\n"
                f"        if itm > 0:\n"
                f"            total += itm\n"
                f"    return total\n\n\n"
                f'if __name__ == "__main__":\n'
                f"    assert process_{term}_clean([1, 2, 3]) == 6\n"
                f"    assert process_{term}_clean([]) == 0\n"
            )
            reason = DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE.value
            details = "Clean execution (exit code 0). AST parses cleanly and zero static diagnostics detected."
            manifest = [
                ("runtime:clean", "Process exited cleanly with code 0"),
                ("syntax:clean", "Python AST parsed successfully without syntax errors"),
                (f"ast:function:process_{term}_clean", f"Function process_{term}_clean (lines 4-12)"),
                ("source:lines_1-17", "Source lines 1-17 of target file"),
            ]
            failure = [
                "Clean execution (exit code 0). No runtime exceptions.",
                "Syntax Status: Clean (0 syntax diagnostics).",
            ]
            category = "clean_code"

        elif variant == 1:
            # External dependency failure outside single target boundary
            domain = self.rng.choice(DOMAINS)
            pkg_name = f"cloud_{domain['domain']}_sdk"
            target_file = f"{domain['domain']}_external_client.py"
            code = (
                f'"""External service connector for {domain["domain"]}."""\n\n'
                f"import {pkg_name}  # Non-existent external package\n\n\n"
                f"def fetch_remote_{domain['terms'][0]}() -> dict:\n"
                f'    """Call remote client."""\n'
                f"    client = {pkg_name}.Client()\n"
                f"    return client.get_status()\n"
            )
            reason = DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE.value
            details = f"Failure originates from missing external package '{pkg_name}' outside the single target file boundary."
            manifest = [
                ("runtime:ModuleNotFoundError", f"No module named '{pkg_name}'"),
                ("traceback:line_3", "Top target traceback frame at line 3"),
                ("source:lines_1-9", "Source lines 1-9 of target file"),
            ]
            failure = [
                f"Runtime Exception: ModuleNotFoundError: No module named '{pkg_name}'",
                "Fault Line in Target: line 3",
            ]
            category = "external_dependency"

        elif variant == 2:
            # Out-of-bounds line error
            target_file = f"out_of_bounds_target_{idx}.py"
            code = (
                '"""Small script with 6 lines total."""\n\n'
                "def calculate() -> int:\n"
                "    return 42\n"
            )
            reason = DiagnosisAbstentionReason.OUT_OF_BOUNDS_LINES.value
            details = "Reported traceback frame references line 450, which is outside the target file bounds (4 lines)."
            manifest = [
                ("traceback:line_450", "External or corrupted traceback frame referencing line 450"),
                ("source:lines_1-4", "Source lines 1-4 of target file"),
            ]
            failure = [
                "Runtime Exception: RuntimeError: Corrupted frame pointer",
                "Fault Line in Target: line 450",
            ]
            category = "out_of_bounds"

        else:
            # Prompt budget exceeded
            target_file = f"oversized_target_{idx}.py"
            code = (
                '"""Large target file."""\n\n'
                + "\n".join(f"def helper_{i}() -> int: return {i}" for i in range(15))
                + "\n# ... [additional functions omitted due to prompt budget limit] ...\n"
            )
            reason = DiagnosisAbstentionReason.PROMPT_BUDGET_EXCEEDED.value
            details = "Assembled model context exceeded prompt budget limit of 1,200 tokens during evidence collection."
            manifest = [
                ("runtime:MemoryError", "Subprocess exceeded memory limit"),
                ("source:lines_1-17", "Truncated source excerpt"),
            ]
            failure = [
                "Error: Prompt token budget (1,200 tokens) exceeded; evidence was truncated.",
            ]
            category = "prompt_budget_exceeded"

        return code, target_file, reason, details, manifest, failure, category

    # -------------------------------------------------------------------------
    # Main Generation Loop
    # -------------------------------------------------------------------------
    def generate_all_pairs(
        self,
        total_target: int = TOTAL_SAMPLES_TARGET,
    ) -> list[InstructionPair]:
        """Generate at least `total_target` validated instruction-tuning pairs."""
        pairs: list[InstructionPair] = []

        # Target distribution:
        # ~1,550 Diagnosis pairs
        # ~1,550 Edit Proposal pairs
        # ~700 Abstention pairs
        # Total = 3,800 pairs (exceeds 3,000 threshold)
        diag_target = int(total_target * 0.41)  # 1558
        edit_target = int(total_target * 0.41)  # 1558
        abst_target = total_target - diag_target - edit_target  # 684

        generators = [
            ("ZeroDivisionError", self.gen_zero_division_case),
            ("IndexError", self.gen_index_error_case),
            ("KeyError", self.gen_key_error_case),
            ("TypeError", self.gen_type_error_case),
            ("AttributeError", self.gen_attribute_error_case),
            ("ValueError", self.gen_value_error_case),
            ("NameError", self.gen_name_error_case),
            ("RecursionError", self.gen_recursion_error_case),
            ("SyntaxError", self.gen_syntax_error_case),
            ("logic_bug", self.gen_logic_bug_case),
        ]

        logger.info(
            "Generating %d total pairs: %d diagnosis, %d edit, %d abstention...",
            total_target,
            diag_target,
            edit_target,
            abst_target,
        )

        # 1. Generate Diagnosis Pairs
        for i in range(diag_target):
            cat_name, gen_fn = generators[i % len(generators)]
            (
                code,
                target_file,
                _,
                expected,
                replacement,
                bug_desc,
                root_cause,
                rationale,
                fix_expl,
            ) = gen_fn(i)

            fault_line = find_snippet_line(code, expected)

            evidence_items = [
                (f"runtime:{cat_name}", f"Runtime failure: {bug_desc}"),
                (f"traceback:line_{fault_line}", f"Top traceback frame at line {fault_line}"),
                ("syntax:clean" if cat_name != "SyntaxError" else "syntax:error", "Syntax check result"),
                (f"source:lines_1-{len(code.splitlines())}", f"Source lines 1-{len(code.splitlines())}"),
            ]
            failure_lines = [
                f"Runtime Exception: {cat_name}: {bug_desc}",
                f"Fault Line in Target: line {fault_line}",
            ]

            messages, user_prompt, manifest_ids = build_diagnosis_prompt_messages(
                target_file=target_file,
                source_code=code,
                evidence_items=evidence_items,
                failure_lines=failure_lines,
            )
            full_prompt = f"{messages[0]['content']}\n\n{user_prompt}"

            # Grounded citation citing manifest IDs
            cited_ids = [f"runtime:{cat_name}", f"traceback:line_{fault_line}"]
            diag_record = DiagnosisRecord(
                bug_description=bug_desc,
                root_cause=root_cause,
                confidence=ConfidenceEnum.HIGH,
                cited_evidence_ids=cited_ids,
                rationale=rationale,
            )
            completion_json = diag_record.model_dump_json(indent=2)

            # Messages with assistant completion
            chat_messages = list(messages)
            chat_messages.append({"role": "assistant", "content": completion_json})

            p_tokens = self.token_counter.count_tokens(full_prompt)
            c_tokens = self.token_counter.count_tokens(completion_json)

            pair = InstructionPair(
                id=f"ft_diag_{i+1:05d}",
                task_type=DatasetSampleType.DIAGNOSIS,
                category=cat_name,
                target_file=target_file,
                source_code=code,
                prompt=full_prompt,
                completion=completion_json,
                messages=chat_messages,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=p_tokens + c_tokens,
                evidence_manifest=manifest_ids,
                metadata={"fault_line": fault_line, "cited_evidence_ids": cited_ids},
            )
            pairs.append(pair)

        # 2. Generate Edit Proposal Pairs
        for i in range(edit_target):
            cat_name, gen_fn = generators[i % len(generators)]
            (
                code,
                target_file,
                _,
                expected,
                replacement,
                bug_desc,
                root_cause,
                rationale,
                fix_expl,
            ) = gen_fn(i + 10000)

            fault_line = find_snippet_line(code, expected)
            expected_lines = expected.splitlines()
            start_l = fault_line
            end_l = fault_line + len(expected_lines) - 1
            exact_expected = "\n".join(code.splitlines()[start_l - 1 : end_l])

            edit_op = EditOperation(
                operation=EditOperationType.REPLACE,
                start_line=start_l,
                end_line=end_l,
                expected_text=exact_expected,
                replacement_text=replacement,
            )
            proposal = EditProposalRecord(
                target_file=target_file,
                edits=[edit_op],
                explanation=fix_expl,
            )
            completion_json = proposal.model_dump_json(indent=2)

            diag_summary = f"Identified {cat_name} at line {fault_line}: {bug_desc}. Root cause: {root_cause}"
            failure_ev = [
                f"Runtime Exception: {cat_name}: {bug_desc}",
                f"Fault Line in Target: line {fault_line}",
            ]

            messages, user_prompt = build_edit_proposal_prompt_messages(
                target_file=target_file,
                source_code=code,
                diagnosis_summary=diag_summary,
                failure_evidence=failure_ev,
            )
            full_prompt = f"{messages[0]['content']}\n\n{user_prompt}"

            chat_messages = list(messages)
            chat_messages.append({"role": "assistant", "content": completion_json})

            p_tokens = self.token_counter.count_tokens(full_prompt)
            c_tokens = self.token_counter.count_tokens(completion_json)

            pair = InstructionPair(
                id=f"ft_edit_{i+1:05d}",
                task_type=DatasetSampleType.EDIT_PROPOSAL,
                category=cat_name,
                target_file=target_file,
                source_code=code,
                prompt=full_prompt,
                completion=completion_json,
                messages=chat_messages,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=p_tokens + c_tokens,
                evidence_manifest=[],
                metadata={"fault_line": fault_line, "edit_count": 1},
            )
            pairs.append(pair)

        # 3. Generate Abstention Pairs
        for i in range(abst_target):
            code, target_file, reason_str, details, manifest, failure, cat = (
                self.gen_abstention_case(i)
            )

            abstention = DiagnosisAbstention(
                target=target_file,
                reason=DiagnosisAbstentionReason(reason_str),
                details=details,
                raw_payload=None,
                validation_errors=[],
                retry_attempted=False,
            )
            completion_json = abstention.model_dump_json(indent=2)

            messages, user_prompt, manifest_ids = build_abstention_prompt_messages(
                target_file=target_file,
                source_code=code,
                evidence_items=manifest,
                failure_lines=failure,
            )
            full_prompt = f"{messages[0]['content']}\n\n{user_prompt}"

            chat_messages = list(messages)
            chat_messages.append({"role": "assistant", "content": completion_json})

            p_tokens = self.token_counter.count_tokens(full_prompt)
            c_tokens = self.token_counter.count_tokens(completion_json)

            pair = InstructionPair(
                id=f"ft_abst_{i+1:05d}",
                task_type=DatasetSampleType.ABSTENTION,
                category=cat,
                target_file=target_file,
                source_code=code,
                prompt=full_prompt,
                completion=completion_json,
                messages=chat_messages,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=p_tokens + c_tokens,
                evidence_manifest=manifest_ids,
                metadata={"abstention_reason": reason_str},
            )
            pairs.append(pair)

        logger.info("Successfully generated %d pairs.", len(pairs))
        return pairs


# =============================================================================
# Validation and Splitting Pipeline
# =============================================================================


def validate_dataset(
    pairs: Sequence[InstructionPair],
    token_counter: QwenTokenCounter | None = None,
) -> tuple[bool, list[str]]:
    """Execute multi-stage verification across all dataset pairs."""
    all_valid = True
    all_errors: list[str] = []

    for idx, pair in enumerate(pairs):
        is_valid, errors = validate_instruction_pair(pair, token_counter)
        if not is_valid:
            all_valid = False
            for err in errors:
                all_errors.append(f"Sample {pair.id} (index {idx}): {err}")

    return all_valid, all_errors


def create_stratified_splits(
    pairs: list[InstructionPair],
    seed: int = DEFAULT_SEED,
) -> tuple[list[InstructionPair], list[InstructionPair], list[InstructionPair]]:
    """Partition dataset into 80/10/10 train/val/test splits with stratified task types."""
    rng = random.Random(seed)

    # Group by task_type for balanced allocation across tasks
    strata: dict[str, list[InstructionPair]] = {}
    for pair in pairs:
        key = pair.task_type.value
        strata.setdefault(key, []).append(pair)

    train_set: list[InstructionPair] = []
    val_set: list[InstructionPair] = []
    test_set: list[InstructionPair] = []

    for _, group in strata.items():
        rng.shuffle(group)
        n = len(group)
        if n >= 10:
            n_val = max(1, int(round(n * 0.10)))
            n_test = max(1, int(round(n * 0.10)))
        elif n >= 3:
            n_val = 1
            n_test = 1
        else:
            n_val = 0
            n_test = 0
        n_train = max(1, n - n_val - n_test)

        train_part = group[:n_train]
        val_part = group[n_train : n_train + n_val]
        test_part = group[n_train + n_val :]

        train_set.extend(train_part)
        val_set.extend(val_part)
        test_set.extend(test_part)

    # Shuffle each split deterministically
    rng.shuffle(train_set)
    rng.shuffle(val_set)
    rng.shuffle(test_set)

    return train_set, val_set, test_set


def calculate_token_stats(tokens: list[int]) -> dict[str, float]:
    """Compute min, max, mean, median for a sequence of token counts."""
    if not tokens:
        return {"min": 0, "max": 0, "mean": 0.0, "median": 0.0}
    sorted_toks = sorted(tokens)
    n = len(sorted_toks)
    median = (
        sorted_toks[n // 2]
        if n % 2 == 1
        else (sorted_toks[n // 2 - 1] + sorted_toks[n // 2]) / 2.0
    )
    return {
        "min": float(min(sorted_toks)),
        "max": float(max(sorted_toks)),
        "mean": round(sum(sorted_toks) / float(n), 2),
        "median": round(float(median), 2),
    }


def write_dataset_files(
    output_dir: Path,
    train_pairs: list[InstructionPair],
    val_pairs: list[InstructionPair],
    test_pairs: list[InstructionPair],
) -> dict[str, Any]:
    """Serialize jsonl splits and manifest.json to target output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    train_path = output_dir / "train.jsonl"
    val_path = output_dir / "val.jsonl"
    test_path = output_dir / "test.jsonl"
    manifest_path = output_dir / "manifest.json"

    def write_jsonl(path: Path, pairs: list[InstructionPair]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for p in pairs:
                f.write(p.model_dump_json() + "\n")

    write_jsonl(train_path, train_pairs)
    write_jsonl(val_path, val_pairs)
    write_jsonl(test_path, test_pairs)

    all_pairs = train_pairs + val_pairs + test_pairs
    total_count = len(all_pairs)

    # Compute task and category distribution
    task_counts = Counter(p.task_type.value for p in all_pairs)
    category_counts = Counter(p.category for p in all_pairs)

    # Token stats
    prompt_tokens = [p.prompt_tokens for p in all_pairs]
    completion_tokens = [p.completion_tokens for p in all_pairs]
    total_tokens = [p.total_tokens for p in all_pairs]

    manifest: dict[str, Any] = {
        "dataset_name": "localdev-instruction-tuning",
        "version": "1.0.0",
        "description": "High-quality, evidence-grounded instruction-tuning corpus for local SLM code repair.",
        "total_samples": total_count,
        "splits": {
            "train": {
                "count": len(train_pairs),
                "percentage": round(len(train_pairs) / total_count * 100, 2),
                "file": "train.jsonl",
            },
            "val": {
                "count": len(val_pairs),
                "percentage": round(len(val_pairs) / total_count * 100, 2),
                "file": "val.jsonl",
            },
            "test": {
                "count": len(test_pairs),
                "percentage": round(len(test_pairs) / total_count * 100, 2),
                "file": "test.jsonl",
            },
        },
        "task_distribution": {
            k: {
                "count": v,
                "percentage": round(v / total_count * 100, 2),
            }
            for k, v in sorted(task_counts.items())
        },
        "category_distribution": dict(sorted(category_counts.items())),
        "token_statistics": {
            "prompt_tokens": calculate_token_stats(prompt_tokens),
            "completion_tokens": calculate_token_stats(completion_tokens),
            "total_tokens": calculate_token_stats(total_tokens),
            "invariants": {
                "max_allowed_prompt": 1200,
                "max_allowed_completion": 600,
                "max_allowed_context": 2048,
                "violations": 0,
            },
        },
        "verification_summary": {
            "schema_compliance_rate": 1.0,
            "evidence_grounding_rate": 1.0,
            "edit_applicability_rate": 1.0,
            "markdown_fence_violations": 0,
            "all_verified": True,
        },
    }

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate, validate, and partition instruction-tuning datasets for local SLM fine-tuning."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Target output directory for jsonl and manifest (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=TOTAL_SAMPLES_TARGET,
        help=f"Number of samples to generate (default: {TOTAL_SAMPLES_TARGET})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random generator seed for determinism (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing dataset in output directory without re-generating.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose informational logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    output_dir = args.output_dir.resolve()

    if args.verify_only:
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.is_file():
            logger.error("Manifest not found at %s", manifest_path)
            return 1

        logger.info("Verifying existing dataset at %s...", output_dir)
        pairs: list[InstructionPair] = []
        for split_file in ["train.jsonl", "val.jsonl", "test.jsonl"]:
            p = output_dir / split_file
            if not p.is_file():
                logger.error("Missing split file: %s", p)
                return 1
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        pairs.append(InstructionPair.model_validate_json(line))

        token_counter = QwenTokenCounter()
        is_valid, errors = validate_dataset(pairs, token_counter)
        if not is_valid:
            logger.error("Verification failed with %d errors:", len(errors))
            for err in errors[:10]:
                logger.error("  - %s", err)
            return 1
        logger.info("All %d pairs passed strict verification!", len(pairs))
        return 0

    # Generate dataset
    generator = DatasetGenerator(seed=args.seed)
    pairs = generator.generate_all_pairs(total_target=args.num_samples)

    # Multi-stage validation
    logger.info("Executing multi-stage validation on %d pairs...", len(pairs))
    is_valid, errors = validate_dataset(pairs, generator.token_counter)
    if not is_valid:
        logger.error("Dataset validation failed with %d errors:", len(errors))
        for err in errors[:20]:
            logger.error("  - %s", err)
        return 1

    logger.info("100% of samples passed Pydantic schemas, grounding, and edit checks.")

    # Stratified 80/10/10 split
    train_set, val_set, test_set = create_stratified_splits(pairs, seed=args.seed)
    logger.info(
        "Split into Train: %d (%.1f%%), Val: %d (%.1f%%), Test: %d (%.1f%%)",
        len(train_set),
        len(train_set) / len(pairs) * 100,
        len(val_set),
        len(val_set) / len(pairs) * 100,
        len(test_set),
        len(test_set) / len(pairs) * 100,
    )

    manifest = write_dataset_files(output_dir, train_set, val_set, test_set)
    logger.info("Dataset files and manifest written to %s", output_dir)
    logger.info(
        "Token summary: prompt mean=%.1f (max=%d), completion mean=%.1f (max=%d), total mean=%.1f (max=%d)",
        manifest["token_statistics"]["prompt_tokens"]["mean"],
        manifest["token_statistics"]["prompt_tokens"]["max"],
        manifest["token_statistics"]["completion_tokens"]["mean"],
        manifest["token_statistics"]["completion_tokens"]["max"],
        manifest["token_statistics"]["total_tokens"]["mean"],
        manifest["token_statistics"]["total_tokens"]["max"],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
