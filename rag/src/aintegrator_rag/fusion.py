"""Rank fusion shared by the in-memory and Postgres stores."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

__all__ = ["RRF_K", "reciprocal_rank_fusion"]

# A small corpus needs a smaller constant than the classic k=60: with only a few
# dozen chunks, k=60 flattens rank differences and rewards any chunk appearing in
# both lists over a chunk ranked first by one of them.
RRF_K = 10


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = RRF_K
) -> list[tuple[str, float]]:
    """Fuse ranked ID lists with reciprocal rank fusion.

    Ties break on the chunk ID so two stores receiving the same rankings return
    the same order. A chunk present in both rankings is rewarded by both.
    """
    if k < 0:
        raise ValueError("k must be non-negative")
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
