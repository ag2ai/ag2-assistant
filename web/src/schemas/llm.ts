// Named LLM + live (voice) configurations: the install-wide lists, the active id,
// and the probe result both Test buttons return.
import { z } from 'zod'
import { SecretRef, SharedKey } from './primitives.ts'
import { CatalogModel } from './system.ts'

// Optional provider-library state for a config type (llm_configs.deps_status).
export const DepsStatus = z.object({
  ok: z.boolean(),
  extra: z.string(),
  install: z.string(),
})
export type DepsStatus = z.infer<typeof DepsStatus>

// Which key an actual call would send (llm_configs.key_source docstring).
export const KeySource = z.enum(['secret', 'shared', 'not_needed', 'none', 'subscription', 'cli_login'])
export type KeySource = z.infer<typeof KeySource>

export const LlmConfig = z.object({
  id: z.string(),
  name: z.string(),
  type: z.string(),
  model: z.string(),
  base_url: z.string(),
  host: z.string(),
  options: z.record(z.string(), z.unknown()),
  secret_id: z.string(),
  secret: SecretRef.nullable(),
  secret_missing: z.boolean(),
  key_source: KeySource,
  images: z.boolean(),
  deps: DepsStatus,
  shared_key: SharedKey,
  active: z.boolean(),
  // Added only for type === 'openai_subscription' (live ChatGPT sign-in state).
  signed_in: z.boolean().optional(),
})
export type LlmConfig = z.infer<typeof LlmConfig>

// Whichever of AG2ASSISTANT_LLM_PROVIDER / AG2ASSISTANT_MODEL is set, else null.
export const LlmEnvOverride = z.object({
  provider: z.string().optional(),
  model: z.string().optional(),
})
export type LlmEnvOverride = z.infer<typeof LlmEnvOverride>

export const LlmConfigList = z.object({
  configs: z.array(LlmConfig),
  active: z.string().nullable(),
  env_override: LlmEnvOverride.nullable(),
  provider_deps: z.record(z.string(), DepsStatus),
})
export type LlmConfigList = z.infer<typeof LlmConfigList>

// GET /api/llm-configs/models — the same {models, current, reason} envelope the ACP
// route uses, with provider_catalog.py's own reasons (lib/modelSuggest.ts REASON)
// instead of the coding-agent ones. `current` is always '' here: a provider names no
// model of its own. CatalogModel is shared with the ACP route, hence the import.
export const ProviderCatalog = z.object({
  models: z.array(CatalogModel),
  current: z.string(),
  reason: z.enum(['', 'unauthorized', 'unreachable', 'no_list_endpoint', 'not_probeable']),
})
export type ProviderCatalog = z.infer<typeof ProviderCatalog>

export const LlmConfigSaved = z.object({
  ok: z.literal(true),
  config: LlmConfig,
  active: z.string().nullable(),
})
export type LlmConfigSaved = z.infer<typeof LlmConfigSaved>

export const LiveKeySource = z.enum(['secret', 'shared', 'none'])
export type LiveKeySource = z.infer<typeof LiveKeySource>

export const LiveConfig = z.object({
  id: z.string(),
  name: z.string(),
  provider: z.string(),
  model: z.string(),
  voice: z.string(),
  secret_id: z.string(),
  secret: SecretRef.nullable(),
  secret_missing: z.boolean(),
  key_source: LiveKeySource,
  shared_key: SharedKey,
  active: z.boolean(),
})
export type LiveConfig = z.infer<typeof LiveConfig>

export const LiveProvider = z.object({
  name: z.string(),
  default_model: z.string(),
  default_voice: z.string(),
})
export type LiveProvider = z.infer<typeof LiveProvider>

export const LiveConfigList = z.object({
  configs: z.array(LiveConfig),
  active: z.string().nullable(),
  providers: z.array(LiveProvider),
})
export type LiveConfigList = z.infer<typeof LiveConfigList>

export const LiveConfigSaved = z.object({
  ok: z.literal(true),
  config: LiveConfig,
  active: z.string().nullable(),
})
export type LiveConfigSaved = z.infer<typeof LiveConfigSaved>

// Both Test buttons: a 502 failure is thrown by the transport, so only success lands here.
export const PingResult = z.object({
  ok: z.literal(true),
  reply: z.string(),
  latency_ms: z.number(),
})
export type PingResult = z.infer<typeof PingResult>
