"""Faithfulness grounding — the verifier sees what the tools actually returned."""

from autogen.beta.events import ToolResult, ToolResultEvent

from agclaw.tasks.executor import _result_text, _retrieved_evidence


class _Hist:
    def __init__(self, events):
        self._events = events

    async def get_events(self):
        return self._events


class _Reply:
    def __init__(self, events):
        self.history = _Hist(events)


def test_result_text_unwraps_parts():
    r = ToolResult("OpenAI valued at $157B")
    assert "OpenAI valued at $157B" in _result_text(r)
    assert _result_text("plain string") == "plain string"
    assert _result_text(None) == ""


async def test_retrieved_evidence_collects_search_and_fetch():
    events = [
        ToolResultEvent(name="web_search", result=ToolResult("Anthropic ~$40B (2024)")),
        ToolResultEvent(name="web_fetch", result=ToolResult("OpenAI ~$157B round")),
        ToolResultEvent(name="read_file", result=ToolResult("irrelevant local file")),
    ]
    evidence = await _retrieved_evidence(_Reply(events))
    assert "Anthropic ~$40B" in evidence
    assert "OpenAI ~$157B" in evidence
    assert "irrelevant local file" not in evidence  # only search/fetch tools count


async def test_retrieved_evidence_empty_when_uninspectable():
    class _Bad:
        @property
        def history(self):
            raise RuntimeError("no history")

    assert await _retrieved_evidence(_Bad()) == ""  # never raises → no false reject
