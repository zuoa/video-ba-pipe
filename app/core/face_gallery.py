"""Exact, versioned 1:N face gallery search."""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from app.core.database_models import (
    FaceGallery,
    FaceGalleryMembership,
    FacePerson,
    FaceTemplate,
)
from app.core.face_crypto import decrypt_biometric


def normalize_embedding(value) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError('人脸特征向量无效')
    return np.ascontiguousarray(vector / norm, dtype=np.float32)


def serialize_embedding(value) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, normalize_embedding(value), allow_pickle=False)
    return buffer.getvalue()


def deserialize_embedding(value: bytes) -> np.ndarray:
    return normalize_embedding(np.load(io.BytesIO(value), allow_pickle=False))


@dataclass(frozen=True)
class FaceSearchMatch:
    person_id: int
    person_code: str
    person_name: str
    similarity: float


class GalleryIndex:
    def __init__(self, gallery_id: int, gallery_version: int, matrix, people):
        self.gallery_id = int(gallery_id)
        self.gallery_version = int(gallery_version)
        self.matrix = matrix
        self.people = people

    @property
    def template_count(self) -> int:
        return int(self.matrix.shape[0])

    def search(self, embedding, top_k: int = 3) -> List[FaceSearchMatch]:
        query = normalize_embedding(embedding)
        if self.matrix.size == 0:
            return []
        if self.matrix.shape[1] != query.shape[0]:
            raise ValueError(
                f'特征维度不匹配: gallery={self.matrix.shape[1]}, query={query.shape[0]}'
            )
        scores = self.matrix @ query
        best_by_person: Dict[int, float] = {}
        person_info = {}
        for index, score in enumerate(scores.tolist()):
            person_id, code, name = self.people[index]
            if person_id not in best_by_person or score > best_by_person[person_id]:
                best_by_person[person_id] = float(score)
                person_info[person_id] = (code, name)
        ordered = sorted(best_by_person.items(), key=lambda item: item[1], reverse=True)
        return [
            FaceSearchMatch(
                person_id=person_id,
                person_code=person_info[person_id][0],
                person_name=person_info[person_id][1],
                similarity=score,
            )
            for person_id, score in ordered[:max(1, int(top_k))]
        ]


class GalleryIndexCache:
    def __init__(self):
        self._lock = threading.RLock()
        self._indexes: Dict[int, GalleryIndex] = {}

    def invalidate(self, gallery_id: Optional[int] = None):
        with self._lock:
            if gallery_id is None:
                self._indexes.clear()
            else:
                self._indexes.pop(int(gallery_id), None)

    def get(self, gallery_id: int) -> GalleryIndex:
        gallery = FaceGallery.get_by_id(int(gallery_id))
        with self._lock:
            cached = self._indexes.get(gallery.id)
            if cached is not None and cached.gallery_version == gallery.gallery_version:
                return cached
        loaded = self._load(gallery)
        with self._lock:
            self._indexes[gallery.id] = loaded
        return loaded

    @staticmethod
    def _load(gallery: FaceGallery) -> GalleryIndex:
        query = (
            FaceTemplate
            .select(FaceTemplate, FacePerson)
            .join(FacePerson)
            .switch(FaceTemplate)
            .join(
                FaceGalleryMembership,
                on=(FaceGalleryMembership.person == FaceTemplate.person),
            )
            .where(
                (FaceGalleryMembership.gallery == gallery.id)
                & (FacePerson.enabled == True)
                & FaceTemplate.encrypted_embedding.is_null(False)
                & (FaceTemplate.model_contract == gallery.model_bundle.contract_id)
            )
            .order_by(FaceTemplate.id)
        )
        vectors = []
        people = []
        dimension = int(gallery.model_bundle.embedding_dimension)
        for template in query:
            raw = decrypt_biometric(
                bytes(template.encrypted_embedding),
                purpose=f'face-embedding:{template.person_id}',
            )
            vector = deserialize_embedding(raw)
            if vector.shape[0] != dimension:
                continue
            vectors.append(vector)
            people.append((
                int(template.person_id),
                template.person.person_code,
                template.person.name,
            ))
        matrix = (
            np.stack(vectors).astype(np.float32, copy=False)
            if vectors else np.empty((0, dimension), dtype=np.float32)
        )
        return GalleryIndex(gallery.id, gallery.gallery_version, matrix, people)


gallery_index_cache = GalleryIndexCache()
