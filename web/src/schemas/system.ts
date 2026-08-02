// Install-wide status surfaces: health, usage, channels, and the OAuth cards.
import { z } from 'zod'

// gateway/core.py status(); the zero-profile stub answers {status, profiles}.
export const Health = z.object({
  status: z.string(),
  model: z.string().optional(),
  memory: z.boolean().optional(),
  platform: z.string().optional(),
  chats: z.number().optional(),
  profiles: z.number().optional(),
})
export type Health = z.infer<typeof Health>

// One model's slice of a day's spend (usage.py _blank minus by_model).
export const UsageTotals = z.object({
  prompt: z.number(),
  completion: z.number(),
  total: z.number(),
  cost: z.number(),
  priced: z.boolean(),
})
export type UsageTotals = z.infer<typeof UsageTotals>

// usage.py today() + the date the snapshot covers.
export const Usage = UsageTotals.extend({
  date: z.string(),
  by_model: z.record(z.string(), UsageTotals),
})
export type Usage = z.infer<typeof Usage>

// The install-wide roll-up: one row per running profile, plus a bare sum that
// carries neither a date nor a by_model breakdown.
export const UsageRollup = z.object({
  profiles: z.array(Usage.extend({ pid: z.string(), name: z.string() })),
  total: UsageTotals,
})
export type UsageRollup = z.infer<typeof UsageRollup>

// GET /api/status — a bare array, not an envelope.
export const StatusRow = z.object({
  pid: z.string(),
  busy: z.boolean(),
  running_tasks: z.number(),
  unseen_done: z.number(),
})
export type StatusRow = z.infer<typeof StatusRow>

export const StatusList = z.array(StatusRow)
export type StatusList = z.infer<typeof StatusList>

export const ChannelEntry = z.object({
  profile: z.string().nullable(),
  token_present: z.boolean(),
  active: z.boolean(),
  error: z.string().nullable(),
})
export type ChannelEntry = z.infer<typeof ChannelEntry>

// Keyed by platform, both on the list route and on the single-entry responses.
export const Channels = z.record(z.string(), ChannelEntry)
export type Channels = z.infer<typeof Channels>

export const GoogleStatus = z.object({
  configured: z.boolean(),
  signed_in: z.boolean(),
  email: z.string().nullable(),
  libs_available: z.boolean(),
  install_hint: z.string().nullable(),
})
export type GoogleStatus = z.infer<typeof GoogleStatus>

// codex_auth.status(): expires_at is a POSIX timestamp written as time.time().
export const CodexStatus = z.object({
  signed_in: z.boolean(),
  source: z.string().nullable(),
  account_id: z.string().nullable(),
  expires_at: z.number().nullable(),
})
export type CodexStatus = z.infer<typeof CodexStatus>

export const CodexLoginUrl = z.object({
  ok: z.literal(true),
  auth_url: z.string(),
  state: z.string(),
})
export type CodexLoginUrl = z.infer<typeof CodexLoginUrl>

// Google answers 200 either way, so the failure branch rides the same body.
export const GoogleLoginUrl = z.union([
  z.object({ ok: z.literal(true), auth_url: z.string() }),
  z.object({ ok: z.literal(false), error: z.string() }),
])
export type GoogleLoginUrl = z.infer<typeof GoogleLoginUrl>

export const CodingAgent = z.object({
  name: z.string(),
  label: z.string(),
  available: z.boolean(),
})
export type CodingAgent = z.infer<typeof CodingAgent>

export const CodingAgents = z.object({
  mode: z.enum(['local', 'bridge']),
  bridge: z.string().nullable(),
  connected: z.boolean(),
  // Present only when a bridge connection failed.
  error: z.string().optional(),
  agents: z.array(CodingAgent),
})
export type CodingAgents = z.infer<typeof CodingAgents>

// coding/model_catalog.py as_view — reason says WHY an empty catalog is empty.
export const CatalogModel = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
})
export type CatalogModel = z.infer<typeof CatalogModel>

export const CodingCatalog = z.object({
  models: z.array(CatalogModel),
  current: z.string(),
  reason: z.enum(['', 'adapter_missing', 'bridge', 'probe_failed']),
})
export type CodingCatalog = z.infer<typeof CodingCatalog>

// GET /api/fs/list — the host folder picker; an unreadable dir answers ok:false.
export const FsListing = z.union([
  z.object({
    ok: z.literal(true),
    path: z.string(),
    parent: z.string().nullable(),
    dirs: z.array(z.object({ name: z.string(), path: z.string() })),
  }),
  z.object({ ok: z.literal(false), error: z.string() }),
])
export type FsListing = z.infer<typeof FsListing>

// POST /api/fs/mkdir — failures are non-2xx and thrown by the transport.
export const FsMkdirResult = z.object({ ok: z.literal(true), path: z.string() })
export type FsMkdirResult = z.infer<typeof FsMkdirResult>

// POST /api/identity — seed-only; `reason` says why a seed was skipped.
export const IdentitySeeded = z.object({
  ok: z.boolean(),
  seeded: z.boolean(),
  reason: z.string().optional(),
})
export type IdentitySeeded = z.infer<typeof IdentitySeeded>

// POST /api/google/credentials — a bad client JSON answers 200 with ok:false.
export const OkOrError = z.union([
  z.object({ ok: z.literal(true) }),
  z.object({ ok: z.literal(false), error: z.string() }),
])
export type OkOrError = z.infer<typeof OkOrError>
