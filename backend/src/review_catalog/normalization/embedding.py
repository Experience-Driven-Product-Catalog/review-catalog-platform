from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np

from review_catalog.settings import Settings


class Embedder(Protocol):
    model_id: str

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(self, settings: Settings) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("install review-catalog-platform[embedding]") from exc
        model_path = settings.embedding_model_path
        if model_path is None or not model_path.is_dir():
            raise FileNotFoundError(f"local embedding model is missing: {model_path}")
        self.model_id = settings.embedding_model_id
        self.batch_size = settings.embedding_batch_size
        self.model = SentenceTransformer(
            str(model_path),
            device=settings.embedding_device,
            local_files_only=settings.embedding_local_files_only,
        )
        self._cache: dict[str, np.ndarray] = {}

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        normalized = [str(text) for text in texts]
        missing = sorted(set(normalized) - set(self._cache))
        if missing:
            vectors = self.model.encode(
                missing,
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).astype(np.float32, copy=False)
            for text, vector in zip(missing, vectors, strict=True):
                self._cache[text] = vector
        if not normalized:
            return np.empty((0, 0), dtype=np.float32)
        return np.stack([self._cache[text] for text in normalized]).astype(np.float32, copy=False)


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "sentence_transformer":
        return SentenceTransformerEmbedder(settings)
    raise ValueError(f"unsupported embedding backend: {settings.embedding_backend}")
