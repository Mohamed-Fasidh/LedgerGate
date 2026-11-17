import importlib.util
from pathlib import Path

PROFILE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "vllm_profile.py"
spec = importlib.util.spec_from_file_location("vllm_profile", PROFILE_SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_profile_command_exposes_batching_and_kv_cache_controls():
    profile = {
        "dtype": "bfloat16", "gpu_memory_utilization": 0.9, "max_num_seqs": 16,
        "max_num_batched_tokens": 8192, "max_model_len": 4096, "kv_cache_dtype": "fp8",
    }
    command = module.build_command(profile, "org/model", "0.0.0.0", 8001)
    assert "--max-num-seqs" in command
    assert "--max-num-batched-tokens" in command
    assert "--kv-cache-dtype" in command
    assert "bfloat16" in command


def test_quantization_profile_passes_server_quantization():
    profile = {
        "dtype": "half", "gpu_memory_utilization": 0.9, "max_num_seqs": 8,
        "max_num_batched_tokens": 4096, "max_model_len": 4096, "quantization": "awq",
    }
    command = module.build_command(profile, "org/awq-model", "127.0.0.1", 8001)
    assert command[command.index("--quantization") + 1] == "awq"
