import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(request, monkeypatch):
    monkeypatch.setenv("FINAGENT_HMAC_KEY", "0" * 64)
    # Only tests explicitly marked llm may see real credentials or live-model settings.
    if request.node.get_closest_marker("llm") is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        for name in list(os.environ):
            if name.startswith("FINAGENT_LLM_"):
                monkeypatch.delenv(name)
