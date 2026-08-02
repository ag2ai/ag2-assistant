// Skills: the install-wide projection, this profile's resolved view, and the
// registry/git install flows.
import { z } from 'zod'

export const SkillOrigin = z.enum(['bundled', 'global', 'profile'])
export type SkillOrigin = z.infer<typeof SkillOrigin>

// GET /api/skills — install-wide Bundled + Global rows.
export const Skill = z.object({
  name: z.string(),
  description: z.string(),
  origin: SkillOrigin,
  enabled: z.boolean(),
})
export type Skill = z.infer<typeof Skill>

// GET /api/p/{pid}/skills — the same rows plus the per-profile resolution.
export const ProfileSkill = Skill.extend({
  suppressed: z.boolean(),
  available: z.boolean(),
})
export type ProfileSkill = z.infer<typeof ProfileSkill>

export const SkillList = z.object({ skills: z.array(Skill) })
export type SkillList = z.infer<typeof SkillList>

export const ProfileSkillList = z.object({ skills: z.array(ProfileSkill) })
export type ProfileSkillList = z.infer<typeof ProfileSkillList>

export const SkillSearchHit = z.object({
  name: z.string(),
  install_id: z.string(),
  description: z.string(),
  installs: z.number(),
})
export type SkillSearchHit = z.infer<typeof SkillSearchHit>

export const SkillSearchResults = z.object({ results: z.array(SkillSearchHit) })
export type SkillSearchResults = z.infer<typeof SkillSearchResults>

export const DiscoveredSkill = z.object({ name: z.string(), description: z.string() })
export type DiscoveredSkill = z.infer<typeof DiscoveredSkill>

export const SkillDiscovered = z.object({ skills: z.array(DiscoveredSkill) })
export type SkillDiscovered = z.infer<typeof SkillDiscovered>

// Install routes return ok + the installed rows + the refreshed projection. Both
// registry_install and install_from_source yield {name, description} rows, so
// `installed` mirrors the discover shape rather than being a list of names.
// The global surface returns install-wide rows, the profile surface its own.
export const SkillInstalled = z.object({
  ok: z.literal(true),
  installed: z.array(DiscoveredSkill),
  skills: z.array(Skill),
})
export type SkillInstalled = z.infer<typeof SkillInstalled>

export const ProfileSkillInstalled = z.object({
  ok: z.literal(true),
  installed: z.array(DiscoveredSkill),
  skills: z.array(ProfileSkill),
})
export type ProfileSkillInstalled = z.infer<typeof ProfileSkillInstalled>

// Enable/disable and suppress routes echo ok + the refreshed projection.
export const SkillMutated = z.object({ ok: z.literal(true), skills: z.array(Skill) })
export type SkillMutated = z.infer<typeof SkillMutated>

export const ProfileSkillMutated = z.object({
  ok: z.literal(true),
  skills: z.array(ProfileSkill),
})
export type ProfileSkillMutated = z.infer<typeof ProfileSkillMutated>
