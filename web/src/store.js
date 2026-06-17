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
