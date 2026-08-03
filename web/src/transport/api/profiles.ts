// Global profile registry routes (app.py 1851-1958).
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import { Ok, ProfileEnvelope, ProfileList } from '../../schemas/index.ts'

export const profilesApi = {
  profiles: () => get(G('/profiles'), ProfileList),

  createProfile: (name: string, accent: string) =>
    post(G('/profiles'), { name, accent }, ProfileEnvelope),

  // Metadata update (§4.2): {name?, accent?} — both registry-only, display changes.
  updateProfile: (pid: string, body: { name?: string; accent?: string }) =>
    post(G('/profiles/' + encodeURIComponent(pid)), body, ProfileEnvelope),

  // Archive (§4.9). newDefault is required when archiving the active_default —
  // passed in the request body (DELETE with body → ProfileArchiveRequest).
  archiveProfile: (pid: string, newDefault?: string | null) =>
    del(G('/profiles/' + encodeURIComponent(pid)), Ok, newDefault ? { new_default: newDefault } : {}),

  // Restore (unarchive + boot live) an archived profile → {profile}. 409 if it isn't
  // archived, 404 if unknown (ADR 0003).
  restoreProfile: (pid: string) =>
    post(G('/profiles/' + encodeURIComponent(pid) + '/restore'), undefined, ProfileEnvelope),

  // Permanently delete an ARCHIVED profile (erases its folder). ?purge=true escalates
  // the DELETE from archive to hard-delete; 409 if the profile isn't archived yet.
  deleteProfile: (pid: string) =>
    del(G('/profiles/' + encodeURIComponent(pid) + '?purge=true'), Ok),
}
