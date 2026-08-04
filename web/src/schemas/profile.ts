// Profiles registry: the boot payload every client starts from.
import { z } from 'zod'

export const Profile = z.object({
  id: z.string(),
  name: z.string(),
  accent: z.string(),
  workspace: z.string(),
  created: z.string(),
})
export type Profile = z.infer<typeof Profile>

export const ProfileList = z.object({
  profiles: z.array(Profile),
  archived: z.array(Profile),
  active_default: z.string().nullable(),
  onboarded: z.boolean(),
  version: z.string(),
  ag2_version: z.string(),
})
export type ProfileList = z.infer<typeof ProfileList>

export const ProfileEnvelope = z.object({ profile: Profile })
export type ProfileEnvelope = z.infer<typeof ProfileEnvelope>
