"""
Result Ranker for Smart Scout Search.
Scores, ranks, and deduplicates art results across museum sources.
"""

import logging
import unicodedata
from difflib import SequenceMatcher
from typing import List, Dict, Optional

logger = logging.getLogger("artwork-display-api.ranker")


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip accents, remove articles."""
    if not text:
        return ""
    # Lowercase
    text = text.lower().strip()
    # Strip accents
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    # Remove common articles
    for article in ("the ", "a ", "an ", "le ", "la ", "les ", "de ", "het ", "een "):
        if text.startswith(article):
            text = text[len(article):]
    return text.strip()


def _fuzzy_match(a: str, b: str, threshold: float = 0.85) -> bool:
    """Check if two strings are fuzzy matches above threshold."""
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


class ResultRanker:
    """
    Scores and deduplicates results from multiple museum scouts.
    """

    def rank_and_deduplicate(
        self,
        results: List[Dict],
        intent=None
    ) -> List[Dict]:
        """
        Score each result, deduplicate across sources, and return sorted by relevance.

        Args:
            results: List of art dicts from scouts
            intent: SearchIntent from the classifier (optional)

        Returns:
            Sorted, deduplicated list of art dicts with 'relevance_score' added
        """
        if not results:
            return []

        # 1. Score each result
        for item in results:
            item['relevance_score'] = self._score(item, intent)

        # 2. Sort by relevance (highest first)
        results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)

        # 3. Deduplicate
        deduped = self._deduplicate(results)

        logger.info(
            f"[Ranker] Scored {len(results)} results, "
            f"deduped to {len(deduped)} unique works"
        )
        return deduped

    def _score(self, item: Dict, intent=None) -> float:
        """
        Score a single result on a 0–100 scale.

        Signals:
        - Artist match (30 pts): proposed_artist matches intent.canonical_name
        - Title relevance (20 pts): query terms in proposed_title
        - Highlight flag (20 pts): museum curator pick
        - Image quality (15 pts): heuristic from URL patterns
        - Metadata completeness (15 pts): title + artist + context_hints present
        """
        score = 0.0

        proposed_artist = (item.get('proposed_artist') or '').lower()
        proposed_title = (item.get('proposed_title') or '').lower()

        # --- Artist Match (30 pts) ---
        if intent and intent.query_type == "artist":
            canonical_lower = intent.canonical_name.lower()
            # Check various matching strategies
            if canonical_lower in proposed_artist or proposed_artist in canonical_lower:
                score += 30
            elif _fuzzy_match(_normalize_text(intent.canonical_name), _normalize_text(proposed_artist)):
                score += 25
            # Partial last-name match (e.g., "Van Gogh" in "Vincent Willem van Gogh")
            elif intent.original_query.lower() in proposed_artist:
                score += 20

        # --- Title Relevance (20 pts) ---
        if intent:
            query_terms = intent.original_query.lower().split()
            matched_terms = sum(1 for t in query_terms if t in proposed_title)
            if query_terms:
                score += 20 * (matched_terms / len(query_terms))

        # --- Highlight / Featured Flag (20 pts) ---
        context = item.get('context_hints', '')
        if isinstance(context, str):
            ctx_lower = context.lower()
            # Various API highlight indicators
            if any(flag in ctx_lower for flag in (
                '"ishighlight":true', '"ishighlight": true',
                '"highlight":1', '"highlight": 1',
                '"is_boosted":true', '"is_boosted": true',
            )):
                score += 20

        # --- Image Quality Heuristic (15 pts) ---
        source_url = item.get('source_url', '')
        if source_url:
            # Full resolution indicators
            if '/full/max/' in source_url or '/full/full/' in source_url:
                score += 15
            elif 'width=2000' in source_url or '/print/' in source_url:
                score += 12
            elif '/full/' in source_url:
                score += 10
            else:
                score += 5  # At least it has an image

        # --- Metadata Completeness (15 pts) ---
        completeness = 0
        if item.get('proposed_title') and item['proposed_title'] != 'Unknown':
            completeness += 1
        if item.get('proposed_artist') and item['proposed_artist'] != 'Unknown Artist':
            completeness += 1
        if item.get('context_hints'):
            completeness += 1
        score += 15 * (completeness / 3)

        return round(score, 1)

    def _deduplicate(self, results: List[Dict]) -> List[Dict]:
        """
        Remove duplicate artworks across museum sources.
        Uses fuzzy matching on normalized title + artist.
        Keeps the higher-scoring copy.
        """
        unique = []
        seen_keys = []  # List of (normalized_title, normalized_artist) tuples

        for item in results:
            norm_title = _normalize_text(item.get('proposed_title', ''))
            norm_artist = _normalize_text(item.get('proposed_artist', ''))
            item_key = f"{norm_title} by {norm_artist}"

            is_duplicate = False
            for existing_key in seen_keys:
                if _fuzzy_match(item_key, existing_key):
                    is_duplicate = True
                    break

            if not is_duplicate:
                unique.append(item)
                seen_keys.append(item_key)

        return unique
