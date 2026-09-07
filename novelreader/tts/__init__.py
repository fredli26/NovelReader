"""Engine registry — add a new backend by appending it to ENGINES."""
from __future__ import annotations

from .azure_engine import AzureEngine
from .base import Engine, TTSError, Voice
from .edge_engine import EdgeEngine
from .macos_engine import MacSayEngine

ENGINES: dict[str, Engine] = {
    engine.id: engine
    for engine in (EdgeEngine(), MacSayEngine(), AzureEngine())
}

DEFAULT_ENGINE = "edge"


def get_engine(engine_id: str) -> Engine:
    engine = ENGINES.get(engine_id or DEFAULT_ENGINE)
    if engine is None:
        raise TTSError(f"Unknown engine: {engine_id!r}")
    return engine


def describe_all() -> list[dict]:
    """Everything the UI needs to render the engine and voice pickers."""
    out = []
    for engine in ENGINES.values():
        ok, reason = engine.available()
        out.append({
            "id": engine.id,
            "label": engine.label,
            "description": engine.description,
            "available": ok,
            "reason": reason,
            "needs_network": engine.needs_network,
            "voices": [
                {"id": v.id, "label": v.label, "gender": v.gender, "note": v.note}
                for v in (engine.voices() if ok else [])
            ],
        })
    return out


__all__ = ["ENGINES", "DEFAULT_ENGINE", "Engine", "TTSError", "Voice",
           "get_engine", "describe_all"]
