// Named LLM + live (voice) configurations — install-wide lists and the active
// selection (app.py 1103-1409). `api_key` is WRITE-ONLY and DRAFT-TEST ONLY:
// create/update ignore it, the /test routes use a typed value directly.
import { globalApi as G } from '../../lib/profile.js'
import { del, get, post } from '../http.ts'
import {
  LiveConfigList,
  LiveConfigSaved,
  LlmConfigList,
  LlmConfigSaved,
  Ok,
  PingResult,
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
