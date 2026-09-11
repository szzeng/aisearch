"""Spawned AI worker process for manifest-driven Chinese-CLIP inference."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any

from photo_search.models.embedding.chinese_clip_onnx import ChineseClipOnnxAdapter
from photo_search.workers.protocol import ProtocolError, checked_message, error_message


def run_worker(
    request_queue: Any,
    response_queue: Any,
    model_dir: str,
    manifest_path: str,
    stderr_path: str,
) -> None:
    """Service bounded requests until a shutdown request or unrecoverable transport error."""
    completed_steps: list[str] = []
    with Path(stderr_path).open("a", encoding="utf-8", buffering=1) as stderr, contextlib.redirect_stderr(stderr):
        try:
            adapter = ChineseClipOnnxAdapter(Path(model_dir), Path(manifest_path))
            adapter.load()
            completed_steps.append("model_loaded")
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
                        error_message(stage=stage, error=error, completed_steps=completed_steps), timeout=1
                    )
        except BaseException as error:
            print(f"worker startup/fatal failure: {type(error).__name__}: {error}", file=sys.stderr)
            try:
                response_queue.put(
                    error_message(stage="startup", error=error, completed_steps=completed_steps), timeout=1
                )
            except Exception:
                pass


def _handle(
    adapter: ChineseClipOnnxAdapter,
    request: dict[str, Any],
    completed_steps: list[str],
) -> dict[str, Any]:
    action = request.get("action")
    if action == "health":
        result: dict[str, Any] = adapter.health()
    elif action == "encode_text":
        result = {"vector": adapter.encode_text(request.get("text")), "spec": adapter.spec().as_dict()}
    elif action == "encode_image":
        image_path = Path(request.get("image_path", ""))
        result = {"vector": adapter.encode_image(image_path), "spec": adapter.spec().as_dict()}
    elif action == "shutdown":
        result = {"exit_code": 0}
    else:
        raise ProtocolError(f"unsupported action: {action!r}")
    completed = [*completed_steps, str(action)]
    return {"status": "ok", "stage": str(action), "completed_steps": completed, "result": result}
