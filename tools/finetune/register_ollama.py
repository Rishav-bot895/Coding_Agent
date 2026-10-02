"""Local Ollama registration, verification, and inference testing (Phase 13 Task P13-T3).

Enforces:
- Automated model creation in local Ollama daemon via `ollama create <name> -f <modelfile>`.
- Verification of local model availability via OllamaClient and `/api/tags` / `/api/show`.
- Structured schema validation test ensuring registered model emits valid DiagnosisRecord.
- Strict project-local storage referencing without remote network calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Final

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx

from localdev.config import get_default_config
from localdev.inference.ollama_client import OllamaClient
from localdev.inference.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    UNTRUSTED_CODE_END,
    UNTRUSTED_CODE_START,
    sanitize_untrusted_code,
)
from localdev.schemas import DiagnosisRecord

logger = logging.getLogger("register_ollama")

DEFAULT_MODEL_NAME: Final[str] = "localdev-qwen-coder:3b"
DEFAULT_MODELFILE: Final[Path] = _PROJECT_ROOT / "models" / "finetune" / "Modelfile"


def register_model_with_ollama(
    model_name: str,
    modelfile_path: Path,
    quantize: str | None = None,
    ollama_cmd: str = "ollama",
) -> dict[str, Any]:
    """Register a fine-tuned model into the local Ollama daemon via `ollama create`.

    Args:
        model_name: Identifier for the registered model (e.g. 'localdev-qwen-coder:3b').
        modelfile_path: Path to the generated Modelfile.
        quantize: Optional quantization flag (e.g. 'q4_K_M' for safetensors imports).
        ollama_cmd: Ollama executable command/path.

    Returns:
        Dictionary containing registration status and execution metrics.

    Raises:
        FileNotFoundError: If Modelfile or ollama executable is missing.
        RuntimeError: If ollama create exits with non-zero status.
    """
    if not modelfile_path.is_file():
        raise FileNotFoundError(f"Modelfile not found at: {modelfile_path}")

    executable = shutil.which(ollama_cmd)
    if not executable:
        raise FileNotFoundError(
            f"Ollama executable '{ollama_cmd}' not found on PATH. "
            "Ensure Ollama is installed and running locally."
        )

    cmd = [executable, "create", model_name, "-f", str(modelfile_path.resolve())]
    if quantize:
        cmd.extend(["-q", quantize])

    logger.info("Executing Ollama registration command: %s", " ".join(cmd))
    t0 = time.perf_counter()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(modelfile_path.parent),
    )
    duration = time.perf_counter() - t0

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        logger.error("Ollama registration failed (exit %d): %s", result.returncode, error_msg)
        raise RuntimeError(f"Ollama registration failed: {error_msg}")

    logger.info("Successfully registered model '%s' in %.2f seconds.", model_name, duration)
    return {
        "success": True,
        "model_name": model_name,
        "duration_sec": round(duration, 2),
        "modelfile": str(modelfile_path),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def verify_ollama_registration(
    model_name: str,
    ollama_url: str = "http://127.0.0.1:11434",
    timeout: float = 5.0,
    client: OllamaClient | None = None,
) -> dict[str, Any]:
    """Verify that model is registered and inspect its parameters via Ollama API.

    Args:
        model_name: Name of model to verify.
        ollama_url: Local Ollama daemon base URL.
        timeout: Request timeout in seconds.
        client: Optional pre-configured OllamaClient.

    Returns:
        Dictionary containing verification results and model details.
    """
    cfg = get_default_config()
    inf_client = client or OllamaClient(config=cfg)

    if not inf_client.is_available():
        raise ConnectionError(f"Cannot reach local Ollama daemon at {ollama_url}")

    available_models = inf_client.list_models()
    normalized_target = model_name if ":" in model_name else f"{model_name}:latest"
    base_target = model_name.split(":")[0]

    is_registered = any(
        m == model_name or m == normalized_target or m.startswith(f"{base_target}:")
        for m in available_models
    )

    if not is_registered:
        return {
            "is_available": True,
            "is_registered": False,
            "model_name": model_name,
            "available_models": available_models,
            "error": f"Model '{model_name}' not found in Ollama model list.",
        }

    # Query /api/show for model details
    details: dict[str, Any] = {}
    show_url = f"{ollama_url.rstrip('/')}/api/show"
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(show_url, json={"name": model_name})
            if resp.is_success:
                data = resp.json()
                details = {
                    "format": data.get("details", {}).get("format"),
                    "family": data.get("details", {}).get("family"),
                    "parameter_size": data.get("details", {}).get("parameter_size"),
                    "quantization_level": data.get("details", {}).get("quantization_level"),
                    "modified_at": data.get("modified_at"),
                }
    except Exception as exc:
        logger.warning("Could not query /api/show for model '%s': %s", model_name, exc)

    return {
        "is_available": True,
        "is_registered": True,
        "model_name": model_name,
        "details": details,
        "available_models": available_models,
    }


def smoke_test_structured_inference(
    model_name: str,
    ollama_url: str = "http://127.0.0.1:11434",
    client: OllamaClient | None = None,
) -> dict[str, Any]:
    """Test end-to-end structured JSON schema generation using the registered model."""
    cfg = get_default_config()
    cfg.active_model = model_name
    cfg.ollama_url = ollama_url
    inf_client = client or OllamaClient(config=cfg)

    # Build concise diagnosis prompt
    test_code = "def divide(a, b):\n    return a / b\n"
    user_prompt = (
        "TARGET FILE: math_ops.py\n"
        "AVAILABLE EVIDENCE MANIFEST:\n"
        "- [runtime:ZeroDivisionError] division by zero at line 2\n"
        "- [traceback:line_2] Top target traceback frame at line 2\n"
        "PRIMARY FAILURE SIGNATURE:\n"
        "ZeroDivisionError: division by zero\n\n"
        f"{UNTRUSTED_CODE_START}\n"
        f"{sanitize_untrusted_code(test_code)}\n"
        f"{UNTRUSTED_CODE_END}\n"
    )

    logger.info("Executing structured inference smoke test against '%s'...", model_name)
    record, metadata = inf_client.generate_structured(
        prompt=user_prompt,
        schema=DiagnosisRecord,
        system_prompt=DIAGNOSIS_SYSTEM_PROMPT,
        model=model_name,
    )

    logger.info("Structured inference passed! Diagnosed: %s", record.bug_description)
    return {
        "success": True,
        "model": model_name,
        "record": record.model_dump(),
        "metadata": metadata.model_dump(),
    }


def run_registration_smoke_test(
    modelfile_path: Path | None = None,
    test_model_name: str = "localdev-smoke-test:latest",
) -> dict[str, Any]:
    """Execute rapid smoke test verifying registration, listing, and cleanup."""
    # Use existing Modelfile if present, or create temporary test Modelfile
    target_modelfile = modelfile_path
    temp_modelfile = False

    if target_modelfile is None or not target_modelfile.is_file():
        temp_dir = _PROJECT_ROOT / "models" / "finetune" / "smoke_export"
        target_modelfile = temp_dir / "Modelfile_test"
        target_modelfile.parent.mkdir(parents=True, exist_ok=True)
        # Use existing local base model for instant creation test
        target_modelfile.write_text(
            "FROM qwen2.5-coder:1.5b-instruct-q4_K_M\n"
            "PARAMETER temperature 0.2\n"
            "PARAMETER top_p 0.95\n"
            "PARAMETER num_ctx 2048\n",
            encoding="utf-8",
        )
        temp_modelfile = True

    try:
        # 1. Register model
        reg_res = register_model_with_ollama(
            model_name=test_model_name,
            modelfile_path=target_modelfile,
        )

        # 2. Verify model
        verify_res = verify_ollama_registration(model_name=test_model_name)
        if not verify_res["is_registered"]:
            raise RuntimeError(f"Model {test_model_name} failed registration check")

        # 3. Test structured inference
        inf_res = smoke_test_structured_inference(model_name=test_model_name)

        return {
            "status": "success",
            "registration": reg_res,
            "verification": verify_res,
            "inference": inf_res,
        }
    finally:
        # Clean up test model from local Ollama daemon
        subprocess.run(["ollama", "rm", test_model_name], capture_output=True)
        if temp_modelfile and target_modelfile.is_file():
            target_modelfile.unlink(missing_ok=True)


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register and verify fine-tuned models in local Ollama daemon."
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=DEFAULT_MODEL_NAME,
        help="Model tag to register in Ollama (e.g. localdev-qwen-coder:3b).",
    )
    parser.add_argument(
        "--modelfile",
        type=Path,
        default=DEFAULT_MODELFILE,
        help="Path to Modelfile.",
    )
    parser.add_argument(
        "--quantize",
        type=str,
        default=None,
        help="Quantization precision for safetensors imports (e.g. q4_K_M).",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Skip registration and verify existing model.",
    )
    parser.add_argument(
        "--test-inference",
        action="store_true",
        help="Execute structured inference test after registration.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run end-to-end registration, verification, and inference smoke test.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.smoke_test:
        logger.info("Executing Ollama registration smoke test...")
        smoke_res = run_registration_smoke_test(
            modelfile_path=args.modelfile if args.modelfile.is_file() else None,
        )
        logger.info("Registration smoke test passed: %s", smoke_res["status"])
        return 0

    if args.verify_only:
        report = verify_ollama_registration(args.model_name)
        logger.info("Verification result: %s", json.dumps(report, indent=2))
        return 0 if report.get("is_registered") else 1

    # Registration workflow
    reg_report = register_model_with_ollama(
        model_name=args.model_name,
        modelfile_path=args.modelfile,
        quantize=args.quantize,
    )
    logger.info("Registration report: %s", reg_report)

    verify_report = verify_ollama_registration(args.model_name)
    logger.info("Verification report: %s", verify_report)

    if args.test_inference:
        inf_report = smoke_test_structured_inference(args.model_name)
        logger.info("Inference report: %s", inf_report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
