"""Перед импортом llm поднимаем фейковый GigaChat и направляем на него адреса API."""
import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fake_gigachat import serve  # noqa: E402

_server, _state = serve("ok", port=0)
_port = _server.server_address[1]
threading.Thread(target=_server.serve_forever, daemon=True).start()

os.environ.update({
    "GIGACHAT_CREDENTIALS": "fake-key",
    "GIGACHAT_AUTH_URL": f"http://127.0.0.1:{_port}/api/v2/oauth",
    "GIGACHAT_BASE_URL": f"http://127.0.0.1:{_port}/v1",
    "GIGACHAT_TIMEOUT": "1",
    "GIGACHAT_CA_BUNDLE": "",
})


@pytest.fixture
def fake(monkeypatch):
    """Возвращает состояние фейка: fake["mode"] = "error500" и т. п.; счётчик вызовов сбрасывается."""
    import llm
    monkeypatch.setattr(llm, "RETRY_PAUSE_SECONDS", 0)
    llm.reset_token_cache()
    _state["mode"] = "ok"
    _state["calls"] = 0
    return _state
