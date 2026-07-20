// Thin REST client for the durable bits AG2 streams don't carry: the task
// tree/schedule/deliverables panel, and the lists for the drawer.
//
// Profile-scoped routes go through P() (→ /api/p/{pid}/…); genuinely global
// routes (profiles registry, secrets, onboarded, google, fs browser) go
// through G() (→ /api/…). See lib/profile.js.

import { api as P, globalApi as G, pidApi as PID, onProfileGone } from '../lib/profile.js'
import { parseEtag } from '../lib/fileEdit.js'

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
    let payload = null
    try { payload = await r.json(); if (payload && payload.error) msg = payload.error } catch {}
    // status/body ride on the Error so callers can act on structured failures
    // (e.g. createSecret's 409 carries the existing Secret to snap to).
    const err = new Error(msg)
    err.status = r.status
    err.body = payload
    throw err
  }
  return r.json()
}

export const api = {
  // ---- Global (unprefixed) ----
  profiles: () => j('GET', G('/profiles')),
  createProfile: (name, accent) => j('POST', G('/profiles'), { name, accent }),
  // Metadata update (§4.2): {name?, accent?} — both registry-only, display changes.
  updateProfile: (pid, body) => j('POST', G('/profiles/' + encodeURIComponent(pid)), body),
  // Archive (§4.9). newDefault is required when archiving the active_default —
  // passed in the request body (DELETE with body → ProfileArchiveRequest).
  archiveProfile: (pid, newDefault) =>
    j('DELETE', G('/profiles/' + encodeURIComponent(pid)), newDefault ? { new_default: newDefault } : {}),
  // Restore (unarchive + boot live) an archived profile → {profile}. 409 if it isn't
  // archived, 404 if unknown (ADR 0003).
  restoreProfile: (pid) => j('POST', G('/profiles/' + encodeURIComponent(pid) + '/restore')),
  // Permanently delete an ARCHIVED profile (erases its folder). ?purge=true escalates
  // the DELETE from archive to hard-delete; 409 if the profile isn't archived yet.
  deleteProfile: (pid) =>
    j('DELETE', G('/profiles/' + encodeURIComponent(pid) + '?purge=true')),
  status: () => j('GET', G('/status')),
  // Install-wide token/cost roll-up across all profiles: {profiles:[{pid,name,...}],
  // total}. The HUD derives the active profile's numbers from `profiles` and appends
  // the `total` only when more than one profile exists (one request, not two).
  usageAll: () => j('GET', G('/usage')),
  setKey: (provider, value) => j('POST', G('/secrets/key'), { provider, value }),
  // ---- Secrets: named reusable API keys (CONTEXT.md "Secrets"). value is
  // WRITE-ONLY (views carry a last-4 hint). createSecret 409s with
  // err.body.existing when the value is already stored (unique by value) —
  // callers snap to it (lib/secrets.js createOrSnap). ----
  secrets: () => j('GET', G('/secrets')),
  createSecret: (s) => j('POST', G('/secrets'), s),
  updateSecret: (id, patch) => j('POST', G('/secrets/' + encodeURIComponent(id)), patch),
  deleteSecret: (id) => j('DELETE', G('/secrets/' + encodeURIComponent(id))),
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
  // Named LIVE (voice) configurations — the spoken counterpart of the LLM configs,
  // same install-wide list + active shape. liveConfigs() → {configs:[entry +
  // {key:{set,hint}, key_source, shared_key, active}], active:id|null, providers:
  // [{name, default_model, default_voice}]}. api_key is WRITE-ONLY (null=unchanged,
  // ""=clear, string=set). Test is a provider models-list ping → {ok, reply,
  // latency_ms} (502 {ok:false,error} throws via j()).
  liveConfigs: () => j('GET', G('/live-configs')),
  saveLiveConfig: (cfg) => {
    const { id, ...body } = cfg
    return j('POST', G('/live-configs' + (id ? '/' + encodeURIComponent(id) : '')), body)
  },
  deleteLiveConfig: (id) => j('DELETE', G('/live-configs/' + encodeURIComponent(id))),
  useLiveConfig: (id) => j('POST', G('/live-configs/' + encodeURIComponent(id) + '/use')),
  testLiveConfig: (id) => j('POST', G('/live-configs/' + encodeURIComponent(id) + '/test')),
  testLiveConfigDraft: (cfg) => j('POST', G('/live-configs/test'), cfg),
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
  // Persistent, install-wide COMMAND permission grants. GLOBAL routes; every
  // mutation returns the full snapshot {ok, commands} (McpServers contract).
  permissions:   () => j('GET', G('/permissions')),
  grantCommand:  (tool, prefix) => j('POST', G('/permissions/commands'), { tool, prefix }),
  revokeCommand: (rule) => j('DELETE', G('/permissions/commands'), { rule }),
  // ---- Folders + Grants (install-wide registry; CONTEXT.md "Folders", ADR 0006).
  // Snapshot shape: {folders:[{id,name,path,exists,grants:[{profile,chat_id,task_id,mode}]}]}.
  // createFolder 409s with err.body.existing when the path is already registered.
  // mode: 'read' | 'read_write'. Empty chatId = profile-scope grant.
  folders: () => j('GET', G('/folders')),
  createFolder: (path, name = '') => j('POST', G('/folders'), { path, name }),
  updateFolder: (id, patch) => j('POST', G('/folders/' + encodeURIComponent(id)), patch),
  deleteFolder: (id) => j('DELETE', G('/folders/' + encodeURIComponent(id))),
  setGrant: (id, profile, mode, chatId = '', taskId = '') =>
    j('POST', G('/folders/' + encodeURIComponent(id) + '/grants'), { profile, chat_id: chatId, task_id: taskId, mode }),
  revokeGrant: (id, profile, chatId = '', taskId = '') =>
    j('DELETE', G('/folders/' + encodeURIComponent(id) + '/grants'), { profile, chat_id: chatId, task_id: taskId }),

  // ---- Profile-scoped (/api/p/{pid}/…) ----
  // Cheap subsystem health for the status dot: {overall, checks:[{id,label,state,detail,…}]}.
  // Distinct from the GLOBAL /api/health (Docker liveness). MCP is listed here but
  // probed on demand via healthMcpServer — this route never spawns a server. A 404/410
  // here means the profile is genuinely gone, so it rides j()'s recovery like the
  // other scoped polls.
  health: () => j('GET', P('/health')),
  chats: () => j('GET', P('/chats')).then((d) => d.chats || []),
  deleteChat: (id) => j('DELETE', P('/chats/' + encodeURIComponent(id))),
  // Partial chat-metadata update: {title?, starred?} (absent = unchanged).
  updateChat: (id, patch) => j('PATCH', P('/chats/' + encodeURIComponent(id)), patch),
  // ---- Tasks: config CRUD; runs are chats on stream task-run:{id} ----
  tasks: () => j('GET', P('/tasks')).then((d) => d.tasks || []),
  createTask: (body) => j('POST', P('/tasks'), body).then((d) => d.task),
  task: (id) => j('GET', P('/tasks/' + encodeURIComponent(id))).then((d) => d.task),
  updateTask: (id, patch) => j('PATCH', P('/tasks/' + encodeURIComponent(id)), patch).then((d) => d.task),
  deleteTask: (id) => j('DELETE', P('/tasks/' + encodeURIComponent(id))),
  runTask: (id) => j('POST', P('/tasks/' + encodeURIComponent(id) + '/run')).then((d) => d.run),
  // Per-task command permission rules (mirrors the global commands store, scoped to
  // one task) → {rules:[...]}. deleteTaskPermission takes a DELETE with a JSON body.
  taskPermissions: (id) => j('GET', P('/tasks/' + encodeURIComponent(id) + '/permissions')).then((d) => d.rules || []),
  deleteTaskPermission: (id, rule) => j('DELETE', P('/tasks/' + encodeURIComponent(id) + '/permissions'), { rule }),
  run: (id) => j('GET', P('/runs/' + encodeURIComponent(id))).then((d) => d.run),
  stopRun: (id) => j('POST', P('/runs/' + encodeURIComponent(id) + '/stop')),
  runSeen: (id) => j('POST', P('/runs/' + encodeURIComponent(id) + '/seen')),
  inquiries: () => j('GET', P('/inquiries/pending')).then((d) => d.pending || []),
  answerInquiry: (id, answer) => j('POST', P(`/inquiries/${encodeURIComponent(id)}/answer`), { answer }),
  // Chat-turn permission prompts (run_code/shell/file) live in the HitlServer, a
  // separate store from durable task inquiries — surfaced in the same strip.
  hitlPending: () => j('GET', P('/hitl/pending')).then((d) => d.pending || []),
  // The desktop HITL answer route stays GLOBAL + unprefixed (ids are globally
  // unique; URLs are baked into notifications) — see plan §4.2.
  answerHitl: (id, answer) => j('POST', `/hitl/${encodeURIComponent(id)}/answer`, { answer }),
  // Voice catalogue + selection. Pass a live-config id to scope to that config's
  // provider/voice (else the profile's legacy voice-provider setting).
  voices: (configId) =>
    j('GET', P('/voice/voices' + (configId ? '?config_id=' + encodeURIComponent(configId) : ''))),
  settings: () => j('GET', P('/settings')),
  // Focus areas are a per-profile persona attribute (settings.json → injected into
  // the agent's context). Active-profile setter (Settings modal).
  setFocuses: (focuses) => j('POST', P('/settings/focuses'), { focuses }),
  setReplyTimeout: (replyTimeoutS) => j('POST', P('/settings/reply-timeout'), { reply_timeout_s: replyTimeoutS }),
  setVoiceProvider: (provider) => j('POST', P('/settings/voice_provider'), { provider }),
  addMcpServer: (server) => j('POST', P('/settings/mcp'), server),
  deleteMcpServer: (name) => j('DELETE', P(`/settings/mcp/${encodeURIComponent(name)}`)),
  healthMcpServer: (name) => j('POST', P(`/settings/mcp/${encodeURIComponent(name)}/health`)),
  getMemory: () => j('GET', P('/memory')),
  setMemory: (text) => j('POST', P('/memory'), { text }),
  // Workspace files — the profile's user-writable Files space (ADR 0007). files()
  // returns {root, files:[{path,name,dir,size,modified}], dirs:[relpath]} (dirs
  // includes empty Directories the files-only list omits — the tree needs them).
  files: () => j('GET', P('/files')),
  // Corpus search for the composer's `@`-picker (ADR 0012): matches across the Files
  // space AND every Folder this profile∪chat can read → {results:[{path (absolute),
  // name, dir, kind}]}, ranked filename-first and bounded. chatId scopes chat-only
  // grants; a blank/no-match query yields an empty list.
  searchFiles: (q, chatId = '') =>
    j('GET', P('/files/search?q=' + encodeURIComponent(q) + (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''))),
  fileUrl: (path, download = false) =>
    P('/files/raw?path=' + encodeURIComponent(path) + (download ? '&download=true' : '')),
  fileText: async (path) => {
    const r = await fetch(P('/files/raw?path=' + encodeURIComponent(path)))
    if (!r.ok) throw new Error('file not found')
    return r.text()
  },
  // Like fileText but also returns the served file's `ETag` → {text, etag}, unquoted
  // to match the bare etag saveFile hands back.
  fileTextWithEtag: async (path) => {
    const r = await fetch(P('/files/raw?path=' + encodeURIComponent(path)))
    if (!r.ok) throw new Error('file not found')
    const text = await r.text()
    return { text, etag: parseEtag(r.headers.get('ETag')) }
  },
  // In-place write (ADR 0011): PUT the UTF-8 body with `If-Match: <etag>`, resolving
  // to the new etag. A failure throws an Error carrying `.status`/`.body` (409/404/400
  // distinct); a 404 here is a missing file, not a vanished profile — so not via j().
  saveFile: async (path, text, etag) => {
    const r = await fetch(P('/files/raw?path=' + encodeURIComponent(path)), {
      method: 'PUT',
      headers: { 'Content-Type': 'text/plain; charset=utf-8', ...(etag ? { 'If-Match': `"${etag}"` } : {}) },
      body: text,
    })
    if (!r.ok) {
      let payload = null
      try { payload = await r.json() } catch {}
      const err = new Error((payload && payload.error) || `save failed (${r.status})`)
      err.status = r.status
      err.body = payload
      throw err
    }
    const data = await r.json().catch(() => ({}))
    return data.etag || parseEtag(r.headers.get('ETag'))
  },
  // Delete a file OR a Directory (recursive) — same route, extended server-side.
  deleteFile: (path) => j('DELETE', P('/files/raw?path=' + encodeURIComponent(path))),
  // Upload OS files into a target Directory (empty = root). Multipart, so it skips
  // j()'s JSON envelope; name clashes are auto-suffixed server-side (never overwrites).
  uploadFiles: async (fileList, dir = '') => {
    const fd = new FormData()
    for (const f of fileList) fd.append('files', f, f.name)
    fd.append('dir', dir)
    const r = await fetch(P('/files/upload'), { method: 'POST', body: fd })
    if (!r.ok) {
      let msg = 'upload failed (' + r.status + ')'
      try { const b = await r.json(); if (b && b.error) msg = b.error } catch {}
      throw new Error(msg)
    }
    return r.json()
  },
  // New empty Directory (409 if it already exists → surfaced as an inline error).
  mkdir: (path) => j('POST', P('/files/mkdir'), { path }),
  // Move/rename a file or Directory. 409 if the destination exists (never overwrites).
  moveFile: (from, to) => j('POST', P('/files/move'), { from, to }),
  usage: () => j('GET', P('/usage')),
  selectVoice: (voice, configId) => j('POST', P('/voice/select'), { voice, config_id: configId || null }),
  previewVoice: async (voice, configId) => {
    const r = await fetch(P('/voice/preview'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice, config_id: configId || null }),
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
    setFocuses: (focuses) => j('POST', PID(pid, '/settings/focuses'), { focuses }),
  }),
}
