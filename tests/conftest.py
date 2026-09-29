"""Suite-wide fixtures.

``no_asset_backoff``: the asset retry loop (``canon.backends.failures``)
sleeps 1 + 2 + 4 s between the retries of a RETRYABLE failure, and an
exception the classifier cannot place is retryable — so every exploding
fake producer in the platformer tests (``RuntimeError("boom")`` per tile,
sprite, splash) would wait seven real seconds per asset. Measured before
this fixture: one splash-failure test took 193 s. The loop reads the
backoff constant at call time, so zeroing it here keeps every retry
(counts, attempts, the failure records) and drops only the wait. A test
that wants the real schedule passes ``backoff=`` explicitly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_asset_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    import canon.backends.failures as failures

    monkeypatch.setattr(failures, "ASSET_BACKOFF_SECONDS", (0.0,))
