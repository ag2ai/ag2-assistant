// Thin REST client for the durable bits AG2 streams don't carry: the task
// tree/schedule/deliverables panel, and the lists for the drawer.

async function j(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}`)
  return r.json()
}

export const api = {
  sessions: () => j('GET', '/api/sessions').then((d) => d.sessions || []),
  tasksAll: (status) => j('GET', '/api/tasks/all' + (status && status !== 'all' ? '?status=' + status : '')).then((d) => d.tasks || []),
  task: (id) => j('GET', '/api/tasks/' + id).then((d) => d.task),
  cancelTask: (id) => j('POST', `/api/tasks/${id}/cancel`),
  archiveTask: (id, archived = true) => j('POST', `/api/tasks/${id}/archive`, { archived }),
  inquiries: () => j('GET', '/api/inquiries/pending').then((d) => d.pending || []),
  answerInquiry: (id, answer) => j('POST', `/api/inquiries/${encodeURIComponent(id)}/answer`, { answer }),
  googleStatus: () => j('GET', '/api/google/status'),
  googleLoginUrl: () => j('POST', '/api/google/login_url'),
  googleCredentials: (content) => j('POST', '/api/google/credentials', { content }),
  googleLogout: () => j('POST', '/api/google/logout'),
  voices: () => j('GET', '/api/voice/voices'),
  selectVoice: (voice) => j('POST', '/api/voice/select', { voice }),
  previewVoice: async (voice) => {
    const r = await fetch('/api/voice/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice }),
    })
    if (!r.ok) throw new Error('preview failed (' + r.status + ')')
    return r.blob()
  },
}
