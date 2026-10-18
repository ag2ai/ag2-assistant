// Chats: the drawer rows and their metadata (app.py 2227-2261).
import { api as P } from '../../lib/profile.ts'
import { del, get, patch, post } from '../http.ts'
import type { A2UISurfaceState, AttachmentPayload } from '../../schemas/events.ts'
import { ChatEvents, ChatList, FeedResponse, Ok, Transcript } from '../../schemas/index.ts'

export const chatsApi = {
  chats: () => get(P('/chats'), ChatList).then((d) => d.chats),

  // One chat: its display transcript, its Chat model override ('' = it inherits)
  // and the model it would run on right now. An unknown chat answers with an
  // empty transcript rather than a 404.
  chat: (id: string, limit?: number) => get(P('/chats/' + encodeURIComponent(id) + (limit ? '?limit=' + limit : '')), Transcript),

  // A page of the Chat's history as AG-UI events: the latest one, or the one before `before`.
  chatEvents: (id: string, before?: string) =>
    get(P('/chats/' + encodeURIComponent(id) + '/events?limit=10' + (before ? '&before=' + encodeURIComponent(before) : '')), ChatEvents),

  // What a client does to a Chat besides running a Turn on it (transport/agui.ts).
  cancelChat: (id: string) => post(P('/chats/' + encodeURIComponent(id) + '/cancel'), undefined, Ok),

  // False when no Turn is running to take the message; the caller then runs it.
  feedChat: (id: string, text: string, attachments?: AttachmentPayload[]) =>
    post(P('/chats/' + encodeURIComponent(id) + '/feed'), { text, attachments }, FeedResponse),

  rateChat: (id: string, rating: Record<string, unknown>) =>
    post(P('/chats/' + encodeURIComponent(id) + '/feedback'), rating, Ok),

  clearChatRating: (id: string, target: { target_kind: string; target_id: string }) =>
    post(P('/chats/' + encodeURIComponent(id) + '/feedback/clear'), target, Ok),

  // A click on an A2UI surface; `state` is the clicked instance's data model.
  chatA2ui: (id: string, message: unknown, state?: A2UISurfaceState) =>
    post(P('/chats/' + encodeURIComponent(id) + '/a2ui'), state ? { message, state } : { message }, Ok),

  deleteChat: (id: string) => del(P('/chats/' + encodeURIComponent(id)), Ok),

  // Partial chat-metadata update: {title?, starred?, model?} (absent = unchanged;
  // model '' clears the Chat override back to inheriting — ADR 0025).
  updateChat: (id: string, patchBody: { title?: string; starred?: boolean; model?: string }) =>
    patch(P('/chats/' + encodeURIComponent(id)), patchBody, Ok),

  // Owner's kill switch on a live ACP chat: drops the remote client,
  // keeps the Chat. 404 (not an ACP chat) / 409 (not live) surface as ApiError.
  closeAcpSession: (id: string) =>
    post(P('/chats/' + encodeURIComponent(id) + '/acp/close'), undefined, Ok),
}
