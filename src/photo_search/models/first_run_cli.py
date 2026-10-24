"""Console workflow for explicit first-run model preparation."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path


def _yes(prompt: str) -> bool:
    return input(prompt + " [y/N] ").strip().casefold() in {"y", "yes"}


def run_models_command(args: Namespace) -> int:
    from photo_search.models import first_run

    if args.models_command == "status":
        print(json.dumps(first_run.status(args.data_root), ensure_ascii=False, sort_keys=True))
        return 0
    if args.models_command == "smoke-ocr":
        from PIL import Image

        from photo_search.ocr.engine import PinnedOcrEngine, recognize_image
        from photo_search.ocr.weights import resolve_and_verify

        weights = resolve_and_verify(args.data_root)
        engine = PinnedOcrEngine(weights, resolved_device="cpu")
        with Image.open(args.image) as image:
            result = recognize_image(engine, image.convert("RGB"))
        print(json.dumps({"status": "ok", "device": engine.resolved_device, "text": result.original_text,
                          "regions": len(result.regions)}, ensure_ascii=False))
        return 0
    if args.models_command != "prepare":
        print("Usage: PhotoSearch.exe models status|prepare --data-root <path>")
        return 2

    outcomes: dict[str, str] = {}
    selected = args.component
    if selected is None:
        choice = input("准备模型：1=全部（默认），2=Qwen，3=OCR，4=Face；直接回车准备全部：").strip()
        selected = {"": "all", "1": "all", "2": "qwen", "3": "ocr", "4": "face"}.get(choice)
        if selected is None:
            print("无效选项；没有修改模型文件。")
            return 2
    initial = first_run.status(args.data_root)["components"]
    if selected in {"all", "ocr"}:
        if initial["ocr"]["state"] == "weights_validated_at_install":
            outcomes["ocr"] = "already_ready"
            print("OCR: 已就绪，跳过。")
        else:
            try:
                result = first_run.prepare_ocr(args.data_root)
                outcomes["ocr"] = result["status"]
                print("OCR: 已准备并校验。" if result["status"] == "ok" else "OCR: 已就绪。")
            except Exception as error:  # keep independent components usable
                outcomes["ocr"] = "error"
                print(f"OCR: 准备失败（{type(error).__name__}）：{error}")

    if selected in {"all", "qwen"}:
        qwen_present = initial["qwen"]["state"] == "present_unverified"
        should_prepare = _yes(
            "检测到 Qwen 文件；要校验现有安装或按固定版本修复吗？"
            if qwen_present
            else "Qwen 语义模型约 4.3 GB；确认现在从固定版本下载？"
        )
        if should_prepare:
            try:
                from photo_search.models.embedding.download import download_qwen

                result = download_qwen(args.data_root, confirmed=True)
                first_run.record_qwen_verified(args.data_root)
                outcomes["qwen"] = str(result.get("status"))
                print("Qwen: " + str(result.get("status")))
            except Exception as error:  # noqa: BLE001 - report and continue other models
                outcomes["qwen"] = "error"
                print(
                    f"Qwen: 下载失败（{type(error).__name__}）。请检查网络/TLS 证书后重试，"
                    "或运行 PhotoSearch.exe embedding prepare-local --provider "
                    "qwen3-vl-embedding-2b-exif-v2 --source <已有模型目录> "
                    "--data-root <所选数据目录>。"
                )
        else:
            outcomes["qwen"] = "skipped"

    if selected in {"all", "face"}:
        if initial["face"]["state"] == "weights_validated_at_install":
            outcomes["face"] = "already_ready"
            print("Face: 已就绪，跳过。")
        else:
            print("Face buffalo_l 预训练权重仅限非商业研究用途；查看许可说明：" + first_run.FACE_LICENSE_URL)
        if "face" not in outcomes and _yes("我已阅读并接受 Face 预训练模型许可条件？"):
            try:
                local = input("已有 buffalo_l.zip 的完整路径（直接回车跳过/稍后处理）：").strip().strip('"')
                use_download = False
                if not local:
                    use_download = _yes("是否从固定官方地址下载约 191 MB？")
                if local or use_download:
                    result = first_run.prepare_face(
                        args.data_root,
                        source_zip=Path(local) if local else None,
                        download=use_download,
                        license_accepted=True,
                    )
                    outcomes["face"] = str(result.get("status"))
                    print("Face: " + str(result.get("status")))
                else:
                    outcomes["face"] = "skipped"
            except Exception as error:  # noqa: BLE001 - independent component boundary
                outcomes["face"] = "error"
                print(f"Face: 准备失败（{type(error).__name__}）：{error}")
        elif "face" not in outcomes:
            outcomes["face"] = "skipped_license_not_accepted"

    print(json.dumps({"status": "ok" if "error" not in outcomes.values() else "partial", "components": outcomes}, ensure_ascii=False))
    return 0 if "error" not in outcomes.values() else 1
