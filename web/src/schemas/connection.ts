// Connections: one configured instance of a messaging platform, plus the three
// tables hung off it — which profiles it can reach, who may speak to it, and where
// each of its group conversations lands.
import { z } from 'zod'

// One bot token's presence and last-4 hint; the value itself is never echoed
// (assistant/secrets.py connection_token_status).
export const TokenStatus = z.object({
  set: z.boolean(),
  hint: z.string(),
})
export type TokenStatus = z.infer<typeof TokenStatus>

// gateway/routes/connection.py _connection_entry. `paired_accounts` is a count, not a roster: a live
// Connection with nobody paired answers nobody (ADR 0021), and that is what says so.
export const Connection = z.object({
  id: z.string(),
  platform: z.string(),
  name: z.string(),
  tokens: z.record(z.string(), TokenStatus),
  default_profile: z.string().nullable(),
  active: z.boolean(),
  error: z.string().nullable(),
  paired_accounts: z.number(),
})
export type Connection = z.infer<typeof Connection>

export const ConnectionList = z.object({ connections: z.array(Connection) })
export type ConnectionList = z.infer<typeof ConnectionList>

// One addressable surface of a Connection (assistant/connections.py surfaces()):
// `dm` + `group` where the two switch independently, a single `all` where they do not.
export const ConnectionSurface = z.object({
  kind: z.string(),
  id: z.string(),
})
export type ConnectionSurface = z.infer<typeof ConnectionSurface>

// gateway/routes/connection.py _exposure_view. `exposure` is {pid: {surface_id: reachable}} and is
// default-allow, so a profile nobody withdrew reads true everywhere.
export const ConnectionExposure = z.object({
  surfaces: z.array(ConnectionSurface),
  exposure: z.record(z.string(), z.record(z.string(), z.boolean())),
  default_profile: z.string().nullable(),
})
export type ConnectionExposure = z.infer<typeof ConnectionExposure>

// gateway/routes/connection.py _account_view. `pending` marks an invitation to a handle — not yet an
// identity, so it has nobody behind it until someone presents it.
export const PairedAccount = z.object({
  key: z.string(),
  account_id: z.string().nullable(),
  handle: z.string().nullable(),
  pending: z.boolean(),
})
export type PairedAccount = z.infer<typeof PairedAccount>

// expires_at is a POSIX timestamp, written as time.time().
export const PairingCode = z.object({
  code: z.string(),
  expires_at: z.number(),
})
export type PairingCode = z.infer<typeof PairingCode>

export const ConnectionPairing = z.object({
  accounts: z.array(PairedAccount),
  code: PairingCode.nullable(),
})
export type ConnectionPairing = z.infer<typeof ConnectionPairing>

// POST …/pairing/code answers with the freshly minted code alone, not the roster.
export const PairingCodeIssued = PairingCode.nullable()
export type PairingCodeIssued = z.infer<typeof PairingCodeIssued>

// One group Peer and the profile it is pinned to (null while it has none).
export const ConnectionGroup = z.object({
  chat_id: z.string(),
  profile: z.string().nullable(),
})
export type ConnectionGroup = z.infer<typeof ConnectionGroup>

// gateway/routes/connection.py _connection_group_view — `profiles` are those exposed to THIS Connection's
// group surface, which is what a group may be re-pointed at (ADR 0022).
export const ConnectionGroups = z.object({
  groups: z.array(ConnectionGroup),
  profiles: z.array(z.object({ id: z.string(), name: z.string() })),
})
export type ConnectionGroups = z.infer<typeof ConnectionGroups>
