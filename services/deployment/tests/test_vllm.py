import json

import pytest
from pydantic import ValidationError
from slm_deployment.vllm import VllmArgs, build_vllm_command, to_shell


def flag_value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_minimal_command_uses_vllm_defaults():
    cmd = build_vllm_command("microsoft/Phi-4-mini-instruct", {})
    assert cmd == ["vllm", "serve", "microsoft/Phi-4-mini-instruct", "--host", "0.0.0.0", "--port", "8000"]


def test_core_flags_are_emitted():
    cmd = build_vllm_command(
        "/models/phi",
        {
            "max_model_len": 8192,
            "gpu_memory_utilization": 0.85,
            "max_num_seqs": 128,
            "max_num_batched_tokens": 16384,
            "quantization": "awq",
            "dtype": "bfloat16",
            "served_model_name": "phi-4-mini",
        },
        port=9000,
    )
    assert flag_value(cmd, "--max-model-len") == "8192"
    assert flag_value(cmd, "--gpu-memory-utilization") == "0.85"
    assert flag_value(cmd, "--max-num-seqs") == "128"
    assert flag_value(cmd, "--max-num-batched-tokens") == "16384"
    assert flag_value(cmd, "--quantization") == "awq"
    assert flag_value(cmd, "--dtype") == "bfloat16"
    assert flag_value(cmd, "--served-model-name") == "phi-4-mini"
    assert flag_value(cmd, "--port") == "9000"


def test_prefix_caching_flag_both_ways():
    assert "--enable-prefix-caching" in build_vllm_command("m", {"enable_prefix_caching": True})
    off = build_vllm_command("m", {"enable_prefix_caching": False})
    assert "--no-enable-prefix-caching" in off and "--enable-prefix-caching" not in off
    unset = build_vllm_command("m", {})
    assert "--enable-prefix-caching" not in unset and "--no-enable-prefix-caching" not in unset


def test_speculative_config_is_compact_deterministic_json():
    cmd = build_vllm_command(
        "m",
        {"speculative_config": {"method": "ngram", "num_speculative_tokens": 5, "prompt_lookup_max": 4}},
    )
    raw = flag_value(cmd, "--speculative-config")
    assert raw == '{"method":"ngram","num_speculative_tokens":5,"prompt_lookup_max":4}'
    draft = build_vllm_command(
        "m", {"speculative_config": {"model": "meta-llama/Llama-3.2-1B", "num_speculative_tokens": 3}}
    )
    assert json.loads(flag_value(draft, "--speculative-config")) == {
        "model": "meta-llama/Llama-3.2-1B",
        "num_speculative_tokens": 3,
    }


def test_lora_adapters_on_a_shared_base():
    cmd = build_vllm_command(
        "m",
        {
            "enable_lora": True,
            "max_loras": 4,
            "max_lora_rank": 32,
            "lora_modules": [
                {"name": "support", "path": "s3://adapters/support"},
                {"name": "legal", "path": "/mnt/legal"},
            ],
        },
    )
    assert "--enable-lora" in cmd and flag_value(cmd, "--max-loras") == "4"
    i = cmd.index("--lora-modules")
    assert cmd[i + 1 : i + 3] == ["support=s3://adapters/support", "legal=/mnt/legal"]


def test_tensor_parallel_defaults_to_gpu_count():
    assert "--tensor-parallel-size" not in build_vllm_command("m", {}, gpu_count=1)
    assert flag_value(build_vllm_command("m", {}, gpu_count=4), "--tensor-parallel-size") == "4"
    explicit = build_vllm_command("m", {"tensor_parallel_size": 2}, gpu_count=4)
    assert flag_value(explicit, "--tensor-parallel-size") == "2"


def test_served_name_argument_wins_and_shell_form_is_quoted():
    cmd = build_vllm_command(
        "m",
        VllmArgs(served_model_name="from-args", speculative_config={"method": "ngram", "num_speculative_tokens": 2}),
        served_model_name="from-deployment",
    )
    assert flag_value(cmd, "--served-model-name") == "from-deployment"
    assert "'{\"method\":\"ngram\",\"num_speculative_tokens\":2}'" in to_shell(cmd)


@pytest.mark.parametrize(
    "bad",
    [
        {"gpu_memory_utilization": 1.5},
        {"gpu_memory_utilization": 0},
        {"max_model_len": 10},
        {"quantization": "int3"},
        {"unknown_flag": True},
        {"lora_modules": [{"name": "a", "path": "/x"}]},  # needs enable_lora
        {"enable_lora": True, "lora_modules": [{"name": "a", "path": "/x"}, {"name": "a", "path": "/y"}]},
        {"max_loras": 2},
        {"speculative_config": {"num_speculative_tokens": 3}},  # no drafter
        {"speculative_config": {"method": "eagle", "num_speculative_tokens": 3}},  # needs a model
        {"speculative_config": {"method": "ngram", "model": "x", "num_speculative_tokens": 3}},
        {"speculative_config": {"method": "ngram", "num_speculative_tokens": 0}},
        {"speculative_config": {"method": "ngram", "num_speculative_tokens": 3, "prompt_lookup_min": 5, "prompt_lookup_max": 2}},
    ],
)
def test_invalid_args_are_rejected(bad):
    with pytest.raises(ValidationError):
        build_vllm_command("m", bad)


def test_empty_model_is_rejected():
    with pytest.raises(ValueError, match="model"):
        build_vllm_command("  ", {})
