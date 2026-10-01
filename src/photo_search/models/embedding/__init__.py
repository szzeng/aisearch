"""Pluggable semantic embedding providers (P4-Embedding-Arch).

``base``        - provider-independent manifests, identity derivation and the one frozen
                  ``ProcessorSpec`` builder. Framework-free.
``registry``    - what this data root has installed, and how a persisted ``processor_id``
                  resolves back to its description.
``factory``     - worker-side construction of a concrete adapter from a manifest.
``qwen3_vl``    - the PyTorch/CUDA adapter (imports torch only when a model is loaded).
``chinese_clip_onnx`` - the historical ONNX adapter, kept for compatibility.

Nothing in this package imports a heavy framework at module import time except
``chinese_clip_onnx`` (ONNX Runtime), which the indexing worker already owns.
"""
