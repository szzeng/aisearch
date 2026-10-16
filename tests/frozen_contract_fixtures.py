"""Canonical Git bytes and the explicitly approved P4-02Y-L contract extension."""

from __future__ import annotations

import hashlib

P2_HISTORICAL_SHA256 = "ec7de5f0cbe6d4c2ef2bf357326fa79abc3b427e6133e42186d61778ef0e4a57"
P2_OCR_EXTENSION_SHA256 = "4270997cac64a3c19c80f1d118b5e3d16022fdccb2c0a454c21d43d7730325fa"


def assert_p2_approved_extension(payload: bytes) -> None:
    """Pin today's approved bytes and every historical byte outside the added branch.

    Authorization: docs/reports/P4-02Y-L-sol-contract-freeze.md, implemented in
    c1d0a34. Windows Git text checkout CRLF is normalized; all other bytes matter.
    """
    payload = payload.replace(b"\r\n", b"\n")
    assert hashlib.sha256(payload).hexdigest() == P2_OCR_EXTENSION_SHA256
    start = payload.index(b'    "StructuredModelProcessorSpec": {')
    end = payload.index(b'    "ProcessorSpec": {', start)
    historical = payload[:start] + payload[end:]
    historical = historical.replace(b', "structured_model"', b'')
    historical = historical.replace(
        b',\n        { "$ref": "#/$defs/StructuredModelProcessorSpec" }', b''
    )
    assert hashlib.sha256(historical).hexdigest() == P2_HISTORICAL_SHA256
