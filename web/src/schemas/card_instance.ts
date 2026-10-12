import { z } from 'zod'

export const CardInstanceMessage = z.object({
  surface_id: z.string(), version: z.literal('v1.0'), catalog_id: z.string(),
  component: z.record(z.string(), z.unknown()), data: z.record(z.string(), z.unknown()),
  title: z.string(), intent: z.string(),
})
export type CardInstanceMessage = z.infer<typeof CardInstanceMessage>
export const CardInstance = z.object({
  kind: z.literal('card-instance'), format_version: z.literal(1),
  instance_id: z.string(), saved_at: z.string(), save_request_id: z.string(),
  request_hash: z.string(), message: CardInstanceMessage,
})
export type CardInstance = z.infer<typeof CardInstance>
export const CardInstanceSave = z.object({
  status: z.literal('saved'), path: z.string(), instance_id: z.string(),
  saved_at: z.string(), history_recorded: z.boolean(), message: z.string(),
})
export type CardInstanceSave = z.infer<typeof CardInstanceSave>
export type CardInstanceSaveRequest = {
  surface_id: string
  message: Omit<CardInstanceMessage, 'surface_id'>
  path: string
  request_id: string
}
