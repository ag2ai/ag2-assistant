// Skills, per-profile layer (app.py 2777-2909; ADR 0016 t02 / ADR 0017). Rows add
// {suppressed, available} to the install-wide shape; a change reloads only this
// profile. Search reuses the global searchSkills — only install/discover is scoped.
import { api as P } from '../../lib/profile.js'
import { del, get, post, postForm } from '../http.ts'
import { ProfileSkillInstalled, ProfileSkillList, ProfileSkillMutated, SkillDiscovered } from '../../schemas/index.ts'
import { formFile, type SkillInstallBody } from './skills.ts'

export const profileSkillsApi = {
  profileSkills: () => get(P('/skills'), ProfileSkillList),

  suppressSkill: (name: string, suppressed: boolean) =>
    suppressed
      ? post(P('/skills/' + encodeURIComponent(name) + '/suppress'), undefined, ProfileSkillMutated)
      : del(P('/skills/' + encodeURIComponent(name) + '/suppress'), ProfileSkillMutated),

  setProfileSkillState: (name: string, enabled: boolean) =>
    post(P('/skills/' + encodeURIComponent(name) + '/state'), { enabled }, ProfileSkillMutated),

  // Deletes one of THIS profile's own skills from disk; a shared skill → 409
  // (delete a Global one from Application → Skills instead).
  deleteProfileSkill: (name: string) =>
    del(P('/skills/' + encodeURIComponent(name)), ProfileSkillMutated),

  installProfileSkill: (body: SkillInstallBody) =>
    post(P('/skills/install'), body, ProfileSkillInstalled),

  discoverProfileSkills: (git_url: string) =>
    post(P('/skills/discover'), { git_url }, SkillDiscovered),

  discoverProfileSkillsUpload: (file: File) =>
    postForm(P('/skills/discover-upload'), formFile(file), SkillDiscovered),

  installProfileSkillUpload: (file: File, names: string[] | string) =>
    postForm(P('/skills/install-upload'), formFile(file, names), ProfileSkillInstalled),
}
