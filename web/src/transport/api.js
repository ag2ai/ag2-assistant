// Thin REST client for the durable bits AG2 streams don't carry: the task
// tree/schedule/deliverables panel, and the lists for the drawer.
//
// Profile-scoped routes go through P() (→ /api/p/{pid}/…); genuinely global
// routes (profiles registry, secrets, onboarded, google, fs browser) go
// through G() (→ /api/…). See lib/profile.js.

import { api as P, globalApi as G, pidApi as PID, onProfileGone } from '../lib/profile.js'

async function j(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  // A scoped route returning 410 means the active profile was archived under
  // us — recover by re-resolving (§7 item 6). 404 = unknown pid, same fix.
  if (r.status === 410 || (r.status === 404 && path.startsWith('/api/p/'))) {
    onProfileGone('fetch ' + r.status)
    throw new Error(`${method} ${path} -> ${r.status}`)
  }
  if (!r.ok) {
    let msg = `${method} ${path} -> ${r.status}`
    try { const e = await r.json(); if (e && e.error) msg = e.error } catch {}
    throw new Error(msg)
  }
  return r.json()
}

export const api = {
  // ---- Global (unprefixed) ----
  profiles: () => j('GET', G('/profiles')),
  createProfile: (name, palette, workspace) =>
    j('POST', G('/profiles'), workspace ? { name, palette, workspace } : { name, palette }),
  // Metadata update (§4.2): {name?, palette?, workspace?}. workspace triggers a
  // server-side runtime reload; name/palette are registry-only.
  updateProfile: (pid, body) => j('POST', G('/profiles/' + encodeURIComponent(pid)), body),
  // Archive (§4.9). newDefault is required when archiving the active_default —
  // passed in the request body (DELETE with body → ProfileArchiveRequest).
  archiveProfile: (pid, newDefault) =>
    j('DELETE', G('/profiles/' + encodeURIComponent(pid)), newDefault ? { new_default: newDefault } : {}),
  status: () => j('GET', G('/status')),
  // Read-only status of the CLI coding agents (Settings → Tools). {mode:'local'|
  // 'bridge', bridge, connected, agents:[{name,label,available}], error?}. In
  // Docker with AG2ASSISTANT_ACP_BRIDGE set this reflects the host bridge.
  codingAgents: () => j('GET', G('/coding/agents')),
  // Install-wide token/cost roll-up across all profiles: {profiles:[{pid,name,...}],
  // total}. The HUD derives the active profile's numbers from `profiles` and appends
  // the `total` only when more than one profile exists (one request, not two).
  usageAll: () => j('GET', G('/usage')),
  setKey: (provider, value) => j('POST', G('/secrets/key'), { provider, value }),
  // Named LLM configurations — install-wide list + active selection (LLM is common
  // across profiles). All GLOBAL. llmConfigs() → {configs:[entry + {key:{set,hint},
  // active}], active:id|null, env_override:{provider?,model?}|null}. saveLlmConfig
  // posts to /llm-configs (create) or /llm-configs/{id} (update, when cfg.id set);
  // the id is stripped from the body. api_key is WRITE-ONLY: null=unchanged,
  // ""=clear, string=set — the payload never echoes a raw key. Values validated by
  // a server-side dry-construct → 400 {error} surfaced by j() as Error(msg).
  llmConfigs: () => j('GET', G('/llm-configs')),
  saveLlmConfig: (cfg) => {
    const { id, ...body } = cfg
    return j('POST', G('/llm-configs' + (id ? '/' + encodeURIComponent(id) : '')), body)
  },
  deleteLlmConfig: (id) => j('DELETE', G('/llm-configs/' + encodeURIComponent(id))),
  useLlmConfig: (id) => j('POST', G('/llm-configs/' + encodeURIComponent(id) + '/use')),
  // Real PONG round-trip against the config's resolved runtime key → {ok, reply,
  // latency_ms}; a 502 {ok:false,error} throws via j() (surface the message inline).
  testLlmConfig: (id) => j('POST', G('/llm-configs/' + encodeURIComponent(id) + '/test')),
  // Test an UNSAVED editor draft (nothing persisted; a blank api_key falls back to
  // the stored key when cfg.id is set).
  testLlmConfigDraft: (cfg) => j('POST', G('/llm-configs/test'), cfg),
  setOnboarded: (value = true) => j('POST', G('/onboarded'), { value }),
  listDirs: (path = '') => j('GET', G('/fs/list?path=' + encodeURIComponent(path))),
  googleStatus: () => j('GET', G('/google/status')),
  googleLoginUrl: () => j('POST', G('/google/login_url')),
  googleCredentials: (content) => j('POST', G('/google/credentials'), { content }),
  googleLogout: () => j('POST', G('/google/logout')),
  // OpenAI ChatGPT/Codex subscription ("Sign in with ChatGPT"). Unofficial — the
  // gateway runs a loopback OAuth on localhost:1455; headless users paste the code
  // to /codex/submit with the flow `state` returned by codexLoginUrl(). GLOBAL
  // routes (account-level, shared across profiles — like Google).
  codexStatus: () => j('GET', G('/codex/status')),
  codexLoginUrl: () => j('POST', G('/codex/login_url')),
  codexSubmit: (state, code) => j('POST', G('/codex/submit'), { state, code }),
  codexLogout: () => j('POST', G('/codex/logout')),
  // Messaging channels are install-level: a platform binds to exactly one profile
  // (or is disabled). Both routes are GLOBAL. channels() → {telegram|discord|slack:
  // {profile:pid|null, token_present, active, error}}. channelBind returns the one
  // updated entry {platform: {…}}. The binding persists even if start fails
  // (active:false + error).
  channels: () => j('GET', G('/channels')),
  channelBind: (platform, profile) => j('POST', G('/channels'), { platform, profile }),
  // Save/clear channel bot token(s), like setKey — tokens is {ENV_NAME: value|''}
  // (empty clears). Returns the one updated entry {platform: {…}}. Values never echoed.
  channelTokens: (platform, tokens) => j('POST', G('/channels/token'), { platform, tokens }),
  // Universal "who the user is" memory — a single install-wide doc shared by every
  // profile (identity facts). GLOBAL routes; the per-profile persona memory is
  // getMemory/setMemory below.
  globalMemory: () => j('GET', G('/memory')),
  setGlobalMemory: (text) => j('POST', G('/memory'), { text }),
  // Seed the universal doc from web-onboarding identity answers (all optional).
  // Seed-only: the server refuses to clobber an existing doc → {ok, seeded}.
  setIdentity: (fields) => j('POST', G('/identity'), fields),
  // Persistent, install-wide permission grants (folders + shell/tool command rules).
  // GLOBAL routes; every mutation returns the full snapshot {ok, folders, blocked,
  // commands} so the client never needs a follow-up GET (McpServers contract).
  // Bodies (not URL segments): paths contain '/', command rules contain '( * )'.
  // DELETE-with-body is supported by j() (precedent: archiveProfile).
  permissions:   () => j('GET', G('/permissions')),
  grantFolder:   (path) => j('POST', G('/permissions/folders'), { path }),
  revokeFolder:  (path) => j('DELETE', G('/permissions/folders'), { path }),
  blockFolder:   (path) => j('POST', G('/permissions/blocked'), { path }),
  unblockFolder: (path) => j('DELETE', G('/permissions/blocked'), { path }),
  grantCommand:  (tool, prefix) => j('POST', G('/permissions/commands'), { tool, prefix }),
  revokeCommand: (rule) => j('DELETE', G('/permissions/commands'), { rule }),

  // ---- Profile-scoped (/api/p/{pid}/…) ----
  // Cheap subsystem health for the status dot: {overall, checks:[{id,label,state,detail,…}]}.
  // Distinct from the GLOBAL /api/health (Docker liveness). MCP is listed here but
  // probed on demand via healthMcpServer — this route never spawns a server. A 404/410
  // here means the profile is genuinely gone, so it rides j()'s recovery like the
  // other scoped polls.
  health: () => j('GET', P('/health')),
  sessions: () => j('GET', P('/sessions')).then((d) => d.sessions || []),
  deleteSession: (id) => j('DELETE', P('/sessions/' + encodeURIComponent(id))),
  tasksAll: (status) => j('GET', P('/tasks/all' + (status && status !== 'all' ? '?status=' + status : ''))).then((d) => d.tasks || []),
  task: (id) => j('GET', P('/tasks/' + id)).then((d) => d.task),
  cancelTask: (id) => j('POST', P(`/tasks/${id}/cancel`)),
  markSeen: (id) => j('POST', P(`/tasks/${id}/seen`)),
  deleteTask: (id) => j('DELETE', P(`/tasks/${encodeURIComponent(id)}`)),
  inquiries: () => j('GET', P('/inquiries/pending')).then((d) => d.pending || []),
  answerInquiry: (id, answer) => j('POST', P(`/inquiries/${encodeURIComponent(id)}/answer`), { answer }),
  // Chat-turn permission prompts (run_code/shell/file) live in the HitlServer, a
  // separate store from durable task inquiries — surfaced in the same strip.
  hitlPending: () => j('GET', P('/hitl/pending')).then((d) => d.pending || []),
  // The desktop HITL answer route stays GLOBAL + unprefixed (ids are globally
  // unique; URLs are baked into notifications) — see plan §4.2.
  answerHitl: (id, answer) => j('POST', `/hitl/${encodeURIComponent(id)}/answer`, { answer }),
  voices: () => j('GET', P('/voice/voices')),
  settings: () => j('GET', P('/settings')),
  setProjectFolder: (path) => j('POST', P('/settings/project-folder'), { path }),
  // Focus areas are a per-profile persona attribute (settings.json → injected into
  // the agent's context). Active-profile setter (Settings modal).
  setFocuses: (focuses) => j('POST', P('/settings/focuses'), { focuses }),
  rerunTask: (id) => j('POST', P(`/tasks/${encodeURIComponent(id)}/rerun`)),
  setVoiceProvider: (provider) => j('POST', P('/settings/voice_provider'), { provider }),
  addMcpServer: (server) => j('POST', P('/settings/mcp'), server),
  deleteMcpServer: (name) => j('DELETE', P(`/settings/mcp/${encodeURIComponent(name)}`)),
  healthMcpServer: (name) => j('POST', P(`/settings/mcp/${encodeURIComponent(name)}/health`)),
  getMemory: () => j('GET', P('/memory')),
  setMemory: (text) => j('POST', P('/memory'), { text }),
  // Workspace files (the agent's working file space)
  files: () => j('GET', P('/files')),
  fileUrl: (path, download = false) =>
    P('/files/raw?path=' + encodeURIComponent(path) + (download ? '&download=true' : '')),
  fileText: async (path) => {
    const r = await fetch(P('/files/raw?path=' + encodeURIComponent(path)))
    if (!r.ok) throw new Error('file not found')
    return r.text()
  },
  deleteFile: (path) => j('DELETE', P('/files/raw?path=' + encodeURIComponent(path))),
  usage: () => j('GET', P('/usage')),
  selectVoice: (voice) => j('POST', P('/voice/select'), { voice }),
  previewVoice: async (voice) => {
    const r = await fetch(P('/voice/preview'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice }),
    })
    if (!r.ok) throw new Error('preview failed (' + r.status + ')')
    return r.blob()
  },

  // ---- Explicit-pid scoped (targets a SPECIFIC profile, not the active one) ----
  // For flows that configure a profile other than the one currently active — the
  // onboarding per-profile setup page iterates several freshly-created profiles.
  // Returns the subset of scoped helpers those pages need.
  forProfile: (pid) => ({
    settings: () => j('GET', PID(pid, '/settings')),
    setProjectFolder: (path) => j('POST', PID(pid, '/settings/project-folder'), { path }),
    setFocuses: (focuses) => j('POST', PID(pid, '/settings/focuses'), { focuses }),
  }),
}
