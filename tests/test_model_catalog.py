"""Tests for the ACP model-catalog probe feeding the Settings picker."""

import asyncio

import pytest

from assistant.coding import model_catalog


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(model_catalog, "_cache", {})
    monkeypatch.setattr(model_catalog, "_inflight", {})


def test_parse_codex_models_shape():
    models, current = model_catalog._parse(
        {
            "models": {
                "currentModelId": "gpt-5.6-sol[low]",
                "availableModels": [
                    {
                        "modelId": "gpt-5.6-sol[low]",
                        "name": "GPT-5.6-Sol (low)",
                        "description": "d",
                    },
                    {"modelId": "gpt-5.5[high]", "name": "GPT-5.5 (high)"},
                    {"name": "ghost, no id"},
                ],
            }
        }
    )
    assert [m.id for m in models] == ["gpt-5.6-sol[low]", "gpt-5.5[high]"]
    assert models[0].description == "d" and models[1].description == ""
    assert current == "gpt-5.6-sol[low]"


def test_parse_claude_config_options_shape():
    models, current = model_catalog._parse(
        {
            "configOptions": [
                {"id": "mode", "category": "mode", "options": [{"value": "ask", "name": "Ask"}]},
                {
                    "id": "model",
                    "category": "model",
                    "currentValue": "opus[1m]",
                    "options": [
                        {"value": "default", "name": "Default (recommended)", "description": "…"},
                        {"value": "opus[1m]", "name": "Opus", "description": "1M context"},
                        {"value": "sonnet", "name": "Sonnet"},
                        {"name": "ghost, no value"},
                    ],
                },
            ]
        }
    )
    # The adapter's "default" row is dropped: for us that case IS an empty model
    # (no model env derived), and sending the literal "default" would just fail.
    assert [m.id for m in models] == ["opus[1m]", "sonnet"]
    assert models[0].description == "1M context"
    assert current == "opus[1m]"


def test_parse_normalises_default_current_to_empty():
    # currentValue "default" means "whatever the CLI is set to" → our empty model.
    models, current = model_catalog._parse(
        {
            "configOptions": [
                {
                    "category": "model",
                    "currentValue": "default",
                    "options": [{"value": "default", "name": "Default"}, {"value": "sonnet"}],
                }
            ]
        }
    )
    assert [m.id for m in models] == ["sonnet"]
    assert current == ""


def test_parse_tolerates_odd_shapes():
    assert model_catalog._parse({}) == ([], "")
    assert model_catalog._parse({"models": "nope"}) == ([], "")
    assert model_catalog._parse({"models": {"availableModels": None}}) == ([], "")
    assert model_catalog._parse({"configOptions": [{"category": "mode"}]}) == ([], "")


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakeStdin:
    def write(self, _data):
        pass

    async def drain(self):
        pass


class _FakeProc:
    """Just enough of an asyncio subprocess to drive _probe's JSON-RPC loop."""

    def __init__(self, lines):
        self.stdin, self.stdout = _FakeStdin(), _FakeStdout(lines)
        self.killed = False

    def kill(self):
        self.killed = True

    async def wait(self):
        return 0


def test_probe_skips_non_json_and_notification_lines(monkeypatch):
    proc = _FakeProc(
        [
            b"npm notice new version available\n",  # launcher noise on stdout
            b"not json at all\n",
            b'{"jsonrpc":"2.0","id":1,"result":{}}\n',
            b'{"jsonrpc":"2.0","method":"session/update","params":{}}\n',  # notification
            b'{"jsonrpc":"2.0","id":2,"result":{"models":{"currentModelId":"m",'
            b'"availableModels":[{"modelId":"m","name":"M"}]}}}\n',
        ]
    )

    async def fake_exec(*_a, **_k):
        return proc

    monkeypatch.setattr(model_catalog.detect, "resolve_agent", lambda name: _agent_info())
    monkeypatch.setattr(model_catalog.asyncio, "create_subprocess_exec", fake_exec)
    models, current = asyncio.run(model_catalog._probe("codex"))
    # A stray banner line must not sink the whole catalog.
    assert [m.id for m in models] == ["m"] and current == "m"
    assert proc.killed is True  # the probe never leaves the adapter running


def test_probe_raises_when_the_adapter_closes_the_pipe(monkeypatch):
    async def fake_exec(*_a, **_k):
        return _FakeProc([])

    monkeypatch.setattr(model_catalog.detect, "resolve_agent", lambda name: _agent_info())
    monkeypatch.setattr(model_catalog.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="closed the pipe"):
        asyncio.run(model_catalog._probe("codex"))


def _agent_info():
    from assistant.coding.detect import AgentInfo

    return AgentInfo(name="codex", label="Codex", command=["codex-acp"], available=True, path="/x")


def test_list_models_caches_per_agent(monkeypatch):
    calls = []

    async def fake_probe(agent):
        calls.append(agent)
        return [model_catalog.CatalogModel(f"{agent}-m", agent, "")], f"{agent}-m"

    monkeypatch.setattr(model_catalog, "_probe", fake_probe)
    asyncio.run(model_catalog.list_models("codex"))
    asyncio.run(model_catalog.list_models("codex"))  # TTL cache absorbs the repeat
    asyncio.run(model_catalog.list_models("claude"))  # separate cache slot
    assert calls == ["codex", "claude"]
    asyncio.run(model_catalog.list_models("codex", refresh=True))
    assert calls == ["codex", "claude", "codex"]


def test_list_models_never_caches_empty(monkeypatch):
    calls = {"n": 0}

    async def fake_probe(agent):
        calls["n"] += 1
        return [], ""

    monkeypatch.setattr(model_catalog, "_probe", fake_probe)
    asyncio.run(model_catalog.list_models("codex"))
    asyncio.run(model_catalog.list_models("codex"))
    # The user may be mid-`npm i -g`: an empty catalog must be re-probed.
    assert calls["n"] == 2


def test_list_models_shares_one_probe_between_concurrent_callers(monkeypatch):
    calls = {"n": 0}

    async def fake_probe(agent):
        calls["n"] += 1
        await asyncio.sleep(0)  # yield: both callers are in flight at once
        return [model_catalog.CatalogModel("m", "M", "")], "m"

    monkeypatch.setattr(model_catalog, "_probe", fake_probe)

    async def both():
        return await asyncio.gather(
            model_catalog.list_models("codex"), model_catalog.list_models("codex")
        )

    a, b = asyncio.run(both())
    # Two form opens (or a double render) must not spawn two adapter subprocesses.
    assert calls["n"] == 1
    assert a == b


def test_list_models_clears_inflight_after_failure(monkeypatch):
    calls = {"n": 0}

    async def boom(agent):
        calls["n"] += 1
        raise RuntimeError("adapter closed the pipe")

    monkeypatch.setattr(model_catalog, "_probe", boom)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(model_catalog.list_models("codex"))
    # A failed probe must not leave a poisoned in-flight entry behind.
    assert calls["n"] == 2
    assert model_catalog._inflight == {}


def test_unavailable_reason(monkeypatch):
    monkeypatch.setattr(model_catalog.detect, "bridge_endpoint", lambda: None)
    monkeypatch.setattr(model_catalog.detect, "resolve_agent", lambda name: object())
    assert model_catalog.unavailable_reason("codex") == ""
    monkeypatch.setattr(model_catalog.detect, "resolve_agent", lambda name: None)
    assert model_catalog.unavailable_reason("codex") == "adapter_missing"
    # A bridge means the adapter runs on the HOST — no local spawn, no catalog,
    # even though the entry itself is usable in that mode.
    monkeypatch.setattr(model_catalog.detect, "bridge_endpoint", lambda: object())
    assert model_catalog.unavailable_reason("codex") == "bridge"


def test_as_view_shape():
    view = model_catalog.as_view([model_catalog.CatalogModel("a[low]", "A (low)", "d")], "a[low]")
    assert view == {
        "models": [{"id": "a[low]", "name": "A (low)", "description": "d"}],
        "current": "a[low]",
        "reason": "",
    }
    assert model_catalog.as_view([], "", "adapter_missing") == {
        "models": [],
        "current": "",
        "reason": "adapter_missing",
    }
