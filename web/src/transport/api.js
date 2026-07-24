// Thin REST client for the durable bits AG2 streams don't carry: the task
// tree/schedule/deliverables panel, and the lists for the drawer.
//
// Profile-scoped routes go through P() (→ /api/p/{pid}/…); genuinely global
// routes (profiles registry, secrets, onboarded, google, fs browser) go
// through G() (→ /api/…). See lib/profile.js.

import { api as P, globalApi as G, pidApi as PID, onProfileGone } from '../lib/profile.js'
import { parseEtag } from '../lib/fileEdit.js'
import { rawQuery } from '../lib/folderFiles.js'

// The one response check both helpers share: the profile-gone recovery (410, or 404
// on a scoped route) and the error extraction off a non-2xx body. Kept in one place so
// a fix to the 410 recovery or the error shape lands for JSON and multipart alike.
// Returns the parsed JSON on success; throws an Error carrying .status/.body on failure.
async function checkResponse(r, method, path) {
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

async function j(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  return checkResponse(r, method, path)
}

// Build the multipart body a skill upload route expects: the file, plus (for install)
// a comma-separated `names` field — multipart can't carry a JSON array.
function formFile(file, names) {
  const fd = new FormData()
  fd.append('file', file)
  if (names !== undefined) fd.append('names', Array.isArray(names) ? names.join(',') : String(names))
  return fd
}

// Multipart POST (file uploads) — the JSON helper above stringifies bodies, so skill
// uploads (discover/install from a SKILL.md or zip) go through this instead. Shares j()'s
// error/410 handling via the same checkResponse().
async function jForm(path, formData) {
  const r = await fetch(path, { method: 'POST', body: formData })
  return checkResponse(r, 'POST', path)
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
  // Read-only status of the CLI coding agents (Settings → Tools). {mode:'local'|
  // 'bridge', bridge, connected, agents:[{name,label,available}], error?}. In
  // Docker with AG2ASSISTANT_ACP_BRIDGE set this reflects the host bridge.
  codingAgents: () => j('GET', G('/coding/agents')),
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
  // Create ONE subfolder in a host directory the picker is viewing; resolves to the new
  // folder's absolute path. Rejects (400/409) carry a message meant to be shown as-is.
  makeDir: (path, name) => j('POST', G('/fs/mkdir'), { path, name }),
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

  // ---- Skills (install-wide Enable/Disable; ADR 0016).
  // Snapshot shape: {skills:[{name,description,origin:'bundled'|'global',enabled}]}.
  // setSkillState fans out a reload to every profile, so the toggle takes effect
  // everywhere from the next turn.
  skills: () => j('GET', G('/skills')),
  setSkillState: (name, enabled) => j('POST', G('/skills/' + encodeURIComponent(name) + '/state'), { enabled }),
  // Delete a Global skill from disk install-wide; cascade-purges every profile's
  // Suppression and fans out a reload. Bundled → 409, unknown → 404 (ADR 0016 t03).
  deleteSkill: (name) => j('DELETE', G('/skills/' + encodeURIComponent(name))),

  // ---- Installing skills (ADR 0017). Search is target-agnostic; install/discover
  // target the SURFACE — these Global variants land in the install-wide layer & fan out.
  // Registry: {results:[{name,install_id,description,installs}]}. discover: {skills:[…]}.
  searchSkills: (query, limit = 10) => j('POST', G('/skills/search'), { query, limit }),
  installSkill: (body) => j('POST', G('/skills/install'), body), // {install_id} or {git_url, names}
  discoverSkills: (git_url) => j('POST', G('/skills/discover'), { git_url }),
  discoverSkillsUpload: (file) => jForm(G('/skills/discover-upload'), formFile(file)),
  installSkillUpload: (file, names) => jForm(G('/skills/install-upload'), formFile(file, names)),

  // ---- Skills (per-profile: Suppression of shared skills + own-skill state; ADR 0016 t02).
  // Scoped to the ACTIVE profile via P(). Row shape adds {suppressed, available} to the
  // install-wide {name,description,origin,enabled}. A change reloads only this profile.
  profileSkills: () => j('GET', P('/skills')),
  suppressSkill: (name, suppressed) =>
    suppressed
      ? j('POST', P('/skills/' + encodeURIComponent(name) + '/suppress'))
      : j('DELETE', P('/skills/' + encodeURIComponent(name) + '/suppress')),
  setProfileSkillState: (name, enabled) =>
    j('POST', P('/skills/' + encodeURIComponent(name) + '/state'), { enabled }),
  // Delete one of THIS profile's own skills from disk (active profile only). A shared
  // skill → 409 (delete a Global one from Application → Skills instead) (ADR 0016 t03).
  deleteProfileSkill: (name) => j('DELETE', P('/skills/' + encodeURIComponent(name))),

  // Install into THIS profile (ADR 0017). Search reuses the global searchSkills; only the
  // install/discover half is profile-scoped so the surface carries the target.
  installProfileSkill: (body) => j('POST', P('/skills/install'), body),
  discoverProfileSkills: (git_url) => j('POST', P('/skills/discover'), { git_url }),
  discoverProfileSkillsUpload: (file) => jForm(P('/skills/discover-upload'), formFile(file)),
  installProfileSkillUpload: (file, names) => jForm(P('/skills/install-upload'), formFile(file, names)),

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
  // Per-profile model Active override (ADR 0015): point THIS profile's Active Text /
  // Live model at a shared install-wide config id; an empty string clears the override
  // (→ back to the install-wide Active). Distinct from the install-wide useLlmConfig /
  // useLiveConfig the composer switcher and Models page call. → {ok, llm_override|
  // live_override: id|null}. The effective + override ids are reported by settings().
  setLlmOverride: (configId = '') => j('POST', P('/settings/llm-override'), { config_id: configId }),
  setLiveOverride: (configId = '') => j('POST', P('/settings/live-override'), { config_id: configId }),
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
  // The Folder roots browsable in the open Thread — the tree's Thread-scoped Folder
  // section (ADR 0013) → {roots:[{id, name, path (absolute), mode, exists}]}. chatId
  // scopes chat-only grants / blocks; empty = profile-level grants only.
  folderRoots: (chatId = '') =>
    j('GET', P('/folders/roots' + (chatId ? '?chat_id=' + encodeURIComponent(chatId) : ''))),
  // One Directory level inside a granted Folder (lazy-expand) → {path, dirs:[{name,
  // path}], files:[{name, path, size}], mode}, noise pruned, authorized + chatId-scoped
  // server-side. `mode` is THIS level's resolved Grant mode (read | read_write) — the
  // tree derives its rows' write affordances from it (ticket 04). `path` is absolute (a
  // Folder Directory); 404 throws via j().
  folderList: (path, chatId = '') =>
    j('GET', P('/files?path=' + encodeURIComponent(path) + (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''))),
  // Corpus search for the composer's `@`-picker (ADR 0012): matches across the Files
  // space AND every Folder this profile∪chat can read → {results:[{path (absolute),
  // name, dir, kind}]}, ranked filename-first and bounded. chatId scopes chat-only
  // grants; a blank/no-match query yields an empty list.
  searchFiles: (q, chatId = '') =>
    j('GET', P('/files/search?q=' + encodeURIComponent(q) + (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''))),
  // The preview rail's "Mentioned in N threads" backlink (ADR 0014): the current
  // profile's Threads (Chats + Task Runs) whose transcript mentions this file →
  // {threads:[{stream_id, kind:'chat'|'run', title, updated, task_id?, task_name?,
  // run_started_at?}]}, newest-first, hidden-when-empty. `path` is the previewed
  // file's path (relative = Files-space, absolute = Folder); `chatId` mirrors the
  // other /files helpers' signature but the scan is profile-wide.
  fileMentions: (path, chatId = '') =>
    j('GET', P('/files/mentions?path=' + encodeURIComponent(path) + (chatId ? '&chat_id=' + encodeURIComponent(chatId) : ''))),
  // A Folder (absolute) `path` carries `chatId` so the server resolves the Grant for
  // THIS Thread; a Files-space (relative) path ignores it (rawQuery decides — ADR 0013).
  fileUrl: (path, download = false, chatId = '') =>
    P('/files/raw?' + rawQuery(path, { download, chatId })),
  fileText: async (path, chatId = '') => {
    const r = await fetch(P('/files/raw?' + rawQuery(path, { chatId })))
    if (!r.ok) throw new Error('file not found')
    return r.text()
  },
  // Like fileText but also returns the served file's `ETag` → {text, etag, mode},
  // unquoted to match the bare etag saveFile hands back. `mode` is the server's
  // `X-File-Mode` (read | read_write) — the resolved Grant mode a Folder file's
  // edit affordance gates on (ADR 0013, ticket 04); a Files-space file reads back
  // read_write (the user owns their space).
  fileTextWithEtag: async (path, chatId = '') => {
    const r = await fetch(P('/files/raw?' + rawQuery(path, { chatId })))
    if (!r.ok) throw new Error('file not found')
    const text = await r.text()
    return { text, etag: parseEtag(r.headers.get('ETag')), mode: r.headers.get('X-File-Mode') || '' }
  },
  // In-place write (ADR 0011): PUT the UTF-8 body with `If-Match: <etag>`, resolving
  // to the new etag. A failure throws an Error carrying `.status`/`.body` (409/404/400
  // distinct); a 404 here is a missing file, not a vanished profile — so not via j().
  // A Folder (absolute) path carries `chatId` so the server resolves its read_write
  // Grant for THIS Thread (a read-only Folder file is 403 — ticket 04).
  saveFile: async (path, text, etag, chatId = '') => {
    const r = await fetch(P('/files/raw?' + rawQuery(path, { chatId })), {
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
  // Delete a file OR a Directory (recursive) — same route, extended server-side. A
  // Folder (absolute) path carries `chatId` for its read_write Grant (ticket 04).
  deleteFile: (path, chatId = '') => j('DELETE', P('/files/raw?' + rawQuery(path, { chatId }))),
  // Upload OS files into a target Directory (empty = root). Multipart, so it skips
  // j()'s JSON envelope; name clashes are auto-suffixed server-side (never overwrites).
  // An ABSOLUTE `dir` uploads into a Folder Directory under a read_write Grant, scoped
  // by `chatId` (a read-only Folder is 403 — ticket 05).
  uploadFiles: async (fileList, dir = '', chatId = '') => {
    const fd = new FormData()
    for (const f of fileList) fd.append('files', f, f.name)
    fd.append('dir', dir)
    if (chatId) fd.append('chat_id', chatId)
    const r = await fetch(P('/files/upload'), { method: 'POST', body: fd })
    if (!r.ok) {
      let msg = 'upload failed (' + r.status + ')'
      try { const b = await r.json(); if (b && b.error) msg = b.error } catch {}
      throw new Error(msg)
    }
    return r.json()
  },
  // New empty Directory (409 if it already exists → surfaced as an inline error). An
  // ABSOLUTE `path` creates a Folder Directory under a read_write Grant scoped by
  // `chatId` (ticket 05).
  mkdir: (path, chatId = '') => j('POST', P('/files/mkdir'), { path, chat_id: chatId }),
  // Move/rename a file or Directory. 409 if the destination exists (never overwrites).
  // A Folder move (absolute `from`/`to`) carries `chatId` and is confined to the
  // source's readable root server-side — a cross-Root target is rejected (ticket 04).
  moveFile: (from, to, chatId = '') => j('POST', P('/files/move'), { from, to, chat_id: chatId }),
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
