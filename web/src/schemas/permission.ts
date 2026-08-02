// Permissions: canonical command rule strings, install-wide or task-scoped.
import { z } from 'zod'

export const PermissionSnapshot = z.object({ commands: z.array(z.string()) })
export type PermissionSnapshot = z.infer<typeof PermissionSnapshot>

// Install-wide grant/revoke echo the refreshed snapshot alongside ok. The
// task-scoped revoke answers a bare {ok} instead, so it uses Ok, not this.
export const PermissionMutated = z.object({
  ok: z.literal(true),
  commands: z.array(z.string()),
})
export type PermissionMutated = z.infer<typeof PermissionMutated>

// GET /api/p/{pid}/tasks/{id}/permissions — this task's own rules only.
export const TaskRules = z.object({ rules: z.array(z.string()) })
export type TaskRules = z.infer<typeof TaskRules>
