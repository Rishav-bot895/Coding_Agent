"""Unit tests for LanguageAdapter abstract contract, PythonAdapter, and Orchestrator.

Verifies:
1. LanguageAdapter cannot be instantiated directly (abstract class).
2. Incomplete subclasses missing any abstract property/method cannot be instantiated.
3. Complete MockAdapter can be instantiated and injected into Orchestrator.
4. Orchestrator routes all analysis and validation flows through the LanguageAdapter contract.
5. AdapterRegistry registers, retrieves, lists, and detects adapters.
6. Orchestrator gracefully handles unsupported languages via UnsupportedLanguageError.
7. PythonAdapter skeleton satisfies the complete LanguageAdapter contract.
8. Round-trip serialization of new language and execution schemas.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from localdev.agent.orchestrator import Orchestrator
from localdev.errors import UnsupportedLanguageError
from localdev.languages.base import (
    AdapterRegistry,
    LanguageAdapter,
    get_default_registry,
)
from localdev.languages.python.adapter import PythonAdapter
from localdev.schemas import (
    ASTClassFact,
    ASTFacts,
    ASTFunctionFact,
    ComplexityClassEnum,
    ComplexityReport,
    ConfidenceEnum,
    DetectionConfidence,
    DetectionResult,
    DiagnosticRecord,
    ExecutionResult,
    ExecutionSpec,
    LanguageCapabilities,
    SeverityEnum,
    TargetRecord,
    ValidationLevel,
    ValidationReport,
)

# =============================================================================
# Mock Adapters for Contract Testing
# =============================================================================


class IncompleteAdapter(LanguageAdapter):
    """Adapter missing required abstract methods/properties."""

    @property
    def name(self) -> str:
        return "incomplete"


class MockAdapter(LanguageAdapter):
    """Fully compliant mock adapter recording invocations for orchestrator testing."""

    def __init__(
        self,
        name: str = "mock_lang",
        extensions: tuple[str, ...] = (".mock", ".ml"),
    ) -> None:
        self._name = name
        self._extensions = extensions
        self.calls: list[tuple[str, dict[str, object]]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return self._extensions

    @property
    def capabilities(self) -> LanguageCapabilities:
        return LanguageCapabilities(
            supports_syntax_check=True,
            supports_ast_facts=True,
            supports_diagnostics=False,
            supports_execution=True,
            supports_complexity=False,
            supports_validation=True,
        )

    def detect_confidence(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> DetectionResult:
        self.calls.append(("detect_confidence", {"target": target, "source_text": source_text}))
        if any(target.path.endswith(ext) for ext in self._extensions):
            return DetectionResult(
                language=self.name,
                confidence=DetectionConfidence.CERTAIN,
                reasons=["mock_extension_match"],
                matched_extension=".mock",
                has_shebang=False,
            )
        return DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=["no_mock_match"],
            matched_extension=None,
            has_shebang=False,
        )

    def check_syntax(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        self.calls.append(("check_syntax", {"target": target, "source_text": source_text}))
        if "syntax_error" in (source_text or ""):
            return [
                DiagnosticRecord(
                    source="mock_linter",
                    code="M001",
                    message="Mock syntax error",
                    severity=SeverityEnum.ERROR,
                    start_line=1,
                    start_col=1,
                    end_line=1,
                    end_col=5,
                    fix_available=False,
                )
            ]
        return []

    def extract_ast_facts(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> ASTFacts:
        self.calls.append(("extract_ast_facts", {"target": target, "source_text": source_text}))
        return ASTFacts(
            functions=[
                ASTFunctionFact(
                    name="mock_func",
                    qualified_name="mock_func",
                    start_line=1,
                    end_line=3,
                    parameters=["x"],
                    is_async=False,
                    is_method=False,
                    docstring="Mock function docstring",
                )
            ],
            classes=[
                ASTClassFact(
                    name="MockClass",
                    start_line=5,
                    end_line=10,
                    methods=["mock_method"],
                )
            ],
            total_lines=10,
            syntax_error=None,
        )

    def run_diagnostics(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        self.calls.append(("run_diagnostics", {"target": target, "source_text": source_text}))
        return [
            DiagnosticRecord(
                source="mock_checker",
                code="W100",
                message="Mock warning",
                severity=SeverityEnum.WARNING,
                start_line=2,
                start_col=1,
                end_line=2,
                end_col=10,
                fix_available=True,
            )
        ]

    def prepare_execution(
        self,
        target: TargetRecord,
        args: list[str] | None = None,
    ) -> ExecutionSpec:
        self.calls.append(("prepare_execution", {"target": target, "args": args}))
        return ExecutionSpec(
            command_args=["mock_runner", target.absolute_path, *(args or [])],
            env_overrides={"MOCK_ENV": "1"},
            cwd="/mock/cwd",
        )

    def analyze_complexity(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        selector: str | None = None,
    ) -> ComplexityReport:
        self.calls.append(
            (
                "analyze_complexity",
                {"target": target, "source_text": source_text, "selector": selector},
            )
        )
        return ComplexityReport(
            target=target.path,
            time_complexity=ComplexityClassEnum.O_N,
            auxiliary_space=ComplexityClassEnum.O_1,
            output_space=ComplexityClassEnum.O_N,
            confidence=ConfidenceEnum.HIGH,
            is_amortized=False,
            is_expected=False,
            assumptions=["Mock linear cost"],
            abstention_reason=None,
            details="Mock complexity derivation",
        )

    def validate_candidate(
        self,
        target: TargetRecord,
        candidate_path: Path | str,
        baseline_result: ExecutionResult | None = None,
        expected_stdout: str | None = None,
        expected_stdout_contains: str | None = None,
        expected_exit: int | None = None,
        **kwargs: object,
    ) -> ValidationReport:
        self.calls.append(
            (
                "validate_candidate",
                {
                    "target": target,
                    "candidate_path": candidate_path,
                    "baseline_result": baseline_result,
                    "expected_stdout": expected_stdout,
                    "expected_stdout_contains": expected_stdout_contains,
                    "expected_exit": expected_exit,
                },
            )
        )
        return ValidationReport(
            level_achieved=ValidationLevel.LEVEL_A,
            static_valid=True,
            failure_reproduction_removed=False,
            clean_execution=False,
            behavioral_oracle_passed=None,
            details={"mock": "validated"},
        )


# =============================================================================
# Helper Fixtures
# =============================================================================


def _make_dummy_target(path: str = "app.mock") -> TargetRecord:
    return TargetRecord(
        path=path,
        absolute_path=f"C:\\workspace\\{path}",
        file_size_bytes=100,
        sha256="0" * 64,
        encoding="utf-8",
        has_bom=False,
        newline_style="\n",
        has_trailing_newline=True,
        is_read_only=False,
        is_reparse_point=False,
    )


# =============================================================================
# Tests: LanguageAdapter Abstract Contract
# =============================================================================


def test_language_adapter_cannot_be_instantiated() -> None:
    """Verify that LanguageAdapter is an abstract class that cannot be instantiated directly."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class LanguageAdapter"):
        LanguageAdapter()  # type: ignore[abstract]


def test_incomplete_adapter_subclass_cannot_be_instantiated() -> None:
    """Verify that an adapter missing abstract methods/properties cannot be instantiated."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class IncompleteAdapter"):
        IncompleteAdapter()  # type: ignore[abstract]


# =============================================================================
# Tests: Orchestrator with Injected Mock Adapter
# =============================================================================


def test_orchestrator_routes_detection_via_adapter() -> None:
    """Verify orchestrator detect() delegates to injected adapter without language coupling."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target("sample.mock")

    result = orchestrator.detect(target)
    assert result.confidence == DetectionConfidence.CERTAIN
    assert result.language == "mock_lang"
    assert len(mock.calls) == 1
    assert mock.calls[0][0] == "detect_confidence"


def test_orchestrator_routes_check_syntax_via_adapter() -> None:
    """Verify orchestrator check_syntax() delegates to injected adapter."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target()

    # Valid syntax
    diags = orchestrator.check_syntax(target, source_text="clean code")
    assert diags == []

    # Syntax error
    diags_err = orchestrator.check_syntax(target, source_text="syntax_error here")
    assert len(diags_err) == 1
    assert diags_err[0].code == "M001"
    assert diags_err[0].severity == SeverityEnum.ERROR


def test_orchestrator_routes_ast_facts_via_adapter() -> None:
    """Verify orchestrator extract_ast_facts() delegates to injected adapter."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target()

    facts = orchestrator.extract_ast_facts(target)
    assert len(facts.functions) == 1
    assert facts.functions[0].name == "mock_func"
    assert len(facts.classes) == 1
    assert facts.classes[0].name == "MockClass"


def test_orchestrator_routes_diagnostics_via_adapter() -> None:
    """Verify orchestrator run_diagnostics() delegates to injected adapter."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target()

    diags = orchestrator.run_diagnostics(target)
    assert len(diags) == 1
    assert diags[0].code == "W100"


def test_orchestrator_routes_execution_prep_via_adapter() -> None:
    """Verify orchestrator prepare_execution() delegates to injected adapter."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target()

    spec = orchestrator.prepare_execution(target, args=["--test", "val"])
    assert spec.command_args[0] == "mock_runner"
    assert spec.command_args[-1] == "val"
    assert spec.env_overrides == {"MOCK_ENV": "1"}


def test_orchestrator_routes_complexity_via_adapter() -> None:
    """Verify orchestrator analyze_complexity() delegates to injected adapter."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target()

    report = orchestrator.analyze_complexity(target, selector="my_func")
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.confidence == ConfidenceEnum.HIGH


def test_orchestrator_routes_validation_via_adapter() -> None:
    """Verify orchestrator validate_candidate() delegates to injected adapter."""
    mock = MockAdapter()
    orchestrator = Orchestrator(adapter=mock)
    target = _make_dummy_target()

    val_report = orchestrator.validate_candidate(target, candidate_path="candidate.mock")
    assert val_report.level_achieved == ValidationLevel.LEVEL_A
    assert val_report.static_valid is True


# =============================================================================
# Tests: Adapter Registry
# =============================================================================


def test_adapter_registry_registration_and_lookup() -> None:
    """Verify AdapterRegistry registers and retrieves adapters with case insensitivity."""
    registry = AdapterRegistry()
    mock = MockAdapter(name="Ruby", extensions=(".rb",))

    registry.register(mock)
    assert registry.get("ruby") is mock
    assert registry.get("RUBY") is mock
    assert registry.get("unknown") is None
    assert registry.list_adapters() == ["ruby"]


def test_adapter_registry_rejects_invalid_type() -> None:
    """Verify AdapterRegistry raises TypeError when registering non-adapter objects."""
    registry = AdapterRegistry()
    with pytest.raises(TypeError, match="Expected LanguageAdapter instance"):
        registry.register("not an adapter")  # type: ignore[arg-type]


def test_adapter_registry_detection() -> None:
    """Verify detect_adapter finds matching adapter by target evaluation."""
    registry = AdapterRegistry()
    mock_ruby = MockAdapter(name="ruby", extensions=(".rb",))
    registry.register(mock_ruby)

    target_rb = _make_dummy_target("script.rb")
    adapter, result = registry.detect_adapter(target_rb)
    assert adapter is mock_ruby
    assert result.confidence == DetectionConfidence.CERTAIN

    target_unsupported = _make_dummy_target("image.png")
    adapter_none, result_unsupp = registry.detect_adapter(target_unsupported)
    assert adapter_none is None
    assert result_unsupp.confidence == DetectionConfidence.UNSUPPORTED


def test_orchestrator_unsupported_language_raises_abstention() -> None:
    """Verify orchestrator raises UnsupportedLanguageError when target language cannot be resolved."""
    registry = AdapterRegistry()  # empty registry
    orchestrator = Orchestrator(registry=registry)
    target = _make_dummy_target("unknown.xyz")

    with pytest.raises(UnsupportedLanguageError) as exc_info:
        orchestrator.resolve_adapter(target)
    assert exc_info.value.reason_code == "UNSUPPORTED_LANGUAGE"
    from localdev.constants import EXIT_ABSTENTION
    assert exc_info.value.exit_code == EXIT_ABSTENTION


# =============================================================================
# Tests: PythonAdapter Implementation Skeleton
# =============================================================================


def test_python_adapter_properties() -> None:
    """Verify PythonAdapter identity and capability declarations."""
    adapter = PythonAdapter()
    assert adapter.name == "python"
    assert adapter.file_extensions == (".py", ".pyw")
    caps = adapter.capabilities
    assert caps.supports_syntax_check is True
    assert caps.supports_ast_facts is True
    assert caps.supports_diagnostics is True
    assert caps.supports_execution is True
    assert caps.supports_complexity is True
    assert caps.supports_validation is True


def test_python_adapter_detect_extension_and_ast() -> None:
    """Verify PythonAdapter detect_confidence on extension and ast parse."""
    adapter = PythonAdapter()

    # Valid Python file with clean code
    target_clean = _make_dummy_target("main.py")
    res_clean = adapter.detect_confidence(target_clean, source_text="x = 1\nprint(x)\n")
    assert res_clean.confidence == DetectionConfidence.CERTAIN
    assert res_clean.language == "python"

    # Python file with syntax error -> PROBABLE
    target_broken = _make_dummy_target("broken.py")
    res_broken = adapter.detect_confidence(target_broken, source_text="def broken(\n")
    assert res_broken.confidence == DetectionConfidence.PROBABLE
    assert res_broken.language == "python"

    # Extensionless file with python shebang -> CERTAIN
    target_shebang = _make_dummy_target("myscript")
    res_shebang = adapter.detect_confidence(
        target_shebang, source_text="#!/usr/bin/env python3\nprint('hello')\n"
    )
    assert res_shebang.confidence == DetectionConfidence.CERTAIN
    assert res_shebang.has_shebang is True

    # Unsupported non-Python file
    target_other = _make_dummy_target("readme.md")
    res_other = adapter.detect_confidence(target_other, source_text="# Readme\nJust markdown\n")
    assert res_other.confidence == DetectionConfidence.UNSUPPORTED


def test_python_adapter_syntax_check() -> None:
    """Verify PythonAdapter check_syntax extracts syntax errors without executing."""
    adapter = PythonAdapter()
    target = _make_dummy_target("test.py")

    # Clean code
    diags = adapter.check_syntax(target, source_text="def foo():\n    return 42\n")
    assert diags == []

    # Syntax error
    diags_err = adapter.check_syntax(target, source_text="def foo(\n")
    assert len(diags_err) == 1
    assert diags_err[0].code == "SyntaxError"
    assert diags_err[0].severity == SeverityEnum.ERROR
    assert diags_err[0].start_line >= 1


def test_python_adapter_ast_facts() -> None:
    """Verify PythonAdapter extract_ast_facts parses functions and classes."""
    adapter = PythonAdapter()
    target = _make_dummy_target("module.py")
    source = (
        "def top_func(a, b):\n"
        "    '''A top function.'''\n"
        "    return a + b\n\n"
        "class Calculator:\n"
        "    def add(self, x):\n"
        "        return x\n"
    )
    facts = adapter.extract_ast_facts(target, source_text=source)
    assert facts.syntax_error is None
    assert len(facts.functions) == 2
    assert facts.functions[0].name == "top_func"
    assert facts.functions[0].parameters == ["a", "b"]
    assert facts.functions[0].docstring == "A top function."
    assert facts.functions[1].name == "add"
    assert facts.functions[1].qualified_name == "Calculator.add"
    assert facts.functions[1].is_method is True
    assert len(facts.classes) == 1
    assert facts.classes[0].name == "Calculator"


def test_python_adapter_prepare_execution() -> None:
    """Verify PythonAdapter prepare_execution isolates flags with -E -B -P."""
    adapter = PythonAdapter()
    target = _make_dummy_target("run.py")

    spec = adapter.prepare_execution(target, args=["--flag", "1"])
    assert spec.command_args[0] == sys.executable
    assert "-E" in spec.command_args
    assert "-B" in spec.command_args
    assert "-P" in spec.command_args
    assert target.absolute_path in spec.command_args
    assert spec.command_args[-2:] == ["--flag", "1"]


def test_python_adapter_in_default_registry() -> None:
    """Verify default registry automatically contains PythonAdapter."""
    registry = get_default_registry()
    adapter = registry.get("python")
    assert adapter is not None
    assert isinstance(adapter, PythonAdapter)


# =============================================================================
# Tests: Language Schema Roundtrip
# =============================================================================


def test_language_schemas_roundtrip() -> None:
    """Verify DetectionResult, LanguageCapabilities, and ExecutionSpec serialize correctly."""
    det = DetectionResult(
        language="python",
        confidence=DetectionConfidence.CERTAIN,
        reasons=["extension_match"],
        matched_extension=".py",
        has_shebang=False,
    )
    det_json = det.model_dump_json()
    assert DetectionResult.model_validate_json(det_json) == det

    caps = LanguageCapabilities(
        supports_syntax_check=True,
        supports_ast_facts=True,
        supports_diagnostics=False,
        supports_execution=True,
        supports_complexity=False,
        supports_validation=True,
    )
    caps_json = caps.model_dump_json()
    assert LanguageCapabilities.model_validate_json(caps_json) == caps

    spec = ExecutionSpec(
        command_args=["python", "-E", "-B", "-P", "script.py"],
        env_overrides={"VAR": "val"},
        cwd="C:\\test",
    )
    spec_json = spec.model_dump_json()
    assert ExecutionSpec.model_validate_json(spec_json) == spec
