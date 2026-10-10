import { z } from 'zod'

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
