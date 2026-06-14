# AGClaw Task Management — Design

A persistent, user-facing task system: small ad-hoc jobs ("any unread emails?")
and big multi-step jobs ("research the Anthropic & OpenAI IPOs and prepare a
presentation") are first-class, tracked **task** primitives.

## Decisions (from design Q&A)

- **Scheduling:** tasks can run now, at a future time (one-shot), or on a
  recurrence (e.g. daily) — a scheduler is in scope.
- **Concurrency:** several tasks run in the background concurrently, up to a cap.
- **Intake (HITL):** for any *non-trivial* task, AGClaw first asks a short set of
  clarifying questions via HITL on the triggering channel, then plans and runs.
  Trivial tasks skip intake. The agent classifies trivial vs non-trivial.
- **Subtasks:** durable, nestable task tree from Phase 1; cancelling a task
  cancels all descendants immediately.

## What we reuse from AG2 vs build

Reuse (native): the `Task` lifecycle concepts (`TaskState`, `TaskProgress`,
`checkpoint`/`resume_from`, `TaskSpec`), `run_subtasks(parallel=True)` for
execution, and `EventLogWriter` for per-task history (recall/resume).

Build (ours): the durable task **store + tree**, the background **runner** with
**immediate cascading cancel** (AG2's `cancel()` only flips a flag), the HITL
**intake**, the **scheduler**, the gateway **task API**, the **GUI Tasks view**,
**channel** triggering/routing, and the task **observer/memory**.

> #16 (custom AG2 network *channel adapter* for task delivery): researched —
> not a fit. The network is agent↔agent orchestration; our tasks are user↔agent.
> We build a dedicated task layer instead (Hub kept in reserve for cross-machine).

## Model

A `Task` record (persisted as JSON in `~/.agclaw/tasks.db`):

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
