// Persistent, install-wide COMMAND permission grants (app.py 1465-1511). Every
// mutation returns the full snapshot {ok, commands}.
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import { PermissionMutated, PermissionSnapshot } from '../../schemas/index.ts'

export const permissionsApi = {
  permissions: () => get(G('/permissions'), PermissionSnapshot),

  // prefix is a shell command prefix (e.g. "git"), or null for the whole tool.
  grantCommand: (tool: string, prefix?: string | null) =>
    post(G('/permissions/commands'), { tool, prefix }, PermissionMutated),

  revokeCommand: (rule: string) =>
    del(G('/permissions/commands'), PermissionMutated, { rule }),
}
