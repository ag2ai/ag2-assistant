// Plain-JSON responses of the AG-UI routes (the two streams name no body).
import { z } from 'zod'

// POST /chats/{id}/feed: whether a running Turn took the message.
export const FeedResponse = z.object({ fed: z.boolean() })
export type FeedResponse = z.infer<typeof FeedResponse>
