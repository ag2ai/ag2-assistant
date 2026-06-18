import { writable } from 'svelte/store'

// The active thread: a projection of one AG2 stream. `items` are folded from
// `{type,data}` events (see project.js). `kind` is 'chat' or 'task'.
export const thread = writable({ id: null, kind: 'chat', items: [], busy: false })

// Drawer: unified history of chats + tasks.
export const sessions = writable([])
export const tasks = writable([])
export const drawerTab = writable('chats') // 'chats' | 'tasks'

// Current task's durable panel data (tree/schedule/deliverables), when kind==='task'.
export const taskPanel = writable(null)

// Durable HITL: pending questions/permissions across all tasks, answerable
// anywhere (polled). Survives restarts — backed by the InquiryStore.
export const inquiries = writable([])

// Google connect modal open/closed.
export const googleOpen = writable(false)

// Voice picker modal open/closed.
export const voicePickerOpen = writable(false)

// Deliverable full-view modal: { title, text } when open, null when closed.
export const viewer = writable(null)

// Settings modal open/closed (launches voice picker + Google from one place).
export const settingsOpen = writable(false)

// Memory viewer/editor modal open/closed.
export const memoryOpen = writable(false)

// A localStorage-backed preference (per-device): survives reloads.
function persisted(key, initial) {
  let v = initial
  try { const s = localStorage.getItem(key); if (s !== null) v = JSON.parse(s) } catch {}
  const w = writable(v)
  w.subscribe((val) => { try { localStorage.setItem(key, JSON.stringify(val)) } catch {} })
  return w
}

// Play a chime when the assistant needs your input (HITL). Off by default.
export const soundOnInput = persisted('soundOnInput', false)
