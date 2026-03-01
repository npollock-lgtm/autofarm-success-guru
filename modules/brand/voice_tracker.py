"""
Voice Tracker — monitors semantic drift and ensures voice consistency over time.

Uses embeddings (via LLMRouter) and cosine similarity to compare new scripts
against a rolling baseline of recent brand scripts.  Alerts when a brand's
voice drifts beyond acceptable bounds.

Similar approach to ``cross_brand_dedup.py`` but applied **intra-brand** —
we want scripts from the *same* brand to remain consistent.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("autofarm.brand.voice_tracker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASELINE_SIZE = 20  # rolling window of recent scripts
DRIFT_THRESHOLD = 0.25  # cosine distance; > this triggers alert
MIN_BASELINE = 3  # need at least N scripts before drift is meaningful


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class VoiceCheckResult:
    """Result of a voice-consistency check."""

    brand_id: str
    similarity: float  # 0-1 cosine similarity to baseline centroid
    drift: float  # 1 - similarity
    drifted: bool  # True if drift > threshold
    message: str = ""
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for storage or API response."""
        return {
            "brand_id": self.brand_id,
            "similarity": round(self.similarity, 4),
            "drift": round(self.drift, 4),
            "drifted": self.drifted,
            "message": self.message,
            "checked_at": self.checked_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# VoiceTracker
# ---------------------------------------------------------------------------


class VoiceTracker:
    """Track intra-brand voice consistency via embeddings.

    Parameters
    ----------
    db:
        Database helper instance.
    llm_router:
        ``LLMRouter`` — used to generate text embeddings.
    baseline_size:
        Number of recent scripts to use as the rolling baseline.
    drift_threshold:
        Cosine-distance threshold above which drift is flagged.
    """

    def __init__(
        self,
        db: Any,
        llm_router: Any,
        baseline_size: int = DEFAULT_BASELINE_SIZE,
        drift_threshold: float = DRIFT_THRESHOLD,
    ) -> None:
        self.db = db
        self.llm_router = llm_router
        self.baseline_size = baseline_size
        self.drift_threshold = drift_threshold
        # In-memory cache: brand_id → list of embedding vectors
        self._baselines: Dict[str, List[List[float]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_voice(
        self, brand_id: str, script_text: str
    ) -> VoiceCheckResult:
        """Compare *script_text* against the brand's rolling baseline.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        script_text:
            The new script body.

        Returns
        -------
        VoiceCheckResult

        Side Effects
        ------------
        * Stores the new embedding in the ``voice_embeddings`` table.
        * Updates the in-memory baseline cache.
        """
        # Generate embedding for the new script
        new_embedding = await self._get_embedding(script_text, brand_id)

        # Load / refresh baseline
        baseline = await self._get_baseline(brand_id)

        if len(baseline) < MIN_BASELINE:
            # Not enough data to judge drift
            await self._store_embedding(brand_id, new_embedding)
            self._update_cache(brand_id, new_embedding)
            return VoiceCheckResult(
                brand_id=brand_id,
                similarity=1.0,
                drift=0.0,
                drifted=False,
                message=f"Baseline too small ({len(baseline)}/{MIN_BASELINE}); skipping drift check.",
            )

        centroid = self._compute_centroid(baseline)
        similarity = self._cosine_similarity(new_embedding, centroid)
        drift = round(1.0 - similarity, 4)
        drifted = drift > self.drift_threshold

        msg = ""
        if drifted:
            msg = (
                f"Voice drift detected for brand '{brand_id}': "
                f"drift={drift:.4f} > threshold={self.drift_threshold}"
            )
            logger.warning(msg)
        else:
            msg = f"Voice consistent for brand '{brand_id}': drift={drift:.4f}"
            logger.info(msg)

        # Persist and update cache
        await self._store_embedding(brand_id, new_embedding)
        self._update_cache(brand_id, new_embedding)

        return VoiceCheckResult(
            brand_id=brand_id,
            similarity=similarity,
            drift=drift,
            drifted=drifted,
            message=msg,
        )

    async def get_drift_history(
        self, brand_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Return recent drift measurements for dashboards.

        Parameters
        ----------
        brand_id:
            Brand to query.
        limit:
            Max rows.

        Returns
        -------
        List[Dict[str, Any]]
        """
        rows = await self.db.fetch_all(
            """
            SELECT brand_id, similarity, drift, created_at
            FROM voice_drift_log
            WHERE brand_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (brand_id, limit),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    async def _get_embedding(
        self, text: str, brand_id: str
    ) -> List[float]:
        """Generate an embedding vector for *text* via LLMRouter.

        Parameters
        ----------
        text:
            Input string.
        brand_id:
            Brand context (for routing / caching).

        Returns
        -------
        List[float]
            Embedding vector.
        """
        try:
            embedding = await self.llm_router.get_embedding(
                text=text, task="voice_tracking", brand_id=brand_id
            )
            return embedding
        except Exception as exc:
            logger.error("Embedding generation failed: %s", exc)
            # Return a zero vector as a safe fallback
            return [0.0] * 384  # typical small-model dimension

    # ------------------------------------------------------------------
    # Baseline management
    # ------------------------------------------------------------------

    async def _get_baseline(self, brand_id: str) -> List[List[float]]:
        """Return cached baseline or load from DB.

        Parameters
        ----------
        brand_id:
            Brand identifier.

        Returns
        -------
        List[List[float]]
            List of embedding vectors.
        """
        if brand_id in self._baselines:
            return self._baselines[brand_id]

        rows = await self.db.fetch_all(
            """
            SELECT embedding FROM voice_embeddings
            WHERE brand_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (brand_id, self.baseline_size),
        )
        import json

        embeddings = []
        for row in rows:
            try:
                vec = json.loads(row["embedding"])
                embeddings.append(vec)
            except (json.JSONDecodeError, KeyError):
                continue

        self._baselines[brand_id] = embeddings
        return embeddings

    def _update_cache(self, brand_id: str, embedding: List[float]) -> None:
        """Append *embedding* to the in-memory baseline, trimming to window.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        embedding:
            New embedding vector.
        """
        if brand_id not in self._baselines:
            self._baselines[brand_id] = []
        self._baselines[brand_id].insert(0, embedding)
        # Trim to rolling window size
        self._baselines[brand_id] = self._baselines[brand_id][
            : self.baseline_size
        ]

    async def _store_embedding(
        self, brand_id: str, embedding: List[float]
    ) -> None:
        """Persist an embedding to the database.

        Parameters
        ----------
        brand_id:
            Brand identifier.
        embedding:
            Vector to store.

        Side Effects
        ------------
        Inserts a row into ``voice_embeddings``.
        """
        import json

        await self.db.execute(
            """
            INSERT INTO voice_embeddings (brand_id, embedding, created_at)
            VALUES (?, ?, ?)
            """,
            (
                brand_id,
                json.dumps(embedding),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    # ------------------------------------------------------------------
    # Vector math
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_centroid(vectors: List[List[float]]) -> List[float]:
        """Compute the element-wise mean of a list of vectors.

        Parameters
        ----------
        vectors:
            Non-empty list of equal-length float lists.

        Returns
        -------
        List[float]
            Centroid vector.
        """
        if not vectors:
            return []
        dim = len(vectors[0])
        centroid = [0.0] * dim
        for vec in vectors:
            for i in range(min(dim, len(vec))):
                centroid[i] += vec[i]
        n = len(vectors)
        return [c / n for c in centroid]

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors.

        Parameters
        ----------
        a, b:
            Input vectors (same length expected).

        Returns
        -------
        float
            Similarity in [0, 1].  Returns 0.0 on degenerate input.
        """
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return max(0.0, min(dot / (norm_a * norm_b), 1.0))
