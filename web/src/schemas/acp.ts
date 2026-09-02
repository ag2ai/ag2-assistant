// ACP listeners: install-level, one listener bound to one Profile at creation
// (ADR 0031) — never exposure-gated, so this file carries no exposure/default
// shapes the way connection.ts does for messaging platforms.
import { z } from 'zod'

// gateway/routes/acp.py _entry. `has_token` never carries the raw value — the
// secret is answered exactly once, from the create and rotate-token responses.
export const AcpListener = z.object({
  id: z.string(),
  name: z.string(),
  profile: z.string(),
  port: z.number().nullable(),
  running: z.boolean(),
  error: z.string().nullable(),
  has_token: z.boolean(),
})
export type AcpListener = z.infer<typeof AcpListener>

export const AcpListenerList = z.object({ listeners: z.array(AcpListener) })
export type AcpListenerList = z.infer<typeof AcpListenerList>

// POST /api/acp/listeners: the token is shown here once — Settings must copy it
// now or lose it forever. Empty for a stdio listener, which has none.
export const AcpListenerCreated = z.object({ listener: AcpListener, token: z.string() })
export type AcpListenerCreated = z.infer<typeof AcpListenerCreated>

// POST /api/acp/listeners/{id}/rotate-token: same one-time-reveal shape as create.
export const AcpListenerTokenRotated = z.object({ listener: AcpListener, token: z.string() })
export type AcpListenerTokenRotated = z.infer<typeof AcpListenerTokenRotated>
