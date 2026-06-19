# AG2 bug: zero-argument tools break tool calling on the OpenAI realtime `LiveAgent`

> **Status: RESOLVED upstream** — fixed in ag2ai/ag2#2985 (no-arg tools now serialise to
> `{"type": "object", "properties": {}}`). AG2 Assistant pins `ag2 @ git main` and has removed
> its interim workaround. Kept as a historical record of the investigation.

**Component:** `autogen.beta` — `LiveAgent` + `autogen.beta.live.openai` (OpenAI realtime)
**Severity:** High — a single no-arg tool silently disables **all** tool calling for the session.
**Affected provider:** OpenAI realtime (`openai.RealTimeConfig`). Gemini realtime is unaffected (tolerant).
**Environment:** `autogen` 0.13.4, `openai` (python) 2.43.0, Python 3.13.7, model `gpt-realtime-2`.

## Summary

A `LiveAgent` tool that takes **no arguments** serializes its JSON Schema as
`parameters: {"type": "null"}`. OpenAI's realtime API rejects a function tool
whose `parameters` is not an object schema, and — critically — when one tool in
the `session.update` tools array is invalid, the provider drops the **entire**
tools array. The model then runs with **no tools at all**: it never emits a
function call and typically says things like *"I can't access that right now."*

Because the failure is silent (no exception surfaced to the app; the session
otherwise works and the model still talks), this is very hard to diagnose from
the application side.

## Impact

Any realtime agent that includes at least one zero-arg tool loses tool calling
entirely — including its *other*, well-formed tools. Common zero-arg tools that
trigger it: `current_time()`, `end_call()`, `list_items()`, etc.

In our app (AG2 Assistant) the voice agent exposed four zero-arg tools alongside a
`ask_assistant(request: str)` delegate; the model never called any tool on
OpenAI, while the identical agent worked on Gemini.

## Root cause

`_tool_schema_to_session_tool` passes the function's parameters straight through:

```python
# autogen/beta/live/openai.py:324
def _tool_schema_to_session_tool(t: ToolSchema) -> RealtimeFunctionToolParam:
    if isinstance(t, FunctionToolSchema):
        return RealtimeFunctionToolParam(
            type="function",
            name=t.function.name,
            description=t.function.description,
            parameters=t.function.parameters,   # <-- {"type": "null"} for a no-arg tool
        )
```

For a zero-arg tool, `t.function.parameters` is `{"type": "null"}` rather than an
empty object schema. So the upstream defect is in **how the function tool schema
is generated for no-arg callables** (`FunctionToolSchema.function.parameters`);
the OpenAI mapper merely forwards it.

OpenAI realtime expects function `parameters` to be a JSON Schema **object**
(e.g. `{"type": "object", "properties": {}}`). `{"type": "null"}` is invalid in
that position and the tool (and array) is rejected.

## Minimal reproduction

### A. Headless — show the bad schema (no API key, no audio)

```python
import asyncio
from autogen.beta import tool
from autogen.beta.live.openai import _tool_schema_to_session_tool
from autogen.beta.context import ConversationContext
from autogen.beta.stream import MemoryStream

@tool
def current_time() -> str:
    """A zero-argument tool."""
    return "now"

@tool
async def ask_assistant(request: str) -> str:
    """A tool with a parameter."""
    return "ok"

async def main():
    ctx = ConversationContext(stream=MemoryStream(id="x"))
    for t in (current_time, ask_assistant):
        for s in await t.schemas(ctx):
            print(t.name, "->", _tool_schema_to_session_tool(s)["parameters"])

asyncio.run(main())
```

Output:

```
current_time  -> {'type': 'null'}                                  # invalid for OpenAI
ask_assistant -> {'type': 'object', 'properties': {'request': {...}}, 'required': ['request'], 'type': 'object'}
```

### B. Live — observe tool calling break

```python
import asyncio
from autogen.beta.live import (
    LiveAgent, OpenAIRealTimeConfig, SoundDevicePlayer, SoundDeviceRecorder,
)
from autogen.beta.live import openai as oai

agent = LiveAgent(
    name="assistant",
    prompt="When asked the weather you MUST call get_weather. Never say you can't.",
    config=OpenAIRealTimeConfig("gpt-realtime-2", output=oai.AudioOutput(voice="marin")),
)

@agent.tool
async def get_weather(location: str) -> str:
    """Get the weather for a location."""
    print(">>> get_weather", location)
    return "18C and sunny"

@agent.tool
def current_time() -> str:                 # <-- remove this line and get_weather starts working
    """The current time."""
    return "now"

async def main():
    async with (
        agent.run() as context,
        SoundDevicePlayer(context=context),
        SoundDeviceRecorder(context=context),
    ):
        await asyncio.Future()

asyncio.run(main())
```

Ask *"What's the weather in Sydney?"*:

- **With** the zero-arg `current_time` present → `get_weather` is **never** called; the
  model says it can't access the weather.
- **Remove** `current_time` → `get_weather` is called normally.

This isolates it cleanly: the only change is the presence of one zero-arg tool,
yet it disables the *other* (well-formed) tool too.

## Suggested fix

Emit a valid empty-object schema for no-arg tools. Either (preferred) at schema
generation so every provider benefits:

```python
# wherever FunctionToolSchema.function.parameters is built:
parameters = parameters or {"type": "object", "properties": {}}
# and never {"type": "null"} for a callable with no parameters
```

…or defensively in the OpenAI mapper:

```python
# autogen/beta/live/openai.py  _tool_schema_to_session_tool
params = t.function.parameters
if not isinstance(params, dict) or params.get("type") != "object":
    params = {"type": "object", "properties": {}}
return RealtimeFunctionToolParam(..., parameters=params)
```

A schema-generation fix is better — `{"type": "null"}` for a no-arg tool is
questionable for any consumer; OpenAI realtime is just the strict one that
surfaces it. Worth a regression test asserting a no-arg `@tool` serializes to
`{"type": "object", "properties": {}}`.

## Interim workaround (downstream, AG2 Assistant)

Until the upstream fix lands, AG2 Assistant monkeypatches the mapper at its OpenAI
boundary to coerce the null schema (idempotent; removed once AG2 is re-pinned):

```python
from autogen.beta.live import openai as oai
_orig = oai._tool_schema_to_session_tool
def _patched(t):
    d = _orig(t)
    if isinstance(d.get("parameters"), dict) and d["parameters"].get("type") == "null":
        d["parameters"] = {"type": "object", "properties": {}}
    return d
oai._tool_schema_to_session_tool = _patched
```

## Notes

- Gemini realtime (`gemini.RealTimeConfig`) accepts the same agent/tools without
  issue, so this is specific to the OpenAI realtime path's (correct) strictness.
- The failure is silent end-to-end: no exception reaches the application, the
  session connects and the model responds normally — it just has no tools. Even a
  warning logged when the provider rejects `session.update` tools would have made
  this far easier to find.
