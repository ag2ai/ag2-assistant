// Per-profile settings and the cheap health roll-up behind the status dot.
import { z } from 'zod'

// list_mcp_servers hides env values by default and exposes their keys instead.
export const McpServer = z.object({
  name: z.string(),
  enabled: z.boolean(),
  command: z.string(),
  args: z.array(z.string()),
  cwd: z.string().nullable(),
  allowed_tools: z.array(z.string()),
  blocked_tools: z.array(z.string()),
  env_keys: z.array(z.string()).default([]),
})
export type McpServer = z.infer<typeof McpServer>

// POST /settings/mcp/{name}/health — an unreachable server answers ok:false.
export const McpHealth = z.union([
  z.object({ ok: z.literal(true), tools: z.array(z.string()) }),
  z.object({ ok: z.literal(false), error: z.string() }),
])
export type McpHealth = z.infer<typeof McpHealth>

// secrets.status(): every provider reports {set, hint}, except ollama which
// reports its base URL instead of a key hint.
export const ProviderKey = z.object({
  set: z.boolean(),
  hint: z.string().optional(),
  base_url: z.string().optional(),
})
export type ProviderKey = z.infer<typeof ProviderKey>

export const ProfileSettings = z.object({
  keys: z.record(z.string(), ProviderKey),
  voice_available: z.record(z.string(), z.boolean()),
  assistant: z.object({ provider: z.string(), model: z.string() }),
  llm_override: z.string().nullable(),
  llm_active: z.string().nullable(),
  live_override: z.string().nullable(),
  live_active: z.string().nullable(),
  codex: z.object({
    signed_in: z.boolean(),
    source: z.string().nullable(),
    account_id: z.string().nullable(),
    expires_at: z.number().nullable(),
  }),
  voice_provider: z.string(),
  mcp_servers: z.array(McpServer),
  focuses: z.array(z.string()),
  reply_timeout_s: z.number(),
  fs: z.object({ home: z.string(), cwd: z.string(), workspace: z.string() }),
})
export type ProfileSettings = z.infer<typeof ProfileSettings>

// `overall` only ever rolls up to ok/warn/down; a single row can also be off.
export const HealthState = z.enum(['ok', 'warn', 'down', 'off'])
export type HealthState = z.infer<typeof HealthState>

// One row per subsystem; the mcp and channels rows carry extra detail.
export const HealthCheck = z.object({
  id: z.string(),
  label: z.string(),
  state: HealthState,
  detail: z.string(),
  servers: z.array(z.object({ name: z.string(), enabled: z.boolean() })).optional(),
  items: z
    .array(
      z.object({
        platform: z.string(),
        active: z.boolean(),
        error: z.string().nullable(),
        token_present: z.boolean(),
      }),
    )
    .optional(),
})
export type HealthCheck = z.infer<typeof HealthCheck>

export const ProfileHealth = z.object({
  overall: HealthState,
  checks: z.array(HealthCheck),
})
export type ProfileHealth = z.infer<typeof ProfileHealth>

export const VoiceCatalog = z.object({
  voices: z.array(z.object({ name: z.string(), style: z.string() })),
  current: z.string().nullable(),
  provider: z.string(),
  input_rate: z.number(),
})
export type VoiceCatalog = z.infer<typeof VoiceCatalog>

export const MemoryDoc = z.object({ text: z.string() })
export type MemoryDoc = z.infer<typeof MemoryDoc>
