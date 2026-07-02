"""Behaviour observers — AG2-native guards on the agent's event stream.

Observers watch the stream and emit `ObserverAlert`s, which ride the same stream
the GUI projects (so they surface as a note in the thread and persist in the
event log). This first cut focuses on **stuck turns**:

- `LoopDetector` (AG2 built-in): the same tool called repeatedly with *identical*
  arguments — a tight retry loop.
- `ToolChurnObserver`: a turn that makes *many* tool calls without producing an
  answer — the flailing case (e.g. 20+ varied searches) the LoopDetector misses
  because the arguments differ each time.

Observe-only for now: alerts surface + persist but don't halt the turn or get
injected into the model (no `AlertPolicy` wired yet).
"""

from ag2.annotations import Context
from ag2.events import (
    BaseEvent,
    ModelResponse,
    ObserverAlert,
    Severity,
    ToolCallEvent,
)
from ag2.observers import BaseObserver, LoopDetector
from ag2.watch import EventWatch

# A turn that calls this many tools without answering is treated as stuck/flailing.
_CHURN_THRESHOLD = 20


class ToolChurnObserver(BaseObserver):
    """Warn when one turn makes too many tool calls without producing a final
    answer — a flailing/stuck turn (e.g. the 27 varied Gmail/Drive searches that
    `LoopDetector` misses because each call's arguments differ).

    Counts `ToolCallEvent`s across the turn and resets when the turn produces a
    `ModelResponse` with no further tool calls (a final answer). Fires once per
    turn (until reset) at the threshold.
    """

    def __init__(self, threshold: int = _CHURN_THRESHOLD, *, name: str = "tool-churn") -> None:
        super().__init__(name, watch=EventWatch((ToolCallEvent, ModelResponse)))
        self._threshold = threshold
        self._count = 0
        self._flagged = False

    async def process(self, events: list[BaseEvent], ctx: Context) -> ObserverAlert | None:
        for event in events:
            if isinstance(event, ToolCallEvent):
                self._count += 1
                if self._count >= self._threshold and not self._flagged:
                    self._flagged = True
                    return ObserverAlert(
                        source=self.name,
                        severity=Severity.WARNING,
                        message=(
                            f"This turn has made {self._count} tool calls without "
                            "producing an answer — it may be stuck or flailing."
                        ),
                        data={"tool_calls": self._count},
                    )
            elif isinstance(event, ModelResponse):
                # A response with no further tool calls ends the turn → reset.
                if not (event.tool_calls and event.tool_calls.calls):
                    self._count = 0
                    self._flagged = False
        return None


def build_observers() -> list:
    """The stuck-turn observer set wired onto the assistant's agents."""
    return [LoopDetector(), ToolChurnObserver()]
