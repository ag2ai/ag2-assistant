// The one `api` object every consumer imports. Split by domain for editing;
// recomposed here so call sites keep reading api.listTasks() and friends.
//
// Profile-scoped routes go through P() (→ /api/p/{pid}/…); genuinely global
// routes (profiles registry, secrets, onboarded, google, fs browser) go through
// G() (→ /api/…). See lib/profile.js.
import { chatsApi } from './chats.ts'
import { codingApi } from './coding.ts'
import { filesApi } from './files.ts'
import { foldersApi } from './folders.ts'
import { llmApi } from './llm.ts'
import { permissionsApi } from './permissions.ts'
import { profileSkillsApi } from './profileSkills.ts'
import { profilesApi } from './profiles.ts'
import { secretsApi } from './secrets.ts'
import { settingsApi } from './settings.ts'
import { skillsApi } from './skills.ts'
import { systemApi } from './system.ts'
import { tasksApi } from './tasks.ts'

export const api = {
  ...profilesApi,
  ...secretsApi,
  ...llmApi,
  ...foldersApi,
  ...permissionsApi,
  ...skillsApi,
  ...systemApi,
  ...codingApi,
  ...chatsApi,
  ...tasksApi,
  ...filesApi,
  ...settingsApi,
  ...profileSkillsApi,
}

export type Api = typeof api
