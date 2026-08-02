// Shared store for the install-wide Folder registry + Grants (CONTEXT.md
// "Folders", ADR 0006) — the lib/secrets.js pattern. Settings → Folders, the
// per-chat ChatFolders modal, and the Composer's chip strip all subscribe to
// ONE snapshot, so adding/removing a Folder or flipping a grant on any surface
// updates every surface live. The snapshot is install-wide (all Folders + all
// their grants); each surface derives its own view (this profile / this chat).
import { writable } from 'svelte/store'
import { api } from '../transport/api/index.ts'

export const foldersStore = writable({ folders: [], loaded: false })

export async function loadFolders() {
  try {
    const r = await api.folders()
    foldersStore.set({ folders: r.folders || [], loaded: true })
  } catch {
    foldersStore.set({ folders: [], loaded: true })
  }
}

// Every Folder mutator endpoint (create/update/delete, set/revoke grant) returns
// the full {folders} snapshot — push it to the store so all subscribers refresh
// at once. Returns the response untouched so callers can chain on it.
export function applyFolders(r) {
  foldersStore.set({ folders: (r && r.folders) || [], loaded: true })
  return r
}
