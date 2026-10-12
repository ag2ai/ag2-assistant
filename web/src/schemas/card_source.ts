import { z } from 'zod'

export const CardSource = z.object({
  tool: z.enum(['get_weather', 'get_quotes']).nullish(), code: z.string().nullish(),
  files: z.record(z.string(), z.string()).optional(), args: z.record(z.string(), z.unknown()).optional(),
  parameters: z.record(z.string(), z.unknown()).optional(), fields: z.record(z.string(), z.unknown()),
  required: z.array(z.string()).optional(), path: z.string().optional(),
  interval_seconds: z.number().nullish(), secret_names: z.array(z.string()).optional(),
  secrets: z.record(z.string(), z.string()).optional(),
})
export type CardSource = z.infer<typeof CardSource>
export const CardSourceEvent = z.object({
  created_at: z.number().optional(), surface_id: z.string(), source_id: z.string(), path: z.string(),
  status: z.enum(['updated', 'error', 'approval_required', 'discarded', 'configured']),
  error: z.string(), data: z.record(z.string(), z.unknown()), code_version: z.string(),
})
export type CardSourceEvent = z.infer<typeof CardSourceEvent>
export const CardSourceResponse = z.object({
  status: z.enum(['updated', 'error', 'approval_required', 'discarded', 'configured']),
  events: z.array(z.object({ type: z.string(), data: z.record(z.string(), z.unknown()) })),
})
export type SourceTarget = { source_id: string; path?: string; chat_id?: string; surface_id?: string }
