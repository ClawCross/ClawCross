"""
Optional Chroma-backed memory helpers.

The project keeps working when Chroma is unavailable. When installed, we use a
local PersistentClient plus deterministic hash embeddings so tests and local
setups do not depend on downloading an external embedding model.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

try:  # pragma: no cover - exercised indirectly in integration tests
    import chromadb
except Exception as exc:  # pragma: no cover - import failure path
    chromadb = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""


_EMBED_DIMENSIONS = 192
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9_]{2,}", re.IGNORECASE)


def chroma_available() -> bool:
    return chromadb is not None


def chroma_status() -> dict[str, Any]:
    return {
        "available": chroma_available(),
        "provider": "chroma" if chroma_available() else "keyword",
        "reason": "" if chroma_available() else (_IMPORT_ERROR or "chromadb not installed"),
    }


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            payload[str(key)] = value
        elif isinstance(value, (int, float)):
            payload[str(key)] = value
        elif isinstance(value, str):
            payload[str(key)] = value[:1000]
        else:
            payload[str(key)] = json.dumps(value, ensure_ascii=False, sort_keys=True)[:1000]
    return payload


def _iter_terms(text: str) -> list[str]:
    lowered = (text or "").lower()
    words = _WORD_RE.findall(lowered)
    cjk = _CJK_RE.findall(text or "")
    cjk_pairs = ["".join(cjk[index : index + 2]) for index in range(max(0, len(cjk) - 1))]
    char_ngrams = [lowered[index : index + 3] for index in range(max(0, len(lowered) - 2)) if " " not in lowered[index : index + 3]]
    return [item for item in [*words, *cjk_pairs, *char_ngrams] if item]


def hash_embed_text(text: str, *, dimensions: int = _EMBED_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    terms = _iter_terms(text)
    if not terms:
        return vector
    for term in terms:
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (min(len(term), 12) / 12.0)
        vector[bucket] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [round(value / norm, 8) for value in vector]


def _get_collection(path: str | Path, collection_name: str):
    if not chroma_available():
        return None
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(root))
    return client.get_or_create_collection(name=collection_name)


def upsert_text(
    *,
    path: str | Path,
    collection_name: str,
    record_id: str,
    document: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    collection = _get_collection(path, collection_name)
    if collection is None:
        return False
    try:
        collection.upsert(
            ids=[str(record_id)],
            documents=[str(document or "")[:8000]],
            metadatas=[_sanitize_metadata(metadata)],
            embeddings=[hash_embed_text(str(document or ""))],
        )
        return True
    except Exception:
        return False


def query_text(
    *,
    path: str | Path,
    collection_name: str,
    query: str,
    limit: int = 5,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not str(query or "").strip():
        return []
    collection = _get_collection(path, collection_name)
    if collection is None:
        return []
    try:
        raw = collection.query(
            query_embeddings=[hash_embed_text(query)],
            n_results=max(1, int(limit or 5)),
            include=["documents", "metadatas", "distances"],
            where=where or None,
        )
    except Exception:
        return []

    ids = (raw.get("ids") or [[]])[0]
    documents = (raw.get("documents") or [[]])[0]
    metadatas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    results: list[dict[str, Any]] = []
    for record_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
        numeric_distance = float(distance or 0.0)
        similarity = max(0.0, 1.0 - (numeric_distance / 2.0))
        results.append(
            {
                "id": record_id,
                "document": document or "",
                "metadata": metadata or {},
                "distance": numeric_distance,
                "similarity": round(similarity, 4),
            }
        )
    return results


def list_unique_metadata_values(
    *,
    path: str | Path,
    collection_name: str,
    field: str,
    where: dict[str, Any] | None = None,
    limit: int = 1000,
) -> list[str]:
    collection = _get_collection(path, collection_name)
    if collection is None:
        return []
    values: set[str] = set()
    offset = 0
    batch_size = min(max(50, int(limit or 1000)), 500)
    while offset < max(1, int(limit or 1000)):
        try:
            raw = collection.get(
                include=["metadatas"],
                where=where or None,
                limit=batch_size,
                offset=offset,
            )
        except Exception:
            break
        metadatas = raw.get("metadatas") or []
        if not metadatas:
            break
        for metadata in metadatas:
            value = (metadata or {}).get(field)
            if isinstance(value, str) and value:
                values.add(value)
        offset += len(metadatas)
        if len(metadatas) < batch_size:
            break
    return sorted(values)
