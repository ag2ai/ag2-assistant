// Pure helpers for the composer's per-chat Folder shortcut (CONTEXT.md "Grant").
// Import-free so they unit-test under node:test; the api glue lives in Composer.
//
// The strip mirrors the ChatFolders modal's two sections: chips are chat-ONLY
// folders (a chat Grant, no profile Grant), and the "+N profile folder" note
// covers profile-reachable folders. A chat-scoped `none` Grant is a per-chat
// BLOCK, not access — it never renders as a chip and hides its profile folder
// from the note.

const chatGrant = (f, pid, chatId) => (f.grants || []).find((g) => g.profile === pid && g.chat_id === chatId)
const hasProfileGrant = (f, pid) => (f.grants || []).some((g) => g.profile === pid && !g.chat_id)

// Chat-only folders as chip descriptors — a chat Grant (read/read_write), no
// profile Grant behind it, so the chip's `×` fully removes access. Profile
// folders (even with a per-chat override) live in the note / modal instead.
export function chatChips(folders, pid, chatId) {
  return (folders || [])
    .map((f) => {
      const g = chatGrant(f, pid, chatId)
      if (!g || g.mode === 'none' || hasProfileGrant(f, pid)) return null
      return { id: f.id, name: f.name, path: f.path, exists: f.exists !== false, mode: g.mode }
    })
    .filter(Boolean)
}

// Profile-granted folders that still reach this chat (i.e. not blocked by a
// chat-scoped `none` override) — drives the "+N profile folder" note that opens
// the full ChatFolders modal.
export function profileExtraCount(folders, pid, chatId) {
  return (folders || []).filter((f) => {
    if (!hasProfileGrant(f, pid)) return false
    const cg = chatGrant(f, pid, chatId)
    return !(cg && cg.mode === 'none') // blocked for this chat → not reachable here
  }).length
}

// What "add this folder to the chat" should do, given the folder's current grants.
// The shortcut grants read, which any profile grant already covers.
//   exists   — already a chat chip; covered — a profile grant reaches it;
//   unblock  — a profile folder the chat blocked (drop the block); grant — mint the chat read grant
export function addPlan(folder, pid, chatId) {
  const chat = chatGrant(folder, pid, chatId)
  if (hasProfileGrant(folder, pid)) {
    if (chat && chat.mode === 'none') return { status: 'unblock', id: folder.id, name: folder.name }
    return { status: 'covered', name: folder.name }
  }
  if (chat) return { status: 'exists' }
  return { status: 'grant', id: folder.id }
}
