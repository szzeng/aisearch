"""Packaged front-end assets (HTML/CSS/JS) served read-only by ``api.web``.

The directory is a Python package so ``importlib.resources.files("photo_search.web")``
resolves to real files both from ``src/`` and inside the frozen onedir bundle.
"""
