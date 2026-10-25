import pytest


@pytest.fixture(autouse=True)
def replay_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_DEMO_ANCHOR", "replay")
