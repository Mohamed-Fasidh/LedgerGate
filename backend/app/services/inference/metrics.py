from __future__ import annotations

import platform
from importlib import metadata
from typing import Any


def hardware_snapshot() -> dict[str, Any]:
    """Return best-effort hardware data; unknown GPU measurements remain null."""
    info: dict[str, Any] = {
        "platform": platform.platform(), "python": platform.python_version(),
        "machine": platform.machine(), "processor": platform.processor(),
        "cpu_count": None, "cpu_percent": None, "ram_total_bytes": None,
        "ram_available_bytes": None, "cpu_metrics_available": False,
        "cuda_available": False, "gpu_detected": False, "gpu": None,
        "gpu_utilization_percent": None, "gpu_memory_used_bytes": None,
        "gpu_memory_total_bytes": None, "gpu_driver_version": None, "bf16_supported": None,
    }
    try:
        import psutil
        info["cpu_count"] = psutil.cpu_count(logical=True)
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        info["ram_total_bytes"] = memory.total
        info["ram_available_bytes"] = memory.available
        info["cpu_metrics_available"] = True
    except ImportError:
        pass

    try:
        import torch
        info["torch_version"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["cuda_runtime_version"] = getattr(torch.version, "cuda", None)
        info["rocm_runtime_version"] = getattr(torch.version, "hip", None)
        if info["cuda_available"]:
            props = torch.cuda.get_device_properties(0)
            info["gpu_detected"] = True
            info["gpu"] = props.name
            info["gpu_memory_total_bytes"] = props.total_memory
            info["gpu_memory_used_bytes"] = torch.cuda.memory_allocated(0)
            info["gpu_compute_capability"] = f"{props.major}.{props.minor}"
            try:
                info["bf16_supported"] = bool(torch.cuda.is_bf16_supported())
            except Exception:
                pass
    except ImportError:
        info["torch_version"] = None
        info["cuda_detection"] = "unavailable (PyTorch not installed)"
    except Exception as exc:
        info["gpu_probe_error"] = type(exc).__name__

    # NVML may report physical GPU telemetry independently of the PyTorch build.
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            info["gpu"] = info["gpu"] or (
                name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
            )
            info["gpu_detected"] = True
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            info["gpu_memory_used_bytes"] = memory.used
            info["gpu_memory_total_bytes"] = memory.total
            info["gpu_utilization_percent"] = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
            driver = pynvml.nvmlSystemGetDriverVersion()
            info["gpu_driver_version"] = (
                driver.decode("utf-8", "replace") if isinstance(driver, bytes) else str(driver)
            )
        finally:
            pynvml.nvmlShutdown()
    except ImportError:
        info["gpu_telemetry_source"] = "unavailable (NVML not installed)"
    except Exception as exc:
        info["gpu_telemetry_error"] = type(exc).__name__
    return info


def software_snapshot() -> dict[str, Any]:
    versions = {"python": platform.python_version()}
    for distribution in ("httpx", "torch", "vllm", "transformers", "psutil", "matplotlib"):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions
