// Named LLM + live (voice) configurations — install-wide lists and the active
// selection (app.py 1103-1409). `api_key` is WRITE-ONLY and DRAFT-TEST ONLY:
// create/update ignore it, the /test routes use a typed value directly.
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import {
  LiveConfigList,
  LiveConfigSaved,
  LlmConfigList,
  LlmConfigSaved,
  Ok,
  PingResult,
  ProviderCatalog,
} from '../../schemas/index.ts'

// LlmConfigRequest in app.py. `id` picks create (absent) vs update (present) and
// is stripped from the body — the route takes it from the URL path.
export type LlmConfigDraft = {
  id?: string | null
  name: string
  type: string
  model: string
  base_url?: string
  host?: string
  secret_id?: string
  api_key?: string | null
  options?: Record<string, unknown>
  activate?: boolean
}

// Which configuration a model catalog is read for — non-secret fields only, the
// key named by reference (`secret_id`) and never by value.
export type CatalogTarget = {
  type: string
  base_url?: string
  host?: string
  secret_id?: string
}

// LiveConfigRequest in app.py.
export type LiveConfigDraft = {
  id?: string | null
  name: string
  provider: string
  model?: string
  voice?: string
  secret_id?: string
  api_key?: string | null
  activate?: boolean
}

export const llmApi = {
  llmConfigs: () => get(G('/llm-configs'), LlmConfigList),

  saveLlmConfig: (cfg: LlmConfigDraft) => {
    const { id, ...body } = cfg
    return post(
      G('/llm-configs' + (id ? '/' + encodeURIComponent(id) : '')),
      body,
      LlmConfigSaved,
    )
  },

  deleteLlmConfig: (id: string) => del(G('/llm-configs/' + encodeURIComponent(id)), Ok),

  useLlmConfig: (id: string) =>
    post(G('/llm-configs/' + encodeURIComponent(id) + '/use'), undefined, Ok),

  // Real PONG round-trip against the config's resolved runtime key; a 502
  // {ok:false,error} throws, so only success reaches the schema.
  testLlmConfig: (id: string) =>
    post(G('/llm-configs/' + encodeURIComponent(id) + '/test'), undefined, PingResult),

  // Test an UNSAVED editor draft (nothing persisted; a blank api_key falls back
  // to the stored key when cfg.id is set).
  testLlmConfigDraft: (cfg: LlmConfigDraft) => post(G('/llm-configs/test'), cfg, PingResult),

  // A provider's model catalog for the Model field's combobox, in the same
  // {models, current, reason} envelope codingModels() returns. The configuration
  // is named by non-secret fields only — this route accepts no key material,
  // because a pasted key goes to the provider that owns it and never to us
  // (ADR 0024); that probe is transport/modelCatalog.ts.
  llmCatalog: (target: CatalogTarget, refresh = false) => {
    const q = new URLSearchParams({ type: target.type })
    if (target.base_url) q.set('base_url', target.base_url)
    if (target.host) q.set('host', target.host)
    if (target.secret_id) q.set('secret_id', target.secret_id)
    if (refresh) q.set('refresh', '1')
    return get(G(`/llm-configs/models?${q}`), ProviderCatalog)
  },

  liveConfigs: () => get(G('/live-configs'), LiveConfigList),

  saveLiveConfig: (cfg: LiveConfigDraft) => {
    const { id, ...body } = cfg
    return post(
      G('/live-configs' + (id ? '/' + encodeURIComponent(id) : '')),
      body,
      LiveConfigSaved,
    )
  },

  deleteLiveConfig: (id: string) => del(G('/live-configs/' + encodeURIComponent(id)), Ok),

  useLiveConfig: (id: string) =>
    post(G('/live-configs/' + encodeURIComponent(id) + '/use'), undefined, Ok),

  testLiveConfig: (id: string) =>
    post(G('/live-configs/' + encodeURIComponent(id) + '/test'), undefined, PingResult),

  testLiveConfigDraft: (cfg: LiveConfigDraft) => post(G('/live-configs/test'), cfg, PingResult),
}
