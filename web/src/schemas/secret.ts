// Secrets — named reusable API keys. The raw value never leaves the server;
// views carry a last-4 `hint` only (assistant/secrets.py _secret_view).
import { z } from 'zod'

export const Secret = z.object({
  id: z.string(),
  name: z.string(),
  provider: z.string(),
  default: z.boolean(),
  hint: z.string(),
  // Present only on GET /api/secrets — names of the configs referencing this Secret.
  used_by: z.array(z.string()).default([]),
})
export type Secret = z.infer<typeof Secret>

export const SecretList = z.object({ secrets: z.array(Secret) })
export type SecretList = z.infer<typeof SecretList>

export const SecretSaved = z.object({ ok: z.literal(true), secret: Secret })
export type SecretSaved = z.infer<typeof SecretSaved>

// 409 body when the value is already stored — the form snaps to `existing`.
export const SecretConflict = z.object({
  ok: z.literal(false),
  error: z.string(),
  existing: Secret,
})
export type SecretConflict = z.infer<typeof SecretConflict>
