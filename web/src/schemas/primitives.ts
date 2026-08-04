// Atoms shared by more than one domain schema.
import { z } from 'zod'

export const Ok = z.object({ ok: z.boolean() })
export type Ok = z.infer<typeof Ok>

export const ErrorBody = z.object({ error: z.string() })
export type ErrorBody = z.infer<typeof ErrorBody>

// Effective Folder grant mode (assistant/folders.py READ / READ_WRITE).
export const Mode = z.enum(['read', 'read_write'])
export type Mode = z.infer<typeof Mode>

// A stored grant's mode. `none` is override-only — a chat- or task-scoped grant
// blocking an inherited Folder (assistant/folders.py:37) — so it appears in a
// Folder's `grants` but never as an effective mode.
export const GrantMode = z.enum(['read', 'read_write', 'none'])
export type GrantMode = z.infer<typeof GrantMode>

// The trimmed Secret view embedded in an LLM/live config (app.py _llm_entry_view).
export const SecretRef = z.object({
  id: z.string(),
  name: z.string(),
  hint: z.string(),
})
export type SecretRef = z.infer<typeof SecretRef>

// Provider-wide env key summary carried by both config views.
export const SharedKey = z.object({
  env: z.string(),
  set: z.boolean(),
  hint: z.string(),
})
export type SharedKey = z.infer<typeof SharedKey>
