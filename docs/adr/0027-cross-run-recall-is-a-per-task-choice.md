# Cross-run recall is a per-task choice

`_run_surface` handed every run the one-line summaries of its last three completed
runs, under "do not repeat them". Unconditional, and the three was hardcoded — nobody
chose it for any particular task.

Both halves of that hurt. A task about the present moment carries outcomes it has no
use for: a weather task is told what yesterday's weather was. And a task that genuinely
depends on its own history gets a window too short to be one. A "Python Learning
Module" task, whose prompt asks for a topic *"different to previous ones"*, repeated
five topics across fourteen runs — functools, generators, dataclasses, context
managers, structural pattern matching. Every repeat was a topic that had fallen out of
the three-run window; every topic still inside it was correctly avoided. The agent was
not failing to follow the instruction, it was following it with a note too short to
hold the answer.

We therefore make look-back **one number on the task, default off**:

```python
recall_depth: int = 0  # prior runs indexed in the surface; 0 = none, -1 = all
```

## Considered options

- **One integer, `0` none / `-1` all / `n` a count (chosen).** One field cannot
  contradict itself. `0` being both the default and the off state means the off state
  needs no clear-sentinel — unlike `model`, where `""` clears and `None` means absent,
  because `TaskPatch` filters `None` out of the patch. `0` is not `None`, so turning
  recall off travels the ordinary patch path.
- **A boolean plus a depth.** Rejected: it can represent nonsense (`recall_runs=False`
  beside `recall_depth=20`) and costs two fields in every patch and payload for nothing
  the integer doesn't already say.
- **Keep it always-on and just raise the number.** Rejected twice over. It re-imposes
  on the weather task exactly the cost this change exists to remove, and any fixed
  number only delays the repeat until the task outlives the window — "different to
  *previous* ones" is not a claim about recency.
- **Drop the injected summaries entirely; give the agent tools instead.** Rejected: the
  summaries already exist. Every completed run is summarised regardless, because the
  run list, the drawer, and channel delivery all render it — so suppressing them saves
  nothing at run time while costing the agent the index that tells it *which* run is
  worth opening. The tool (`read_run`) is the complement to the index, not a
  replacement for it.

## A failed run occupies a slot

Load-bearing sub-decision, and the reason this ADR is not purely about a setting.

A failed run used to contribute nothing at all. `_turn` returns at
`_finish(run_id, FAILED, error=…)` before it ever reaches `summarize_run`, so the
record's `summary` stays `""`, and the reader then excluded it twice more — once on
`status != COMPLETED`, once on the empty summary. Cancelled runs went the same way.

That contradicts ADR 0018, *a failed turn keeps its work*: a run that dies keeps
everything it committed first — files written, mail sent, tasks created. Hiding those
runs left the next run with a record that was quietly wrong about its own past. It
also slid the window further back: one failure and a three-slot index reaches a fourth
run deep.

So every settled run is listed. One with no summary names its state and carries the
call that opens it:

```
- run-c51 (2026-08-05) · failed, use read_run("run-c51") to check.
```

The run id repeats deliberately: the line is the call, ready to make. The `error`
string is deliberately absent — provider failures are multi-line JSON blobs that say
nothing about the user's work, which is the same call ADR 0018 makes for the chat note.

## The index states facts; the task's prompt owns the intent

The old block ended `do not repeat them`, and the first cut of this one kept the habit
(`Do not repeat work these runs did`). That is the system deciding, for every task at
once, what looking back is *for*. Avoidance is only one answer: a task that continues
yesterday's draft, updates a running tally, or extends a series wants the opposite, and
was being told not to.

It is also redundant exactly where it seemed to help. The task that motivated this ADR
already says, in its own prompt, "a topic different to previous ones" — the user had
expressed the intent, and the surface was restating it. Where the user has *not*
expressed it, inventing one on their behalf is worse than silence.

So the index reports and stops: what the earlier runs were, and that `read_run` opens
one. Whether to avoid them, build on them, or ignore them is the task prompt's to say —
that prompt is the user's, and it is the only place this intent belongs. The same rule
governs the tool descriptions the agent reads when it creates a task from a chat: they
say to set the depth when the prompt *refers to* earlier runs, not when the agent
guesses a purpose.

Because the render now depends on status, the filter moved out of the store: it returns
every settled run and `_run_surface` decides how each one reads. Prompt shaping is not
the store's business.

## Consequences

- **`-1` is bounded by a byte budget, not by a run count.** A weekday task at ~130
  chars per summary is ~34KB of prompt after a year and grows every run. Over budget
  the oldest entries drop and the header says how many, so a truncated index never
  reads as a complete one. A cap that announces itself is the point; the silent version
  is the bug being fixed.
- **Existing tasks are stamped once on upgrade.** `backfill_recall` gives records
  written before the field existed `3` when their schedule is cron, `0` otherwise, so
  the day this ships nothing behaves differently. It keys off the absence of the JSON
  key, so a user who later chooses `0` is never overwritten — the same
  idempotence-through-absence `strip_workdirs` uses.
- **`TasksConfig` is deleted.** `history_runs` and the three `digest_*` knobs were
  declared, env-overridable, and read nowhere — orphans of `tasks/history.py`, removed
  in `c3c81f6`. Depth living on the task leaves them nothing to do, and its docstring
  pointed at a plan document that no longer exists.
- **`read_run` is a new system tool.** The plumbing already worked — a run's thread is
  an ordinary chat on `task-run:{run_id}` and `Gateway.transcript` never filtered
  internal streams — but nothing told the agent those streams existed or what they were
  called. Unlike `read_chat`, it does not truncate to a tail: a run's output is the
  thing being read, not conversational scrollback.
- **This amends ADR 0018 rather than contradicting it.** Same premise, one step
  further: from "the chat keeps the record" to "the next run can see that it exists".
