"""
Cross-brand content deduplication for AutoFarm Zero — Success Guru Network v6.0.

Ensures content is genuinely distinct across brands. Platforms (especially Meta)
detect coordinated content networks when similar content is posted from
related accounts.

Maintains a rolling window of recent scripts per brand. New scripts are
rejected if cosine similarity >0.7 to any other brand's recent content.

RULE: CrossBrandDeduplicator.check_script_uniqueness() runs on every new
script. Rejected if >0.7 similarity.
"""

import logging
from datetime import datetime

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from database.db import Database

logger = logging.getLogger(__name__)


class CrossBrandDeduplicator:
    """
    Ensures content is genuinely distinct across brands.

    Uses TF-IDF vectorization and cosine similarity to compare new
    scripts against a rolling window of recent scripts from all OTHER
    brands. Rejects scripts that are too similar to prevent platforms
    from detecting coordinated content networks.
    """

    SIMILARITY_THRESHOLD = 0.7
    WINDOW_SIZE = 50  # Scripts per brand to compare against

    def __init__(self, similarity_threshold: float = None):
        """
        Initializes the CrossBrandDeduplicator.

        Parameters:
            similarity_threshold: Override for the default 0.7 threshold.
        """
        self.db = Database()
        if similarity_threshold is not None:
            self.SIMILARITY_THRESHOLD = similarity_threshold

    def check_script_uniqueness(self, script_text: str,
                                brand_id: str) -> dict:
        """
        Checks if a script is sufficiently unique compared to other brands'
        recent content.

        Parameters:
            script_text: Full text of the new script to check.
            brand_id: Brand this script belongs to (excluded from comparison).

        Returns:
            Dictionary with keys:
            - unique (bool): Whether the script passes the uniqueness check.
            - most_similar_brand (str|None): Brand with highest similarity.
            - similarity (float): Highest cosine similarity score found.
            - checked_against (int): Number of scripts compared.

        Side effects:
            Logs the dedup check result to the dedup_checks table.
        """
        if not script_text or len(script_text.strip()) < 20:
            return {
                'unique': True,
                'most_similar_brand': None,
                'similarity': 0.0,
                'checked_against': 0,
            }

        # Get recent scripts from OTHER brands
        other_scripts = self.db.query(
            """SELECT brand_id, script_text FROM scripts
               WHERE brand_id != ? AND created_at > datetime('now', '-30 days')
               AND script_text IS NOT NULL AND length(script_text) > 20
               ORDER BY created_at DESC LIMIT ?""",
            (brand_id, self.WINDOW_SIZE * 5)
        )

        if not other_scripts:
            result = {
                'unique': True,
                'most_similar_brand': None,
                'similarity': 0.0,
                'checked_against': 0,
            }
            self._log_check(brand_id, result)
            return result

        # Build corpus: new script + all comparison scripts
        corpus = [script_text] + [r['script_text'] for r in other_scripts]

        try:
            # TF-IDF vectorization
            vectorizer = TfidfVectorizer(
                stop_words='english',
                max_features=500,
                min_df=1,
                max_df=0.95,
            )
            tfidf_matrix = vectorizer.fit_transform(corpus)

            # Compute cosine similarity between new script and all others
            similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]

            max_idx = int(np.argmax(similarities))
            max_sim = float(similarities[max_idx])

            result = {
                'unique': max_sim < self.SIMILARITY_THRESHOLD,
                'most_similar_brand': other_scripts[max_idx]['brand_id'],
                'similarity': round(max_sim, 3),
                'checked_against': len(other_scripts),
            }

        except Exception as e:
            logger.error(f"Dedup check failed: {e}")
            # Don't block on check failure
            result = {
                'unique': True,
                'most_similar_brand': None,
                'similarity': 0.0,
                'checked_against': 0,
                'error': str(e),
            }

        self._log_check(brand_id, result)

        if not result['unique']:
            logger.warning(
                "Script rejected — too similar to another brand",
                extra={
                    'brand_id': brand_id,
                    'most_similar_brand': result['most_similar_brand'],
                    'similarity': result['similarity'],
                    'threshold': self.SIMILARITY_THRESHOLD,
                }
            )
        else:
            logger.debug(
                "Script passed dedup check",
                extra={
                    'brand_id': brand_id,
                    'max_similarity': result['similarity'],
                    'checked_against': result['checked_against'],
                }
            )

        return result

    def _log_check(self, brand_id: str, result: dict) -> None:
        """
        Logs a dedup check to the database.

        Parameters:
            brand_id: Brand whose script was checked.
            result: Check result dictionary.

        Side effects:
            Inserts a record into the dedup_checks table.
        """
        try:
            self.db.insert('dedup_checks', {
                'brand_id': brand_id,
                'most_similar_brand': result.get('most_similar_brand'),
                'similarity_score': result.get('similarity', 0.0),
                'passed': 1 if result.get('unique', True) else 0,
            })
        except Exception as e:
            logger.debug(f"Failed to log dedup check: {e}")

    def get_brand_similarity_matrix(self) -> dict:
        """
        Computes pairwise similarity between all brands' recent content.

        Useful for monitoring whether brands are drifting too close together.

        Returns:
            Dictionary with brand pairs as keys and average similarity as values.
        """
        from config.settings import get_brand_ids
        brand_ids = get_brand_ids()

        # Get recent scripts per brand
        brand_scripts = {}
        for brand_id in brand_ids:
            scripts = self.db.query(
                """SELECT script_text FROM scripts
                   WHERE brand_id = ? AND created_at > datetime('now', '-30 days')
                   AND script_text IS NOT NULL
                   ORDER BY created_at DESC LIMIT ?""",
                (brand_id, self.WINDOW_SIZE)
            )
            brand_scripts[brand_id] = [s['script_text'] for s in scripts]

        matrix = {}
        for i, brand_a in enumerate(brand_ids):
            for brand_b in brand_ids[i + 1:]:
                scripts_a = brand_scripts.get(brand_a, [])
                scripts_b = brand_scripts.get(brand_b, [])

                if not scripts_a or not scripts_b:
                    matrix[f"{brand_a}:{brand_b}"] = 0.0
                    continue

                try:
                    corpus = scripts_a + scripts_b
                    vectorizer = TfidfVectorizer(
                        stop_words='english', max_features=300
                    )
                    tfidf = vectorizer.fit_transform(corpus)

                    # Average similarity between the two groups
                    sims = cosine_similarity(
                        tfidf[:len(scripts_a)],
                        tfidf[len(scripts_a):]
                    )
                    avg_sim = float(np.mean(sims))
                    matrix[f"{brand_a}:{brand_b}"] = round(avg_sim, 3)
                except Exception:
                    matrix[f"{brand_a}:{brand_b}"] = 0.0

        return matrix

    def get_stats(self) -> dict:
        """
        Returns deduplication statistics.

        Returns:
            Dictionary with total checks, passes, rejections, and recent rejection rate.
        """
        total = self.db.query_one(
            "SELECT COUNT(*) as cnt FROM dedup_checks"
        )
        passed = self.db.query_one(
            "SELECT COUNT(*) as cnt FROM dedup_checks WHERE passed = 1"
        )
        recent_rejected = self.db.query_one(
            """SELECT COUNT(*) as cnt FROM dedup_checks
               WHERE passed = 0 AND checked_at > datetime('now', '-7 days')"""
        )
        recent_total = self.db.query_one(
            """SELECT COUNT(*) as cnt FROM dedup_checks
               WHERE checked_at > datetime('now', '-7 days')"""
        )

        total_cnt = total['cnt'] if total else 0
        passed_cnt = passed['cnt'] if passed else 0
        recent_rej = recent_rejected['cnt'] if recent_rejected else 0
        recent_tot = recent_total['cnt'] if recent_total else 0

        return {
            'total_checks': total_cnt,
            'total_passed': passed_cnt,
            'total_rejected': total_cnt - passed_cnt,
            'rejection_rate': round((total_cnt - passed_cnt) / max(total_cnt, 1), 3),
            'recent_7d_rejected': recent_rej,
            'recent_7d_total': recent_tot,
            'threshold': self.SIMILARITY_THRESHOLD,
        }
