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


@pytest.fixture(autouse=True)
def isolated_key_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Every canon entry point now loads provider keys from the key file
    (``canon.keyfile.apply``) and drops shell exports of key names. Point it
    at a file that does not exist, so a developer's real ``./.env`` is never
    loaded into a test, and put the process's key variables back afterwards
    (``apply`` edits ``os.environ`` directly, outside ``monkeypatch``)."""
    import os

    from canon import keyfile

    monkeypatch.setenv(keyfile.ENV_FILE_VAR, str(tmp_path_factory.mktemp("keys") / "absent.env"))
    names = keyfile.key_names()
    before = {n: os.environ.get(n) for n in names}
    keyfile._reset_for_tests()
    yield
    for n, v in before.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v
    keyfile._reset_for_tests()
