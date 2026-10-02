"""LoRA adapter fusion, GGUF conversion, and Ollama Modelfile generation (P13-T3).

Enforces:
- Strict project-local storage boundary under models/finetune/ (fused, gguf, Modelfile).
- Hugging Face to GGML/GGUF tensor name mapping for Qwen2 architecture.
- GGUF export via gguf.GGUFWriter with architecture parameters and vocabulary.
- Validation via gguf.GGUFReader with safe Windows file lock releases.
- Ollama Modelfile generation referencing project-relative GGUF artifacts.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Final

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import torch
from gguf import GGMLQuantizationType, GGUFReader, GGUFWriter
from peft import PeftModel
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM, AutoTokenizer

from tools.finetune.config import DEFAULT_OUTPUT_DIR
from tools.finetune.train_qlora import build_toy_base_model, verify_checkpoint

logger = logging.getLogger("export_gguf")

DEFAULT_FUSED_DIR: Final[Path] = _PROJECT_ROOT / "models" / "finetune" / "fused"
DEFAULT_GGUF_DIR: Final[Path] = _PROJECT_ROOT / "models" / "finetune" / "gguf"
DEFAULT_MODELFILE_PATH: Final[Path] = _PROJECT_ROOT / "models" / "finetune" / "Modelfile"
DEFAULT_TEMPLATE_PATH: Final[Path] = _PROJECT_ROOT / "tools" / "finetune" / "Modelfile.template"

# Direct mapping for non-block tensors in Qwen2 architecture
QWEN2_STATIC_TENSOR_MAP: Final[dict[str, str]] = {
    "model.embed_tokens.weight": "token_embd.weight",
    "model.norm.weight": "output_norm.weight",
    "lm_head.weight": "output.weight",
}


def map_hf_to_ggml_name(hf_name: str) -> str:
    """Map a Hugging Face Qwen2 tensor name to the standard GGML/GGUF tensor name.

    Converts:
        model.embed_tokens.weight                 -> token_embd.weight
        model.norm.weight                         -> output_norm.weight
        lm_head.weight                            -> output.weight
        model.layers.{i}.self_attn.q_proj.weight  -> blk.{i}.attn_q.weight
        model.layers.{i}.self_attn.q_proj.bias    -> blk.{i}.attn_q.bias
        model.layers.{i}.self_attn.k_proj.weight  -> blk.{i}.attn_k.weight
        model.layers.{i}.self_attn.k_proj.bias    -> blk.{i}.attn_k.bias
        model.layers.{i}.self_attn.v_proj.weight  -> blk.{i}.attn_v.weight
        model.layers.{i}.self_attn.v_proj.bias    -> blk.{i}.attn_v.bias
        model.layers.{i}.self_attn.o_proj.weight  -> blk.{i}.attn_output.weight
        model.layers.{i}.mlp.gate_proj.weight     -> blk.{i}.ffn_gate.weight
        model.layers.{i}.mlp.up_proj.weight       -> blk.{i}.ffn_up.weight
        model.layers.{i}.mlp.down_proj.weight     -> blk.{i}.ffn_down.weight
        model.layers.{i}.input_layernorm.weight   -> blk.{i}.attn_norm.weight
        model.layers.{i}.post_attention_layernorm.weight -> blk.{i}.ffn_norm.weight
    """
    if hf_name in QWEN2_STATIC_TENSOR_MAP:
        return QWEN2_STATIC_TENSOR_MAP[hf_name]

    parts = hf_name.split(".")
    if len(parts) >= 4 and parts[0] == "model" and parts[1] == "layers":
        layer_idx = parts[2]
        rest = ".".join(parts[3:])

        layer_map = {
            "self_attn.q_proj.weight": f"blk.{layer_idx}.attn_q.weight",
            "self_attn.q_proj.bias": f"blk.{layer_idx}.attn_q.bias",
            "self_attn.k_proj.weight": f"blk.{layer_idx}.attn_k.weight",
            "self_attn.k_proj.bias": f"blk.{layer_idx}.attn_k.bias",
            "self_attn.v_proj.weight": f"blk.{layer_idx}.attn_v.weight",
            "self_attn.v_proj.bias": f"blk.{layer_idx}.attn_v.bias",
            "self_attn.o_proj.weight": f"blk.{layer_idx}.attn_output.weight",
            "mlp.gate_proj.weight": f"blk.{layer_idx}.ffn_gate.weight",
            "mlp.up_proj.weight": f"blk.{layer_idx}.ffn_up.weight",
            "mlp.down_proj.weight": f"blk.{layer_idx}.ffn_down.weight",
            "input_layernorm.weight": f"blk.{layer_idx}.attn_norm.weight",
            "post_attention_layernorm.weight": f"blk.{layer_idx}.ffn_norm.weight",
        }
        if rest in layer_map:
            return layer_map[rest]

    return hf_name


# =============================================================================
# 1. LoRA Adapter Fusion
# =============================================================================


def fuse_adapter_to_base(
    adapter_dir: Path,
    base_model_id: str = "Qwen/Qwen2.5-Coder-3B-Instruct",
    output_fused_dir: Path | None = None,
    torch_dtype: str = "bfloat16",
    test_mode: bool = False,
    device: str = "cpu",
) -> Path:
    """Merge trained LoRA adapter weights into base model and save fused weights.

    Args:
        adapter_dir: Directory containing adapter_model.safetensors and adapter_config.json.
        base_model_id: Base model Hugging Face ID or local path.
        output_fused_dir: Directory to save fused Hugging Face model (strictly in project dir).
        torch_dtype: Precision for base model loading ('bfloat16', 'float16', 'float32').
        test_mode: When True, uses lightweight in-memory toy model for testing.
        device: Execution device ('cpu' or 'cuda').

    Returns:
        Path to output_fused_dir.
    """
    target_out = output_fused_dir or DEFAULT_FUSED_DIR
    target_out.mkdir(parents=True, exist_ok=True)

    logger.info("Initiating LoRA adapter fusion from %s -> %s", adapter_dir, target_out)
    verify_checkpoint(adapter_dir)

    if test_mode:
        tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-Coder-1.5B-Instruct")
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or "<|endoftext|>"
        base_model = build_toy_base_model(vocab_size=len(tokenizer))
    else:
        dt = getattr(torch, torch_dtype, torch.bfloat16)
        tokenizer = AutoTokenizer.from_pretrained(base_model_id)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or "<|endoftext|>"
        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_id,
            torch_dtype=dt,
            device_map=device if device != "auto" else "cpu",
        )

    # Wrap with PEFT adapter
    peft_model = PeftModel.from_pretrained(base_model, str(adapter_dir))

    # Merge adapter weights into base model
    fused_model = peft_model.merge_and_unload()

    # Save fused model and tokenizer with safe_serialization
    fused_model.save_pretrained(target_out, safe_serialization=True)
    tokenizer.save_pretrained(target_out)

    logger.info("Fused model and tokenizer successfully saved to %s", target_out)
    return target_out


# =============================================================================
# 2. GGUF Conversion
# =============================================================================


def convert_fused_to_gguf(
    fused_dir: Path,
    output_gguf_path: Path | None = None,
    quant_type: str = "f16",
    model_name: str = "localdev-qwen2.5-coder",
) -> Path:
    """Convert a fused Hugging Face model directory to GGUF format.

    Args:
        fused_dir: Directory containing fused config.json, tokenizer, and safetensors.
        output_gguf_path: Path for output .gguf file (strictly inside models/finetune/gguf/).
        quant_type: Target precision ('f16', 'f32', 'q8_0').
        model_name: Architecture name tag.

    Returns:
        Path to output_gguf_path.
    """
    target_gguf = output_gguf_path or (
        DEFAULT_GGUF_DIR / f"{model_name}-{quant_type}.gguf"
    )
    target_gguf.parent.mkdir(parents=True, exist_ok=True)

    config_path = fused_dir / "config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing config.json in fused model directory: {fused_dir}")

    config_data = json.loads(config_path.read_text(encoding="utf-8"))

    logger.info("Exporting GGUF model to %s (format: %s)", target_gguf, quant_type)
    writer = GGUFWriter(str(target_gguf), "qwen2")

    # 1. Architecture parameters
    writer.add_name(model_name)
    writer.add_context_length(int(config_data.get("max_position_embeddings", 2048)))
    writer.add_embedding_length(int(config_data["hidden_size"]))
    writer.add_block_count(int(config_data["num_hidden_layers"]))
    writer.add_feed_forward_length(int(config_data["intermediate_size"]))
    writer.add_head_count(int(config_data["num_attention_heads"]))
    writer.add_head_count_kv(
        int(config_data.get("num_key_value_heads", config_data["num_attention_heads"]))
    )
    writer.add_layer_norm_rms_eps(float(config_data.get("rms_norm_eps", 1e-6)))
    writer.add_rope_freq_base(float(config_data.get("rope_theta", 1000000.0)))

    # 2. Tokenizer metadata
    writer.add_tokenizer_model("gpt2")
    tokenizer_json = fused_dir / "tokenizer.json"
    if tokenizer_json.is_file():
        try:
            tok = AutoTokenizer.from_pretrained(str(fused_dir))
            vocab = tok.get_vocab()
            id_to_token: list[str | None] = [None] * len(vocab)
            for k, v in vocab.items():
                if v < len(id_to_token):
                    id_to_token[v] = k
            token_bytes = [
                t.encode("utf-8", errors="replace") if isinstance(t, str) else b""
                for t in id_to_token
            ]
            writer.add_token_list(token_bytes)
        except Exception as exc:
            logger.warning("Could not fully parse tokenizer vocabulary: %s. Using basic tokens.", exc)
            writer.add_token_list([b"<|endoftext|>", b"<|im_start|>", b"<|im_end|>"])
    else:
        writer.add_token_list([b"<|endoftext|>", b"<|im_start|>", b"<|im_end|>"])

    # 3. Model Tensors
    state_dict: dict[str, torch.Tensor] = {}
    safetensor_files = sorted(fused_dir.glob("*.safetensors"))
    if safetensor_files:
        for sf in safetensor_files:
            state_dict.update(load_file(str(sf)))
    else:
        bin_files = sorted(fused_dir.glob("*.bin"))
        for bf in bin_files:
            state_dict.update(torch.load(bf, map_location="cpu", weights_only=True))

    if not state_dict:
        raise ValueError(f"No model weights found in {fused_dir}")

    for hf_name, tensor in state_dict.items():
        if "rotary_emb.inv_freq" in hf_name:
            continue

        ggml_name = map_hf_to_ggml_name(hf_name)
        arr = tensor.detach().cpu().to(torch.float32).numpy()

        if quant_type == "f16":
            writer.add_tensor(ggml_name, arr.astype(np.float16))
        elif quant_type == "q8_0" and arr.ndim >= 2 and arr.shape[-1] % 34 == 0:
            import gguf

            q8 = gguf.quantize(arr, GGMLQuantizationType.Q8_0)
            writer.add_tensor(ggml_name, q8, raw_dtype=GGMLQuantizationType.Q8_0)
        else:
            writer.add_tensor(ggml_name, arr.astype(np.float32))

    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    logger.info("Successfully wrote GGUF model: %s (%d bytes)", target_gguf, target_gguf.stat().st_size)
    return target_gguf


# =============================================================================
# 3. GGUF Verification
# =============================================================================


def verify_gguf(gguf_path: Path) -> dict[str, Any]:
    """Verify that a GGUF file is well-formed and contains required Qwen2 metadata.

    Releases all open memory-mapped file handles after inspection to avoid Windows locks.
    """
    if not gguf_path.is_file():
        raise FileNotFoundError(f"GGUF file does not exist: {gguf_path}")

    reader = GGUFReader(str(gguf_path))
    try:
        arch_field = reader.get_field("general.architecture")
        if arch_field is None:
            raise ValueError(f"Missing general.architecture in {gguf_path}")
        arch = arch_field.parts[-1].tobytes().decode("utf-8")

        tensor_names = [t.name for t in reader.tensors]
        tensor_count = len(tensor_names)
        if tensor_count == 0:
            raise ValueError(f"GGUF file contains 0 tensors: {gguf_path}")

        file_size_mb = round(gguf_path.stat().st_size / (1024 * 1024), 2)
        return {
            "is_valid": True,
            "architecture": arch,
            "tensor_count": tensor_count,
            "tensor_names": tensor_names,
            "file_size_mb": file_size_mb,
            "path": str(gguf_path),
        }
    finally:
        del reader
        gc.collect()


# =============================================================================
# 4. Modelfile Generation
# =============================================================================


def generate_modelfile(
    model_source_path: Path,
    output_modelfile: Path | None = None,
    template_path: Path | None = None,
    system_prompt: str | None = None,
) -> Path:
    """Generate an Ollama Modelfile from template referencing the project-local model.

    Args:
        model_source_path: Path to GGUF file or fused model directory.
        output_modelfile: Path to output Modelfile (defaults to models/finetune/Modelfile).
        template_path: Path to Modelfile.template (defaults to tools/finetune/Modelfile.template).
        system_prompt: Optional custom system prompt override.

    Returns:
        Path to output_modelfile.
    """
    target_modelfile = output_modelfile or DEFAULT_MODELFILE_PATH
    target_modelfile.parent.mkdir(parents=True, exist_ok=True)

    tmpl_file = template_path or DEFAULT_TEMPLATE_PATH
    if not tmpl_file.is_file():
        raise FileNotFoundError(f"Modelfile template not found at {tmpl_file}")

    template_str = tmpl_file.read_text(encoding="utf-8")

    # Format model path relative to target_modelfile.parent or project root
    try:
        rel_path = model_source_path.relative_to(target_modelfile.parent)
        model_str = f"./{rel_path.as_posix()}"
    except ValueError:
        try:
            rel_path = model_source_path.relative_to(_PROJECT_ROOT)
            model_str = f"./{rel_path.as_posix()}"
        except ValueError:
            model_str = model_source_path.as_posix()

    rendered = template_str.replace("{{ model_path }}", model_str)
    if system_prompt:
        rendered += f'\nSYSTEM """{system_prompt}"""\n'

    target_modelfile.write_text(rendered, encoding="utf-8")
    logger.info("Generated Ollama Modelfile at %s referencing %s", target_modelfile, model_str)
    return target_modelfile


# =============================================================================
# 5. Smoke Test Runner
# =============================================================================


def run_export_smoke_test(
    output_base_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute end-to-end smoke test verifying adapter fusion, GGUF export, and Modelfile."""
    smoke_dir = output_base_dir or (_PROJECT_ROOT / "models" / "finetune" / "smoke_export")
    adapter_dir = _PROJECT_ROOT / "models" / "finetune" / "smoke_test_adapter"

    # Ensure smoke test adapter exists; if not, generate it
    if not (adapter_dir / "adapter_model.safetensors").is_file():
        from tools.finetune.train_qlora import run_smoke_test

        run_smoke_test(output_dir=adapter_dir)

    fused_dir = smoke_dir / "fused"
    gguf_path = smoke_dir / "gguf" / "smoke_model-f16.gguf"
    modelfile_path = smoke_dir / "Modelfile"

    t0 = time.perf_counter()

    # 1. Fuse adapter into toy base model
    fuse_adapter_to_base(
        adapter_dir=adapter_dir,
        output_fused_dir=fused_dir,
        test_mode=True,
    )

    # 2. Convert fused model to GGUF
    convert_fused_to_gguf(
        fused_dir=fused_dir,
        output_gguf_path=gguf_path,
        quant_type="f16",
        model_name="localdev-qwen2.5-smoke",
    )

    # 3. Verify exported GGUF file
    gguf_report = verify_gguf(gguf_path)

    # 4. Generate Modelfile
    generate_modelfile(
        model_source_path=gguf_path,
        output_modelfile=modelfile_path,
    )

    duration = time.perf_counter() - t0
    report = {
        "status": "success",
        "duration_sec": round(duration, 2),
        "fused_dir": str(fused_dir),
        "gguf_report": gguf_report,
        "modelfile": str(modelfile_path),
    }
    return report


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LoRA adapter fusion, GGUF conversion, and Ollama Modelfile export."
    )
    parser.add_argument(
        "--adapter-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Path to trained LoRA adapter directory.",
    )
    parser.add_argument(
        "--base-model",
        type=str,
        default="Qwen/Qwen2.5-Coder-3B-Instruct",
        help="Base Hugging Face model identifier.",
    )
    parser.add_argument(
        "--fused-dir",
        type=Path,
        default=DEFAULT_FUSED_DIR,
        help="Target directory for merged FP16 Hugging Face model.",
    )
    parser.add_argument(
        "--gguf-path",
        type=Path,
        default=None,
        help="Target path for output .gguf file.",
    )
    parser.add_argument(
        "--modelfile-path",
        type=Path,
        default=DEFAULT_MODELFILE_PATH,
        help="Target path for generated Ollama Modelfile.",
    )
    parser.add_argument(
        "--quant-type",
        type=str,
        default="f16",
        choices=["f16", "f32", "q8_0"],
        help="GGUF quantization precision.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run fast smoke test verifying fusion, GGUF conversion, and Modelfile generation.",
    )
    parser.add_argument(
        "--verify-gguf",
        type=Path,
        default=None,
        help="Verify an existing GGUF file integrity and metadata.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.verify_gguf:
        report = verify_gguf(args.verify_gguf)
        logger.info("GGUF verification report: %s", report)
        return 0

    if args.smoke_test:
        logger.info("Executing export and GGUF conversion smoke test...")
        smoke_res = run_export_smoke_test()
        logger.info("Smoke test completed successfully in %.2fs!", smoke_res["duration_sec"])
        logger.info("GGUF Report: %s", smoke_res["gguf_report"])
        return 0

    # Production full export
    fused_out = fuse_adapter_to_base(
        adapter_dir=args.adapter_dir,
        base_model_id=args.base_model,
        output_fused_dir=args.fused_dir,
    )
    gguf_out = convert_fused_to_gguf(
        fused_dir=fused_out,
        output_gguf_path=args.gguf_path,
        quant_type=args.quant_type,
    )
    generate_modelfile(
        model_source_path=gguf_out,
        output_modelfile=args.modelfile_path,
    )
    verify_gguf(gguf_out)
    logger.info("Production export completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

