import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # fake_telegram importierbar

_ENV = ["DUET_TELEGRAM_TOKEN", "DUET_CHAT_ID", "DUET_NTFY_TOPIC", "DUET_NTFY_SERVER", "DUET_TIMEZONE",
        "DUET_APPROVAL_TIMEOUT_MIN", "DUET_CONFIG", "DUET_STATE_DIR", "CLAUDE_PROJECT_DIR", "XDG_CONFIG_HOME"]


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Keine echten Config-/State-Dateien anfassen."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "home"))
