// Atoms shared by more than one domain schema.
import { z } from 'zod'

export const Ok = z.object({ ok: z.boolean() })
export type Ok = z.infer<typeof Ok>

export const ErrorBody = z.object({ error: z.string() })
export type ErrorBody = z.infer<typeof ErrorBody>

// Effective Folder grant mode (assistant/folders.py READ / READ_WRITE).
export const Mode = z.enum(['read', 'read_write'])
export type Mode = z.infer<typeof Mode>

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
