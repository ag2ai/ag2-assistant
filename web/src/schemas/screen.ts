import { z } from 'zod'
import { CardInstanceMessage } from './card_instance.ts'

export const ScreenRow = z.object({ path: z.string(), title: z.string(), error: z.string() })
export const ScreenList = z.object({ screens: z.array(ScreenRow) })
export const ScreenSource = z.object({
  slot: z.string(), path: z.string(), surface_id: z.string(), source_id: z.string(),
  source: z.record(z.string(), z.unknown()),
})
export const Screen = z.object({
  path: z.string(), title: z.string(), message: CardInstanceMessage,
  sources: z.record(z.string(), ScreenSource),
})
export type Screen = z.infer<typeof Screen>
export type ScreenRow = z.infer<typeof ScreenRow>
