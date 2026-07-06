// Thin REST client for the durable bits AG2 streams don't carry: the task
// tree/schedule/deliverables panel, and the lists for the drawer.
//
// Profile-scoped routes go through P() (→ /api/p/{pid}/…); genuinely global
// routes (profiles registry, secrets, onboarded, google, fs browser) go
// through G() (→ /api/…). See lib/profile.js.

import { api as P, globalApi as G, onProfileGone } from '../lib/profile.js'

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
  // Install-wide token/cost roll-up across all profiles: {profiles:[{pid,name,...}],
  // total}. The HUD derives the active profile's numbers from `profiles` and appends
  // the `total` only when more than one profile exists (one request, not two).
  usageAll: () => j('GET', G('/usage')),
  setKey: (provider, value) => j('POST', G('/secrets/key'), { provider, value }),
  setOnboarded: (value = true) => j('POST', G('/onboarded'), { value }),
  listDirs: (path = '') => j('GET', G('/fs/list?path=' + encodeURIComponent(path))),
  googleStatus: () => j('GET', G('/google/status')),
  googleLoginUrl: () => j('POST', G('/google/login_url')),
  googleCredentials: (content) => j('POST', G('/google/credentials'), { content }),
  googleLogout: () => j('POST', G('/google/logout')),
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

  // ---- Profile-scoped (/api/p/{pid}/…) ----
  sessions: () => j('GET', P('/sessions')).then((d) => d.sessions || []),
  tasksAll: (status) => j('GET', P('/tasks/all' + (status && status !== 'all' ? '?status=' + status : ''))).then((d) => d.tasks || []),
  task: (id) => j('GET', P('/tasks/' + id)).then((d) => d.task),
  cancelTask: (id) => j('POST', P(`/tasks/${id}/cancel`)),
  markSeen: (id) => j('POST', P(`/tasks/${id}/seen`)),
  archiveTask: (id, archived = true) => j('POST', P(`/tasks/${id}/archive`), { archived }),
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
  setLlm: (provider, model) => j('POST', P('/settings/llm'), { provider, model }),
  setProjectFolder: (path) => j('POST', P('/settings/project-folder'), { path }),
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
}
