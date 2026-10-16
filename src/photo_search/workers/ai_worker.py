"""Spawned AI worker process for manifest-driven Chinese-CLIP inference.

The worker owns the device decision: it defaults to CUDA, accepts an explicit CPU
request, reports the device/provider it actually resolved, and turns an unavailable GPU
into a structured failure instead of a silent whole-model CPU run.

P4-Embedding-Arch: the worker does not name an adapter any more. It reads the *manifest* it was
handed and asks the model factory for the adapter that manifest describes, so the Chinese-CLIP
ONNX path and the Qwen3-VL PyTorch path share this one process, this one protocol and this one
device policy - and neither framework is imported by the process that only sends requests.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from photo_search.catalog.errors import CatalogError
from photo_search.media import heif as media_heif
from photo_search.models.embedding.base import CLIP_ADAPTER, provider_block, read_manifest
from photo_search.models.embedding.factory import load_adapter
from photo_search.runtime.device import DevicePolicy, DeviceUnavailableError
from photo_search.workers.protocol import ProtocolError, checked_message, error_message

DEMO_QUERY = "红色的图片"
DEMO_IMAGES = {"red": (220, 40, 40), "green": (40, 180, 70), "blue": (45, 80, 210)}


def prepare_framework_stack(manifest_path: str | Path) -> dict[str, Any]:
    """Keep the ONNX-serving worker to **one** CUDA stack (P4-Embedding-Arch).

    The tokenizer the Chinese-CLIP adapter needs comes from ``transformers``, which imports
    ``torch`` as a side effect. In this environment two CUDA stacks coexist on purpose:
    ``onnxruntime-gpu`` is the CUDA 13 build and PyTorch is the measured cu128 build. ONNX Runtime
    prints "The installed PyTorch … uses CUDA 12.x, but onnxruntime-gpu is built with CUDA 13.x"
    whenever ``torch`` is in ``sys.modules`` of the same process (``onnxruntime/__init__.py``
    looks at ``sys.modules``, not at the process's DLL search path), and the cuDNN 9 files carry
    the *same names* in both stacks while being linked against different CUDA majors.

    Setting ``USE_TORCH=0`` before the first ``transformers`` import makes transformers skip
    PyTorch entirely: the tokenizer is still the same :class:`BertTokenizerFast` with the same
    vocabulary and the same ids (verified against the PyTorch-enabled mode), but no second CUDA
    stack is ever loaded into this process. A ``qwen3_vl`` manifest needs PyTorch, so it is left
    untouched, and an ``USE_TORCH`` the operator set explicitly is never overridden.
    """
    adapter = str(provider_block(read_manifest(manifest_path))["adapter"])
    applied = False
    if adapter == CLIP_ADAPTER and "torch" not in sys.modules and "transformers" not in sys.modules:
        previous = os.environ.get("USE_TORCH")
        if previous is None:
            os.environ["USE_TORCH"] = "0"
            applied = True
        return {
            "adapter": adapter,
            "use_torch": str(os.environ.get("USE_TORCH")),
            "applied": applied,
            "torch_imported": "torch" in sys.modules,
        }
    return {
        "adapter": adapter,
        "use_torch": str(os.environ.get("USE_TORCH", "unset")),
        "applied": False,
        "torch_imported": "torch" in sys.modules,
    }


def run_worker(
    request_queue: Any,
    response_queue: Any,
    model_dir: str,
    manifest_path: str,
    stderr_path: str,
    device: str | None = None,
    device_policy: str = DevicePolicy.CPU_ALLOWED.value,
) -> None:
    """Service bounded requests until a shutdown request or unrecoverable transport error."""
    completed_steps: list[str] = []
    failure_details: dict[str, Any] = {
        "requested_device": device,
        "device_policy": device_policy,
        "cpu_fallback": "disabled",
    }
    with Path(stderr_path).open("a", encoding="utf-8", buffering=1) as stderr, contextlib.redirect_stderr(stderr):
        try:
            # The one HEIF registration point of the product has to run in *this* process too:
            # the adapter decodes the source files here, and Pillow has no HEIF decoder of its own.
            # Without it an image the classifier accepted as indexable (HEIC/HEIF) failed here with
            # a bare PIL ``UnidentifiedImageError`` - the single uncovered asset of a 3610-file
            # library was exactly that case. Fail-closed: an unavailable plugin just means this
            # process cannot decode HEIC, which the adapter then reports per file.
            heif = media_heif.runtime_fact()
            failure_details["heif"] = heif
            framework_stack = prepare_framework_stack(manifest_path)
            failure_details["framework_stack"] = framework_stack
            adapter = load_adapter(
                Path(model_dir),
                Path(manifest_path),
                device=device,
                device_policy=DevicePolicy(device_policy),
                enable_profiling=True,
            )
            # The factory returns an already loaded adapter; loading again would recreate
            # both ONNX sessions or reload the Qwen weights before the first request.
            completed_steps.append("model_loaded")
            failure_details["device"] = adapter.resolution.as_dict()
            while True:
                request = request_queue.get()
                try:
                    checked = checked_message(request)
                    response = _handle(adapter, checked, completed_steps)
                    response_queue.put(checked_message(response), timeout=1)
                    if checked["action"] == "shutdown":
                        return
                except Exception as error:  # one malformed request must not take down the process
                    stage = str(request.get("action", "request")) if isinstance(request, dict) else "request"
                    response_queue.put(
                        error_message(
                            stage=stage,
                            error=error,
                            completed_steps=completed_steps,
                            details=_error_details(error, adapter),
                        ),
                        timeout=1,
                    )
        except BaseException as error:
            print(f"worker startup/fatal failure: {type(error).__name__}: {error}", file=sys.stderr)
            failure_details.update(_error_details(error, None))
            try:
                response_queue.put(
                    error_message(
                        stage="startup",
                        error=error,
                        completed_steps=completed_steps,
                        details=failure_details,
                    ),
                    timeout=1,
                )
            except Exception:
                pass


def _error_details(error: BaseException, adapter: Any) -> dict[str, Any]:
    """Device facts that let a caller pause the right AI task without parsing text."""
    details: dict[str, Any] = {"cpu_fallback": "disabled"}
    if isinstance(error, DeviceUnavailableError):
        details.update(error.as_dict())
        return details
    if isinstance(error, CatalogError):
        # P4-Embedding-Arch: a structured refusal (missing runtime, hash mismatch, unusable CUDA
        # device, OOM) carries its own code and reason, so the parent can report *why* the model
        # could not serve instead of parsing a message.
        details["code"] = str(error.code)
        details.update(dict(error.details))
    if adapter is not None:
        with contextlib.suppress(Exception):
            details["device"] = adapter.resolution.as_dict()
            details["runtime"] = adapter.health().get("runtime")
    return details


def _handle(
    adapter: Any,
    request: dict[str, Any],
    completed_steps: list[str],
) -> dict[str, Any]:
    action = request.get("action")
    if action == "health":
        result: dict[str, Any] = adapter.health()
        result["heif"] = media_heif.runtime_fact()
        result["framework_stack"] = {
            "torch_imported": "torch" in sys.modules,
            "use_torch": str(os.environ.get("USE_TORCH", "unset")),
        }
    elif action == "encode_text":
        result = {
            "vector": adapter.encode_text(request.get("text")),
            "spec": adapter.spec().as_dict(),
            "device": adapter.resolution.as_dict(),
        }
    elif action == "encode_image":
        image_path = Path(request.get("image_path", ""))
        result = {
            "vector": adapter.encode_image(image_path),
            "spec": adapter.spec().as_dict(),
            "device": adapter.resolution.as_dict(),
        }
    elif action == "demo_search":
        result = _demo_search(adapter)
    elif action == "provider_usage":
        result = {"sessions": adapter.provider_usage(), "device": adapter.resolution.as_dict()}
    elif action == "shutdown":
        result = {"exit_code": 0}
    else:
        raise ProtocolError(f"unsupported action: {action!r}")
    completed = [*completed_steps, str(action)]
    return {"status": "ok", "stage": str(action), "completed_steps": completed, "result": result}


def _demo_search(adapter: Any) -> dict[str, Any]:
    """Run the offline fixture search inside the worker on the resolved device."""
    with tempfile.TemporaryDirectory(prefix="photo-search-demo-") as raw_directory:
        directory = Path(raw_directory)
        paths: list[tuple[str, Path]] = []
        for name, color in DEMO_IMAGES.items():
            path = directory / f"{name}.png"
            Image.new("RGB", (256, 192), color).save(path)
            paths.append((name, path))
        query_vector = adapter.encode_text(DEMO_QUERY)
        image_results = [(name, adapter.encode_image(path)) for name, path in paths]
    query = np.asarray(query_vector, dtype=np.float32)
    scores = [
        {"id": name, "cosine": float(query @ np.asarray(vector, dtype=np.float32))}
        for name, vector in image_results
    ]
    scores.sort(key=lambda row: row["cosine"], reverse=True)
    return {
        "query": DEMO_QUERY,
        "query_l2_norm": float(np.linalg.norm(query)),
        "images": [
            {"id": name, "l2_norm": float(np.linalg.norm(np.asarray(vector, dtype=np.float32)))}
            for name, vector in image_results
        ],
        "top_k": scores,
        "spec": adapter.spec().as_dict(),
        "device": adapter.resolution.as_dict(),
    }
