"""Unit tests for LoRA fusion, GGUF export, and Ollama registration (P13-T3)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.finetune.export_gguf import (
    DEFAULT_TEMPLATE_PATH,
    convert_fused_to_gguf,
    fuse_adapter_to_base,
    generate_modelfile,
    map_hf_to_ggml_name,
    run_export_smoke_test,
    verify_gguf,
)
from tools.finetune.register_ollama import (
    register_model_with_ollama,
    run_registration_smoke_test,
    verify_ollama_registration,
)


class TestTensorMapping:
    """Tests for Hugging Face to GGML/GGUF tensor name translation."""

    def test_map_static_tensors(self) -> None:
        assert map_hf_to_ggml_name("model.embed_tokens.weight") == "token_embd.weight"
        assert map_hf_to_ggml_name("model.norm.weight") == "output_norm.weight"
        assert map_hf_to_ggml_name("lm_head.weight") == "output.weight"

    def test_map_layer_projections(self) -> None:
        layer = 3
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.q_proj.weight")
            == f"blk.{layer}.attn_q.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.q_proj.bias")
            == f"blk.{layer}.attn_q.bias"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.k_proj.weight")
            == f"blk.{layer}.attn_k.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.k_proj.bias")
            == f"blk.{layer}.attn_k.bias"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.v_proj.weight")
            == f"blk.{layer}.attn_v.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.v_proj.bias")
            == f"blk.{layer}.attn_v.bias"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.self_attn.o_proj.weight")
            == f"blk.{layer}.attn_output.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.mlp.gate_proj.weight")
            == f"blk.{layer}.ffn_gate.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.mlp.up_proj.weight")
            == f"blk.{layer}.ffn_up.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.mlp.down_proj.weight")
            == f"blk.{layer}.ffn_down.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.input_layernorm.weight")
            == f"blk.{layer}.attn_norm.weight"
        )
        assert (
            map_hf_to_ggml_name(f"model.layers.{layer}.post_attention_layernorm.weight")
            == f"blk.{layer}.ffn_norm.weight"
        )

    def test_unmapped_tensor_passthrough(self) -> None:
        custom_tensor = "model.custom_unmapped_tensor.weight"
        assert map_hf_to_ggml_name(custom_tensor) == custom_tensor


class TestAdapterFusionAndGGUFExport:
    """Tests for fusing LoRA adapters, exporting to GGUF, and reader verification."""

    @pytest.fixture
    def smoke_adapter_dir(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        from tools.finetune.train_qlora import run_smoke_test

        out_dir = tmp_path_factory.mktemp("adapter")
        run_smoke_test(output_dir=out_dir)
        return out_dir

    def test_fuse_adapter_to_base_test_mode(self, smoke_adapter_dir: Path, tmp_path: Path) -> None:
        fused_dir = tmp_path / "fused"
        res = fuse_adapter_to_base(
            adapter_dir=smoke_adapter_dir,
            output_fused_dir=fused_dir,
            test_mode=True,
        )
        assert res == fused_dir
        assert (fused_dir / "config.json").is_file()
        assert (fused_dir / "model.safetensors").is_file()
        assert (fused_dir / "tokenizer.json").is_file()

    def test_convert_fused_to_gguf_and_verify(
        self, smoke_adapter_dir: Path, tmp_path: Path
    ) -> None:
        fused_dir = tmp_path / "fused"
        fuse_adapter_to_base(
            adapter_dir=smoke_adapter_dir,
            output_fused_dir=fused_dir,
            test_mode=True,
        )

        gguf_file = tmp_path / "gguf" / "test_model-f16.gguf"
        res = convert_fused_to_gguf(
            fused_dir=fused_dir,
            output_gguf_path=gguf_file,
            quant_type="f16",
            model_name="localdev-test-export",
        )
        assert res == gguf_file
        assert gguf_file.is_file()
        assert gguf_file.stat().st_size > 0

        # Verify GGUF structure using reader
        report = verify_gguf(gguf_file)
        assert report["is_valid"] is True
        assert report["architecture"] == "qwen2"
        assert report["tensor_count"] == 27
        assert report["file_size_mb"] > 0
        assert "token_embd.weight" in report["tensor_names"]
        assert "output.weight" in report["tensor_names"]

    def test_verify_gguf_nonexistent_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            verify_gguf(tmp_path / "nonexistent.gguf")

    def test_run_export_smoke_test(self, tmp_path: Path) -> None:
        smoke_dir = tmp_path / "smoke_run"
        report = run_export_smoke_test(output_base_dir=smoke_dir)

        assert report["status"] == "success"
        assert report["duration_sec"] > 0
        assert Path(report["fused_dir"]).is_dir()
        assert Path(report["modelfile"]).is_file()
        assert report["gguf_report"]["is_valid"] is True


class TestModelfileGeneration:
    """Tests for Ollama Modelfile rendering and formatting."""

    def test_generate_modelfile_from_template(self, tmp_path: Path) -> None:
        gguf_path = tmp_path / "models" / "finetune" / "gguf" / "model.gguf"
        gguf_path.parent.mkdir(parents=True)
        gguf_path.touch()

        modelfile_path = tmp_path / "models" / "finetune" / "Modelfile"

        out = generate_modelfile(
            model_source_path=gguf_path,
            output_modelfile=modelfile_path,
            template_path=DEFAULT_TEMPLATE_PATH,
        )

        assert out == modelfile_path
        assert modelfile_path.is_file()

        content = modelfile_path.read_text(encoding="utf-8")
        assert "FROM ./gguf/model.gguf" in content
        assert "TEMPLATE" in content
        assert "PARAMETER temperature 0.2" in content
        assert "PARAMETER top_p 0.95" in content
        assert "PARAMETER num_ctx 2048" in content
        assert 'PARAMETER stop "<|im_end|>"' in content

    def test_generate_modelfile_with_custom_system_prompt(self, tmp_path: Path) -> None:
        gguf_path = tmp_path / "model.gguf"
        gguf_path.touch()
        modelfile_path = tmp_path / "Modelfile"

        generate_modelfile(
            model_source_path=gguf_path,
            output_modelfile=modelfile_path,
            system_prompt="Custom system instructions.",
        )

        content = modelfile_path.read_text(encoding="utf-8")
        assert 'SYSTEM """Custom system instructions."""' in content

    def test_generate_modelfile_missing_template(self, tmp_path: Path) -> None:
        gguf_path = tmp_path / "model.gguf"
        gguf_path.touch()
        with pytest.raises(FileNotFoundError):
            generate_modelfile(
                model_source_path=gguf_path,
                output_modelfile=tmp_path / "Modelfile",
                template_path=tmp_path / "nonexistent.template",
            )


class TestOllamaRegistrationHelper:
    """Tests for Ollama CLI execution and API verification."""

    def test_register_model_missing_modelfile(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Modelfile not found"):
            register_model_with_ollama(
                model_name="test-model",
                modelfile_path=tmp_path / "nonexistent_modelfile",
            )

    def test_register_model_missing_executable(self, tmp_path: Path) -> None:
        mf = tmp_path / "Modelfile"
        mf.touch()
        with pytest.raises(FileNotFoundError, match="executable 'nonexistent_ollama' not found"):
            register_model_with_ollama(
                model_name="test-model",
                modelfile_path=mf,
                ollama_cmd="nonexistent_ollama",
            )

    @patch("subprocess.run")
    def test_register_model_success(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mf = tmp_path / "Modelfile"
        mf.touch()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["ollama", "create"],
            returncode=0,
            stdout="writing manifest\nsuccess",
            stderr="",
        )

        res = register_model_with_ollama(
            model_name="localdev-test:3b",
            modelfile_path=mf,
        )
        assert res["success"] is True
        assert res["model_name"] == "localdev-test:3b"
        assert res["duration_sec"] >= 0

    @patch("subprocess.run")
    def test_register_model_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mf = tmp_path / "Modelfile"
        mf.touch()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["ollama", "create"],
            returncode=1,
            stdout="",
            stderr="Error: invalid model architecture",
        )

        with pytest.raises(RuntimeError, match="Ollama registration failed"):
            register_model_with_ollama(
                model_name="localdev-fail:3b",
                modelfile_path=mf,
            )

    def test_verify_ollama_registration_unregistered_model(self) -> None:
        mock_client = MagicMock()
        mock_client.is_available.return_value = True
        mock_client.list_models.return_value = ["model_a:latest", "model_b:latest"]

        res = verify_ollama_registration(
            model_name="unregistered_model",
            client=mock_client,
        )
        assert res["is_available"] is True
        assert res["is_registered"] is False
        assert "not found" in res["error"]

    def test_run_registration_smoke_test_live(self) -> None:
        # Executes live against the local running Ollama daemon
        smoke_res = run_registration_smoke_test(
            test_model_name="localdev-unit-test:latest",
        )
        assert smoke_res["status"] == "success"
        assert smoke_res["registration"]["success"] is True
        assert smoke_res["verification"]["is_registered"] is True
        assert smoke_res["inference"]["success"] is True


class TestGitignoreIntegrity:
    """Tests ensuring .gitignore excludes all large binaries and intermediate fine-tune weights."""

    def test_gitignore_contains_required_model_exclusions(self) -> None:
        gitignore_path = Path(__file__).resolve().parent.parent.parent / ".gitignore"
        assert gitignore_path.is_file()

        content = gitignore_path.read_text(encoding="utf-8")

        # Binary weights exclusions
        assert "*.safetensors" in content
        assert "*.gguf" in content
        assert "models/finetune/fused/" in content
        assert "models/finetune/gguf/" in content
        assert "models/finetune/smoke_test_adapter/" in content
        assert "models/finetune/qlora_adapter/" in content

        # Large dataset splits exclusions
        assert "datasets/finetune/*.jsonl" in content

        # Tracked directory placeholders
        assert "!models/.gitkeep" in content
        assert "!models/finetune/.gitkeep"
        assert "!datasets/.gitkeep"
        assert "!datasets/finetune/.gitkeep"
        assert "!datasets/finetune/manifest.json" in content
