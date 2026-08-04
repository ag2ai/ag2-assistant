// Folders + Grants — the install-wide registry (app.py 1512-1580, ADR 0006).
// Every mutation echoes the whole snapshot alongside ok.
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import { FolderList, FolderMutated, FolderSaved, type GrantMode } from '../../schemas/index.ts'

export const foldersApi = {
  folders: () => get(G('/folders'), FolderList),

  // 409 carries err.body.existing when the path is already registered.
  createFolder: (path: string, name = '') => post(G('/folders'), { path, name }, FolderSaved),

  updateFolder: (id: string, patch: { name?: string; path?: string }) =>
    post(G('/folders/' + encodeURIComponent(id)), patch, FolderSaved),

  deleteFolder: (id: string) => del(G('/folders/' + encodeURIComponent(id)), FolderMutated),

  // An empty chatId/taskId is a profile-scope grant. `none` is the block a chat- or
  // task-scoped grant writes over an inherited Folder; at profile scope it is a 400
  // (folders.py:241).
  setGrant: (id: string, profile: string, mode: GrantMode, chatId = '', taskId = '') =>
    post(
      G('/folders/' + encodeURIComponent(id) + '/grants'),
      { profile, chat_id: chatId, task_id: taskId, mode },
      FolderMutated,
    ),

  revokeGrant: (id: string, profile: string, chatId = '', taskId = '') =>
    del(G('/folders/' + encodeURIComponent(id) + '/grants'), FolderMutated, {
      profile,
      chat_id: chatId,
      task_id: taskId,
    }),
}
