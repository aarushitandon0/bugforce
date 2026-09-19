"""Deterministic id construction shared by the pipeline lambdas.

The implementation moved to bugforge.ids so that bugforge.package -- which
also names challenges -- mints the same id as the cloud path does, instead of
a second one for the same mutation. Re-exported here because every lambda
already imports it from this module.
"""
from __future__ import annotations

from bugforge.ids import batch_id, challenge_id, site_digest

__all__ = ["batch_id", "challenge_id", "site_digest"]
