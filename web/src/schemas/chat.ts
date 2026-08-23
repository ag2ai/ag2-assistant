// Chats: the drawer rows and the display transcript a reopened chat restores.
import { z } from 'zod'

// gateway/core.py writes {role, text} pairs; role is 'user' or 'agent'.
export const TranscriptMessage = z.object({
  role: z.enum(['user', 'agent']),
  text: z.string(),
})
export type TranscriptMessage = z.infer<typeof TranscriptMessage>

// The ACP-origin join: present together on a chat an ACP Peer owns,
// absent on an owner-started one (gateway ships them via response_model_exclude_unset,
// never null — see AGENTS.md "Front-end types"). origin_name is the listener
// Connection's display name, the truth — never the client's self-reported
// clientInfo. origin_live reflects the gateway's live-session registry.
export const ChatRow = z.object({
  chat_id: z.string(),
  updated: z.string(),
  title: z.string(),
  starred: z.boolean(),
  preview: z.string(),
  turns: z.number(),
  origin_platform: z.string().optional(),
  origin_name: z.string().optional(),
  origin_live: z.boolean().optional(),
})
export type ChatRow = z.infer<typeof ChatRow>

export const ChatList = z.object({ chats: z.array(ChatRow) })
export type ChatList = z.infer<typeof ChatList>

// GET /chats/{id}: the display transcript, plus the Chat's own model override
// ('' = it inherits) and the model a message sent right now would run on, so the
// composer's switcher needs no second call (ADR 0025). Same origin trio as
// ChatRow, so Thread renders the badge + close control without a second fetch.
export const Transcript = z.object({
  chat_id: z.string(),
  messages: z.array(TranscriptMessage),
  model: z.string(),
  effective_model: z.string(),
  origin_platform: z.string().optional(),
  origin_name: z.string().optional(),
  origin_live: z.boolean().optional(),
})
export type Transcript = z.infer<typeof Transcript>

// POST /message — the only route with a declared response_model (MessageResponse).
export const MessageReply = z.object({ reply: z.string(), chat_id: z.string() })
export type MessageReply = z.infer<typeof MessageReply>
