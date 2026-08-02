// Skills, install-wide layer (app.py 1659-1849; ADR 0016/0017). Search is
// target-agnostic; install/discover here land in the Global layer and fan out.
import { globalApi as G } from '../../lib/profile.js'
import { del, get, post, postForm } from '../http.ts'
import {
  SkillDiscovered,
  SkillInstalled,
  SkillList,
  SkillMutated,
  SkillSearchResults,
} from '../../schemas/index.ts'

// The multipart body a skill upload expects: the file plus a comma-separated
// `names` field — multipart can't carry a JSON array.
export function formFile(file: File, names?: string[] | string): FormData {
  const fd = new FormData()
  fd.append('file', file)
  if (names !== undefined) fd.append('names', Array.isArray(names) ? names.join(',') : String(names))
  return fd
}

// SkillInstallRequest: a registry install_id, or a git_url plus the names to take.
export type SkillInstallBody = {
  install_id?: string
  git_url?: string
  names?: string[]
}

export const skillsApi = {
  skills: () => get(G('/skills'), SkillList),

  setSkillState: (name: string, enabled: boolean) =>
    post(G('/skills/' + encodeURIComponent(name) + '/state'), { enabled }, SkillMutated),

  // Bundled → 409, unknown → 404 (ADR 0016 t03).
  deleteSkill: (name: string) => del(G('/skills/' + encodeURIComponent(name)), SkillMutated),

  searchSkills: (query: string, limit = 10) =>
    post(G('/skills/search'), { query, limit }, SkillSearchResults),

  installSkill: (body: SkillInstallBody) => post(G('/skills/install'), body, SkillInstalled),

  discoverSkills: (git_url: string) => post(G('/skills/discover'), { git_url }, SkillDiscovered),

  discoverSkillsUpload: (file: File) =>
    postForm(G('/skills/discover-upload'), formFile(file), SkillDiscovered),

  installSkillUpload: (file: File, names: string[] | string) =>
    postForm(G('/skills/install-upload'), formFile(file, names), SkillInstalled),
}
