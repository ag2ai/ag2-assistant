# AG2 Assistant Task Management — Design

A persistent, user-facing task system: small ad-hoc jobs ("any unread emails?")
and big multi-step jobs ("research the Anthropic & OpenAI IPOs and prepare a
presentation") are first-class, tracked **task** primitives.

## Decisions (from design Q&A)

- **Scheduling:** tasks can run now, at a future time (one-shot), or on a
  recurrence (e.g. daily) — a scheduler is in scope.
- **Concurrency:** several tasks run in the background concurrently, up to a cap.
- **Intake (HITL):** for any *non-trivial* task, AG2 Assistant first asks a short set of
  clarifying questions via HITL on the triggering channel, then plans and runs.
  Trivial tasks skip intake. The agent classifies trivial vs non-trivial.
- **Subtasks:** durable, nestable task tree from Phase 1; cancelling a task
  cancels all descendants immediately.

## How this relates to AG2 (app-layer, not native tasking)

This durable task system is an **application layer** AG2 Assistant owns — it is *not*
built on AG2's `Task` primitive. AG2's `Task` is agent-owned and, by design, the
framework "does not assign or schedule them" (`autogen.beta.task`), so it doesn't
provide the durable store, scheduler, or tree we need. The engine here uses none
of `Task` / `TaskConfig` / `run_subtasks` / `checkpoint`.

What it *does* build on AG2: each task runs on a real AG2 `Agent`, and task
lifecycle is expressed through the **AG2 Beta event vocabulary** on a `Stream`
(persisted via `EventLogWriter`), so the same recall/replay machinery as chats
applies. Everything else is ours: the durable task **store + tree**, the
background **runner** with **immediate cascading cancel**, the HITL **intake**,
the **scheduler**, the gateway **task API**, the **GUI Tasks view**, **channel**
triggering/routing, and the task **observer/memory**.

(If AG2's task/subagent primitives later grow durable scheduling, revisit this
per the build-on-main policy — but today the app layer is the right call.)

> #16 (custom AG2 network *channel adapter* for task delivery): researched —
> not a fit. The network is agent↔agent orchestration; our tasks are user↔agent.
> We build a dedicated task layer instead (Hub kept in reserve for cross-machine).

## Model

A `Task` record (persisted as JSON in `~/.ag2assistant/tasks.db`):

- `id`, `title`, `description`
- `status`: pending | scheduled | awaiting_input | planning | running |
  completed | failed | cancelled
- `parent_id` (None for roots) → the tree
- `objective` — definition of done (distinct from `description`, the raw request)
- `deliverables` — concrete promised outputs, each `{id, description, criteria,
  status: pending|produced|accepted|rejected, asset, notes}`; `auto_accept`
  controls whether a *produced* deliverable needs explicit user sign-off
- `created_at`, `started_at`, `ended_at`
- `scheduled_for` (one-shot ISO time) / `recurrence` (e.g. `daily@09:00`)
- `progress`: list of `{at, message, pct?}`
- `result`, `error`
- `origin_channel`, `origin_session` (where it was triggered → HITL routing)
- `hitl_channel` (override for where to ask, default = origin)
- `capability` (tag for recall), `plan` (planned steps/subtasks),
  `intake` (clarifying Q&A)
- `assets`: list of `{name, path, kind}`
- `stream_id`: per-task event-log id (full history for recall/resume)

## When is a task "done"?

Completion is **objective-driven, not "the agent stopped"**. `TaskStore.is_complete`
returns true only when:
1. every **deliverable** is satisfied — `accepted`, or `produced` + `auto_accept`
   (a produced deliverable should meet its `criteria`, checked by the runner via
   self-verification, or by the user when `auto_accept=False`), **and**
2. every **subtask** (descendant) is itself `completed`.

The runner uses this gate before marking a task `completed`; unmet criteria →
rework (re-open the deliverable / spawn a fix subtask) rather than a false done.
The HITL **intake** is where objective + deliverables + criteria get pinned down
for non-trivial tasks; they also become the **recall** template (#6/#7).

## Amending a task ("oh and add SpaceX to that IPO research")

Tasks are mutable scope, not frozen plans:
- `update(task_id, **fields, note=...)` patches title/objective/deliverables/etc.
  (id/parent_id/created_at protected); `add_subtask` / `add_deliverable` /
  `remove_deliverable` change the tree and outputs.
- Because `is_complete` is evaluated **live** over the current deliverables +
  descendants, adding work makes a task incomplete again automatically — the
  runner (which loops on the current tree) picks it up mid-run, with no stale
  plan. Adding work to a **finished** task `reopen`s it (`add_subtask` does this).
- The agent resolves "that IPO research" → the relevant active/recent task, then
  calls these helpers; the amendment is recorded in the progress log for history.

## Components (phased)

1. **TaskStore** — CRUD, tree, children, status transitions, assets. *(this batch)*
2. **TaskManager/runner** — background, concurrent (capped), cascade-cancel,
   progress emission; routes permission/HITL to the origin channel's asker
   (redirectable); the existing `PermissionManager` is unchanged (#11/#12).
3. **Intake + planning** — classify trivial/non-trivial; if non-trivial, ask
   clarifying questions via HITL, then form the plan/subtree.
4. **Subtask tree** — decompose into child tasks, run in parallel, cascade all.
5. **Scheduler** — one-shot + recurring triggers.
6. **Gateway API** — task CRUD/tree/progress over REST + WS.
7. **Web GUI Tasks view** — tree + status + progress + assets; chat→task deep link.
8. **Channels** — trigger from any channel; progress/prompts routed back.
9. **Recall + observer + assets** — task memory: what you run, when, how you like it.
