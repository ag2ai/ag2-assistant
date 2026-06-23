// Thin REST client for the durable bits AG2 streams don't carry: the task
// tree/schedule/deliverables panel, and the lists for the drawer.

async function j(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let msg = `${method} ${path} -> ${r.status}`
    try { const e = await r.json(); if (e && e.error) msg = e.error } catch {}
    throw new Error(msg)
  }
  return r.json()
}

export const api = {
  sessions: () => j('GET', '/api/sessions').then((d) => d.sessions || []),
  tasksAll: (status) => j('GET', '/api/tasks/all' + (status && status !== 'all' ? '?status=' + status : '')).then((d) => d.tasks || []),
  task: (id) => j('GET', '/api/tasks/' + id).then((d) => d.task),
  cancelTask: (id) => j('POST', `/api/tasks/${id}/cancel`),
  markSeen: (id) => j('POST', `/api/tasks/${id}/seen`),
  archiveTask: (id, archived = true) => j('POST', `/api/tasks/${id}/archive`, { archived }),
  inquiries: () => j('GET', '/api/inquiries/pending').then((d) => d.pending || []),
  answerInquiry: (id, answer) => j('POST', `/api/inquiries/${encodeURIComponent(id)}/answer`, { answer }),
  // Chat-turn permission prompts (run_code/shell/file) live in the HitlServer, a
  // separate store from durable task inquiries — surfaced in the same strip.
  hitlPending: () => j('GET', '/api/hitl/pending').then((d) => d.pending || []),
  answerHitl: (id, answer) => j('POST', `/hitl/${encodeURIComponent(id)}/answer`, { answer }),
  googleStatus: () => j('GET', '/api/google/status'),
  googleLoginUrl: () => j('POST', '/api/google/login_url'),
  googleCredentials: (content) => j('POST', '/api/google/credentials', { content }),
  googleLogout: () => j('POST', '/api/google/logout'),
  voices: () => j('GET', '/api/voice/voices'),
  settings: () => j('GET', '/api/settings'),
  setKey: (provider, value) => j('POST', '/api/settings/key', { provider, value }),
  setLlm: (provider, model) => j('POST', '/api/settings/llm', { provider, model }),
  setOnboarded: (value = true) => j('POST', '/api/settings/onboarded', { value }),
  listDirs: (path = '') => j('GET', '/api/fs/list?path=' + encodeURIComponent(path)),
  setProjectFolder: (path) => j('POST', '/api/settings/project-folder', { path }),
  rerunTask: (id) => j('POST', `/api/tasks/${encodeURIComponent(id)}/rerun`),
  setVoiceProvider: (provider) => j('POST', '/api/settings/voice_provider', { provider }),
  addMcpServer: (server) => j('POST', '/api/settings/mcp', server),
  deleteMcpServer: (name) => j('DELETE', `/api/settings/mcp/${encodeURIComponent(name)}`),
  healthMcpServer: (name) => j('POST', `/api/settings/mcp/${encodeURIComponent(name)}/health`),
  getMemory: () => j('GET', '/api/memory'),
  setMemory: (text) => j('POST', '/api/memory', { text }),
  // Workspace files (the agent's working file space)
  files: () => j('GET', '/api/files'),
  fileUrl: (path, download = false) =>
    '/api/files/raw?path=' + encodeURIComponent(path) + (download ? '&download=true' : ''),
  fileText: async (path) => {
    const r = await fetch('/api/files/raw?path=' + encodeURIComponent(path))
    if (!r.ok) throw new Error('file not found')
    return r.text()
  },
  deleteFile: (path) => j('DELETE', '/api/files/raw?path=' + encodeURIComponent(path)),
  usage: () => j('GET', '/api/usage'),
  selectVoice: (voice) => j('POST', '/api/voice/select', { voice }),
  previewVoice: async (voice) => {
    const r = await fetch('/api/voice/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice }),
    })
    if (!r.ok) throw new Error('preview failed (' + r.status + ')')
    return r.blob()
  },
}
