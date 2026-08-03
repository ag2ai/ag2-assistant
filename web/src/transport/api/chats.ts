// Chats: the drawer rows and their metadata (app.py 2227-2261).
import { api as P } from '../../lib/profile.ts'
import { del, get, patch } from '../http.ts'
import { ChatList, Ok } from '../../schemas/index.ts'

export const chatsApi = {
  chats: () => get(P('/chats'), ChatList).then((d) => d.chats),

  deleteChat: (id: string) => del(P('/chats/' + encodeURIComponent(id)), Ok),

  // Partial chat-metadata update: {title?, starred?} (absent = unchanged).
  updateChat: (id: string, patchBody: { title?: string; starred?: boolean }) =>
    patch(P('/chats/' + encodeURIComponent(id)), patchBody, Ok),
}
