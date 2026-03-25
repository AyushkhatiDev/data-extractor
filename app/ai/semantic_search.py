"""
SemanticSearchEngine — Embed extracted business data and enable
natural-language queries using Sentence-Transformers.

Embeddings are stored as JSON arrays in the database for simplicity
(no external vector DB required).
"""

import json
import traceback
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False


# ── Singleton model holder ────────────────────────────────────────────

_model_instance: Optional[object] = None
_model_load_attempted = False


def _get_model(model_name: str = "all-MiniLM-L6-v2"):
    """Lazy-load the embedding model (singleton)."""
    global _model_instance, _model_load_attempted

    if _model_load_attempted:
        return _model_instance

    _model_load_attempted = True

    if not SBERT_AVAILABLE:
        print("[SemanticSearch] sentence-transformers not installed")
        return None

    try:
        _model_instance = SentenceTransformer(model_name)
        print(f"[SemanticSearch] Loaded model: {model_name}")
        return _model_instance
    except Exception as exc:
        print(f"[SemanticSearch] Failed to load model: {exc}")
        traceback.print_exc()
        return None


class SemanticSearchEngine:
    """Embed business records and search by natural-language query."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.model = _get_model(model_name)

    @property
    def is_available(self) -> bool:
        return self.model is not None

    # ── Embedding ─────────────────────────────────────────────────────

    def embed_text(self, text: str) -> Optional[List[float]]:
        """Generate an embedding vector for a piece of text."""
        if not self.model or not text:
            return None

        try:
            vec = self.model.encode(text, convert_to_numpy=True)
            return vec.tolist()
        except Exception as exc:
            print(f"[SemanticSearch] Embedding error: {exc}")
            return None

    def embed_business(self, business_data: Dict) -> Optional[List[float]]:
        """Embed a Business record by concatenating its text fields."""
        parts = []
        for key in ("name", "location", "email", "website"):
            val = business_data.get(key)
            if val:
                parts.append(str(val))

        social = business_data.get("social_links")
        if social and isinstance(social, dict):
            for v in social.values():
                if v:
                    parts.append(str(v))

        text = " | ".join(parts)
        if not text.strip():
            return None

        return self.embed_text(text)

    # ── Search ────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        embeddings: List[Tuple[int, List[float]]],
        top_k: int = 10,
    ) -> List[Dict]:
        """Search over a list of (business_id, embedding_vector) tuples.

        Returns a list of {business_id, score} dicts sorted by relevance.
        """
        if not self.model or not embeddings:
            return []

        query_vec = self.embed_text(query)
        if query_vec is None:
            return []

        query_arr = np.array(query_vec, dtype=np.float32)

        results = []
        for biz_id, emb in embeddings:
            emb_arr = np.array(emb, dtype=np.float32)
            score = float(self._cosine_similarity(query_arr, emb_arr))
            results.append({"business_id": biz_id, "score": round(score, 4)})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # ── Batch operations ──────────────────────────────────────────────

    def embed_and_store_for_task(self, task_id: int):
        """Generate embeddings for all businesses in a task and store them."""
        from app.models import Business, BusinessEmbedding, db

        businesses = Business.query.filter_by(task_id=task_id).all()
        if not businesses:
            return 0

        count = 0
        for biz in businesses:
            # Skip if embedding already exists
            existing = BusinessEmbedding.query.filter_by(business_id=biz.id).first()
            if existing:
                continue

            biz_data = biz.to_dict()
            vec = self.embed_business(biz_data)
            if vec:
                emb = BusinessEmbedding(
                    business_id=biz.id,
                    embedding=json.dumps(vec),
                )
                db.session.add(emb)
                count += 1

        if count:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        return count

    def search_task(self, task_id: int, query: str, top_k: int = 10) -> List[Dict]:
        """Search within a specific task's businesses."""
        from app.models import Business, BusinessEmbedding

        emb_rows = (
            BusinessEmbedding.query
            .join(Business, Business.id == BusinessEmbedding.business_id)
            .filter(Business.task_id == task_id)
            .all()
        )

        embeddings = []
        for row in emb_rows:
            try:
                vec = json.loads(row.embedding)
                embeddings.append((row.business_id, vec))
            except (json.JSONDecodeError, TypeError):
                continue

        return self.search(query, embeddings, top_k)

    # ── Math ──────────────────────────────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
