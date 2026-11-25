import json
import socket
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture(autouse=True)
def _network_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("integration"):
        return

    def _blocked(self: socket.socket, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("network disabled")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
