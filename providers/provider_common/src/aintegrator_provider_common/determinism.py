"""Deterministic pseudo-random data derived from a seed and an address."""

from __future__ import annotations

import hashlib

__all__ = ["digest_int", "pick", "rate_from_hash", "seeded_hash"]


def seeded_hash(seed: int, *parts: object) -> bytes:
    payload = "|".join([str(seed), *(str(part).strip().lower() for part in parts)]).encode()
    return hashlib.sha256(payload).digest()


def digest_int(seed: int, *parts: object) -> int:
    return int.from_bytes(seeded_hash(seed, *parts)[:8], "big")


def pick[T](options: tuple[T, ...], seed: int, *parts: object) -> T:
    """Pick one option deterministically; ``options`` must be non-empty."""
    if not options:
        raise ValueError("options must not be empty")
    return options[digest_int(seed, *parts) % len(options)]


def rate_from_hash(seed: int, *parts: object, minimum: int = 1, maximum: int = 200) -> int:
    """Return an inclusive integer in ``[minimum, maximum]``."""
    if minimum > maximum:
        raise ValueError("minimum must not exceed maximum")
    return minimum + digest_int(seed, *parts) % (maximum - minimum + 1)
