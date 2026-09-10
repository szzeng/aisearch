"""Minimal HTTP application for the P0-01 environment check."""

from fastapi import FastAPI

app = FastAPI(title="Photo Search", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Report only process-level readiness; no models or user data are loaded."""
    return {"status": "ok", "service": "photo-search"}
