// AG-UI events and the JSON responses of the chat routes.
import { z } from 'zod'
import { EventSchema } from '@ag-ui/core/schemas'

// The gateway carries opaque protocol objects; the official schema validates their fields.
export const AguiEvent = z.record(z.string(), z.unknown()).pipe(EventSchema)
export type AguiEvent = z.infer<typeof AguiEvent>

// POST /chats/{id}/feed: whether a running Turn took the message.
export const FeedResponse = z.object({ fed: z.boolean() })
export type FeedResponse = z.infer<typeof FeedResponse>
