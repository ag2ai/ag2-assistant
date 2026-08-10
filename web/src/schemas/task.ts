// Tasks, their runs, and the durable HITL inquiries a run can block on.
import { z } from 'zod'

// assistant/tasks/model.py RunStatus.
export const RunStatus = z.enum(['running', 'needs_input', 'completed', 'failed', 'cancelled'])
export type RunStatus = z.infer<typeof RunStatus>

// model.py pins this to schedule | once | manual, but run records are persisted
// across versions and no view reads the field, so an older value must not throw.
export const RunTrigger = z.string()
export type RunTrigger = z.infer<typeof RunTrigger>

export const Run = z.object({
  id: z.string(),
  task_id: z.string(),
  status: RunStatus,
  trigger: RunTrigger,
  started_at: z.string().nullable(),
  ended_at: z.string().nullable(),
  summary: z.string().nullable(),
  error: z.string(),
  seen: z.boolean(),
})
export type Run = z.infer<typeof Run>

// assistant/tasks/model.py ScheduleKind, normalised to {kind, at, cron}.
// Never null: model.py:87 defaults it and normalize_schedule(None) yields manual.
//
// All three fields are declared because ScheduleField.svelte reads `at` and
// `cron`, and the gateway's model is the contract — a field it does not declare
// never reaches the client. Both are `.nullish()`: store.py create_task accepts a
// schedule dict unnormalised (`schedule or manual_schedule()`), so a persisted row
// can be missing a key normalize_schedule would have filled. Still a looseObject,
// so a shape the backend later grows is rendered rather than rejected.
export const Schedule = z.looseObject({
  kind: z.enum(['manual', 'once', 'cron']),
  at: z.string().nullish(),
  cron: z.string().nullish(),
})
export type Schedule = z.infer<typeof Schedule>

export const Task = z.object({
  id: z.string(),
  name: z.string(),
  prompt: z.string(),
  model: z.string().nullable(),
  description: z.string(),
  schedule: Schedule,
  schedule_desc: z.string(),
  paused: z.boolean(),
  starred: z.boolean(),
  next_run_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  last_run: Run.nullable(),
  unread: z.number(),
  needs_input: z.boolean(),
})
export type Task = z.infer<typeof Task>

// GET /tasks/{id} is the same row plus its run history.
export const TaskWithRuns = Task.extend({ runs: z.array(Run) })
export type TaskWithRuns = z.infer<typeof TaskWithRuns>

export const TaskList = z.object({ tasks: z.array(Task) })
export type TaskList = z.infer<typeof TaskList>

export const TaskEnvelope = z.object({ task: TaskWithRuns })
export type TaskEnvelope = z.infer<typeof TaskEnvelope>

// Both run routes answer through tasks_service.get_run, which stamps the owning
// task's name onto the run view (tasks_service.py:354) — the run header reads it.
export const RunDetail = Run.extend({ task_name: z.string() })
export type RunDetail = z.infer<typeof RunDetail>

export const RunDetailEnvelope = z.object({ run: RunDetail })
export type RunDetailEnvelope = z.infer<typeof RunDetailEnvelope>

export const RunList = z.object({ runs: z.array(Run) })
export type RunList = z.infer<typeof RunList>

export const Inquiry = z.object({
  id: z.string(),
  task_id: z.string(),
  chat: z.string(),
  kind: z.string(),
  text: z.string(),
  detail: z.string(),
  options: z.array(z.string()),
  created_at: z.string(),
  root_id: z.string().nullable(),
  task_title: z.string(),
  run_id: z.string().nullable(),
})
export type Inquiry = z.infer<typeof Inquiry>

export const InquiryList = z.object({ pending: z.array(Inquiry) })
export type InquiryList = z.infer<typeof InquiryList>

// hitl/desktop.py pending_list — this profile's own registry. `detail` and
// `options` are nullable because hitl/base.py Question defaults both to None (no
// secondary context; no options means a free-text answer). They were declared
// plain strings here, which made every prompt without a detail fail safeParse —
// and the strip catches that and renders an empty list, so transient permission
// prompts never appeared. The OpenAPI gate now holds both sides to this.
export const HitlQuestion = z.object({
  id: z.string(),
  text: z.string(),
  detail: z.string().nullable(),
  options: z.array(z.string()).nullable(),
  kind: z.string(),
  path: z.string(),
})
export type HitlQuestion = z.infer<typeof HitlQuestion>

export const HitlPending = z.object({ pending: z.array(HitlQuestion) })
export type HitlPending = z.infer<typeof HitlPending>

// POST /tasks answers with a freshly built row — tasks_service.create_task
// returns _task_row() without the `runs` key GET /tasks/{id} adds.
export const NewTaskEnvelope = z.object({ task: Task })
export type NewTaskEnvelope = z.infer<typeof NewTaskEnvelope>
