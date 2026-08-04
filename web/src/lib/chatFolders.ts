// Pure helpers for the composer's per-chat Folder shortcut (CONTEXT.md "Grant").
// Import-free so they unit-test under node:test; the api glue lives in Composer.
//
// The strip mirrors the ChatFolders modal's two sections: chips are chat-ONLY
// folders (a chat Grant, no profile Grant and no COVERING task Grant — a task
// `none` Grant is a block, not a cover, so it doesn't suppress the chip), and
// the "+N folder" note covers profile/task-reachable folders. A chat-scoped
// `none` Grant is a per-chat BLOCK, not access — it never renders as a chip
// and hides its profile/task folder from the note.
//
// Three-level grant model (profile / task / chat): a task Grant scopes a
// profile's access to that task's runs (its chats). `taskId` is only ever
// meaningful for a run chat — plain chats and Settings pass '' and see only
// the profile level.

// The Folder/Grant fields these helpers read. Structural rather than the Folder
// schema: a legacy persisted grant can lack `task_id`, and the picker's own rows
// arrive without `exists`.
export type GrantRef = { profile: string; chat_id: string; task_id?: string; mode: string }
export type FolderRef = { id: string; name: string; path: string; exists?: boolean; grants?: GrantRef[] }

// One removable chip in the composer strip.
export type FolderChip = { id: string; name: string; path: string; exists: boolean; mode: string }

const chatGrant = (f: FolderRef, pid: string, chatId: string): GrantRef | undefined =>
  (f.grants || []).find((g) => g.profile === pid && g.chat_id === chatId)
export const taskGrant = (f: FolderRef, pid: string, taskId: string): GrantRef | null | undefined =>
  taskId ? (f.grants || []).find((g) => g.profile === pid && g.task_id === taskId && !g.chat_id) : null
const hasProfileGrant = (f: FolderRef, pid: string): boolean =>
  (f.grants || []).some((g) => g.profile === pid && !g.chat_id && !g.task_id)
// A folder "reaches" the chat from above when a profile or task grant covers it —
// per-level `none` blocks: a task `none` kills the profile grant for this task's
// runs; a chat `none` (handled by callers) kills both.
const inheritedGrant = (f: FolderRef, pid: string, taskId: string): GrantRef | null => {
  const t = taskGrant(f, pid, taskId)
  if (t) return t.mode === 'none' ? null : t
  const p = (f.grants || []).find((g) => g.profile === pid && !g.chat_id && !g.task_id)
  return p || null
}

// Chat-only folders as chip descriptors — a chat Grant (read/read_write), no
// profile OR task Grant behind it, so the chip's `×` fully removes access.
// Profile/task folders (even with a per-chat override) live in the note / modal
// instead.
export function chatChips(
  folders: readonly FolderRef[] | null | undefined,
  pid: string,
  chatId: string,
  taskId = '',
): FolderChip[] {
  return (folders || [])
    .map((f) => {
      const g = chatGrant(f, pid, chatId)
      const tg = taskGrant(f, pid, taskId)
      if (!g || g.mode === 'none' || hasProfileGrant(f, pid) || (tg && tg.mode !== 'none')) return null
      return { id: f.id, name: f.name, path: f.path, exists: f.exists !== false, mode: g.mode }
    })
    .filter((c): c is FolderChip => c !== null)
}

// Profile/task-granted folders that still reach this chat (i.e. not blocked by
// a chat-scoped `none` override) — drives the "+N folder" note that opens the
// full ChatFolders modal.
export function inheritedCount(
  folders: readonly FolderRef[] | null | undefined,
  pid: string,
  chatId: string,
  taskId = '',
): number {
  return (folders || []).filter((f) => {
    if (!inheritedGrant(f, pid, taskId)) return false
    const cg = chatGrant(f, pid, chatId)
    return !(cg && cg.mode === 'none') // blocked for this chat → not reachable here
  }).length
}

// What the composer's add shortcut resolves to for one folder.
export type AddPlan =
  | { status: 'exists' }
  | { status: 'covered'; name: string }
  | { status: 'unblock'; id: string; name: string }
  | { status: 'grant'; id: string }

// What "add this folder to the chat" should do, given the folder's current grants.
// The shortcut grants read, which any profile/task grant already covers.
//   exists   — already a chat chip; covered — a profile/task grant reaches it;
//   unblock  — a folder the chat blocked (drop the block); grant — mint the chat read grant
export function addPlan(folder: FolderRef, pid: string, chatId: string, taskId = ''): AddPlan {
  const chat = chatGrant(folder, pid, chatId)
  if (inheritedGrant(folder, pid, taskId)) {
    if (chat && chat.mode === 'none') return { status: 'unblock', id: folder.id, name: folder.name }
    return { status: 'covered', name: folder.name }
  }
  if (chat) return { status: 'exists' }
  return { status: 'grant', id: folder.id }
}
