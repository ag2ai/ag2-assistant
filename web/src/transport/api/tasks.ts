// Tasks: config CRUD plus the durable inquiries a run can block on (app.py
// 2274-2414). Runs are chats on the stream task-run:{id}.
import { api as P } from '../../lib/profile.js'
import { del, get, patch, post } from '../http.ts'
import {
  HitlPending,
  InquiryList,
  NewTaskEnvelope,
  Ok,
  RunDetailEnvelope,
  TaskEnvelope,
  TaskList,
  TaskRules,
} from '../../schemas/index.ts'

// TaskCreate in app.py — an empty name auto-generates one from the prompt.
export type TaskDraft = {
  name?: string
  prompt: string
  model?: string | null
  schedule?: Record<string, unknown> | null
  description?: string
}

// TaskPatch — absent means unchanged; model '' clears back to the profile default.
export type TaskPatchBody = {
  name?: string
  prompt?: string
  model?: string | null
  schedule?: Record<string, unknown> | null
  paused?: boolean
  starred?: boolean
  description?: string
}

export const tasksApi = {
  tasks: () => get(P('/tasks'), TaskList).then((d) => d.tasks),

  // 422 with {error} on a bad schedule/model.
  createTask: (body: TaskDraft) => post(P('/tasks'), body, NewTaskEnvelope).then((d) => d.task),

  task: (id: string) => get(P('/tasks/' + encodeURIComponent(id)), TaskEnvelope).then((d) => d.task),

  updateTask: (id: string, patchBody: TaskPatchBody) =>
    patch(P('/tasks/' + encodeURIComponent(id)), patchBody, TaskEnvelope).then((d) => d.task),

  deleteTask: (id: string) => del(P('/tasks/' + encodeURIComponent(id)), Ok),

  runTask: (id: string) =>
    post(P('/tasks/' + encodeURIComponent(id) + '/run'), undefined, RunDetailEnvelope).then((d) => d.run),

  // Per-task command permission rules — the global commands store, scoped to one task.
  taskPermissions: (id: string) =>
    get(P('/tasks/' + encodeURIComponent(id) + '/permissions'), TaskRules).then((d) => d.rules),

  deleteTaskPermission: (id: string, rule: string) =>
    del(P('/tasks/' + encodeURIComponent(id) + '/permissions'), Ok, { rule }),

  run: (id: string) => get(P('/runs/' + encodeURIComponent(id)), RunDetailEnvelope).then((d) => d.run),

  stopRun: (id: string) => post(P('/runs/' + encodeURIComponent(id) + '/stop'), undefined, Ok),

  runSeen: (id: string) => post(P('/runs/' + encodeURIComponent(id) + '/seen'), undefined, Ok),

  inquiries: () => get(P('/inquiries/pending'), InquiryList).then((d) => d.pending),

  answerInquiry: (id: string, answer: string) =>
    post(P(`/inquiries/${encodeURIComponent(id)}/answer`), { answer }, Ok),

  // Chat-turn permission prompts (run_code/shell/file) live in the HitlServer, a
  // separate store from durable task inquiries — surfaced in the same strip.
  hitlPending: () => get(P('/hitl/pending'), HitlPending).then((d) => d.pending),

  // The desktop HITL answer route stays GLOBAL + unprefixed (ids are globally
  // unique; URLs are baked into notifications) — hitl/desktop.py.
  answerHitl: (id: string, answer: string) =>
    post(`/hitl/${encodeURIComponent(id)}/answer`, { answer }, Ok),
}
