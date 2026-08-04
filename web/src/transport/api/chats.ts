// Chats: the drawer rows and their metadata (app.py 2227-2261).
import { api as P } from '../../lib/profile.ts'
import { del, get, patch } from '../http.ts'
import { ChatList, Ok, Transcript } from '../../schemas/index.ts'

export const chatsApi = {
  chats: () => get(P('/chats'), ChatList).then((d) => d.chats),

  // One chat: its display transcript, its Chat model override ('' = it inherits)
  // and the model it would run on right now. An unknown chat answers with an
  // empty transcript rather than a 404.
  chat: (id: string) => get(P('/chats/' + encodeURIComponent(id)), Transcript),

  deleteChat: (id: string) => del(P('/chats/' + encodeURIComponent(id)), Ok),

  // Partial chat-metadata update: {title?, starred?, model?} (absent = unchanged;
  // model '' clears the Chat override back to inheriting — ADR 0025).
  updateChat: (id: string, patchBody: { title?: string; starred?: boolean; model?: string }) =>
    patch(P('/chats/' + encodeURIComponent(id)), patchBody, Ok),
}
