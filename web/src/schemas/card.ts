// Card definitions, their availability, and files that did not load.
import { z } from 'zod'

export const CardOrigin = z.enum(['bundled', 'global', 'profile'])
export const CardDefinition = z.object({
  name: z.string(),
  description: z.string(),
  topic: z.string(),
  origin: CardOrigin,
  path: z.string(),
  enabled: z.boolean(),
})
export type CardDefinition = z.infer<typeof CardDefinition>

export const ProfileCard = CardDefinition.extend({
  suppressed: z.boolean(),
  available: z.boolean(),
})
export type ProfileCard = z.infer<typeof ProfileCard>

export const CardProblem = z.object({ path: z.string(), origin: CardOrigin, error: z.string() })
export type CardProblem = z.infer<typeof CardProblem>
export const CardFile = z.object({ path: z.string(), name: z.string(), origin: CardOrigin })
export type CardFile = z.infer<typeof CardFile>

export const CardList = z.object({
  cards: z.array(CardDefinition),
  problems: z.array(CardProblem),
  files: z.array(CardFile),
})
export type CardList = z.infer<typeof CardList>
export const ProfileCardList = z.object({
  cards: z.array(ProfileCard),
  problems: z.array(CardProblem),
  files: z.array(CardFile),
})
export type ProfileCardList = z.infer<typeof ProfileCardList>
export const CardMutated = CardList.extend({ ok: z.literal(true) })
export const ProfileCardMutated = ProfileCardList.extend({ ok: z.literal(true) })


export const CardSave = z.object({
  status: z.enum(['saved', 'conflict']),
  name: z.string(),
  path: z.string(),
  conflict_token: z.string(),
  history_recorded: z.boolean(),
  message: z.string(),
})
export type CardSave = z.infer<typeof CardSave>

export type CardSaveRequest = {
  draft_id: string
  expected_version: number
  surface_id: string
  name: string
  filename: string
  request_id: string
  replace?: boolean
  conflict_token?: string
}
