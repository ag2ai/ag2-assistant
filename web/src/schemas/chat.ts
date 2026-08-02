// Chats: the drawer rows and the display transcript a reopened chat restores.
import { z } from 'zod'

// gateway/core.py writes {role, text} pairs; role is 'user' or 'agent'.
export const TranscriptMessage = z.object({
  role: z.enum(['user', 'agent']),
  text: z.string(),
})
export type TranscriptMessage = z.infer<typeof TranscriptMessage>

export const ChatRow = z.object({
  chat_id: z.string(),
  updated: z.string(),
  title: z.string(),
  starred: z.boolean(),
  preview: z.string(),
  turns: z.number(),
})
export type ChatRow = z.infer<typeof ChatRow>

export const ChatList = z.object({ chats: z.array(ChatRow) })
export type ChatList = z.infer<typeof ChatList>

export const Transcript = z.object({
  chat_id: z.string(),
  messages: z.array(TranscriptMessage),
})
export type Transcript = z.infer<typeof Transcript>

// POST /message — the only route with a declared response_model (MessageResponse).
export const MessageReply = z.object({ reply: z.string(), chat_id: z.string() })
export type MessageReply = z.infer<typeof MessageReply>
