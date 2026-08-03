// Install-wide status and account surfaces: activity, usage, onboarding, the
// host folder picker, channels, the universal memory doc and the OAuth cards.
import { globalApi as G } from '../../lib/profile.ts'
import { get, post } from '../http.ts'
import {
  Channels,
  CodexLoginUrl,
  CodexStatus,
  FsListing,
  FsMkdirResult,
  GoogleLoginUrl,
  GoogleStatus,
  IdentitySeeded,
  MemoryDoc,
  Ok,
  OkOrError,
  StatusList,
  UsageRollup,
} from '../../schemas/index.ts'

// IdentityRequest — every field optional; the doc is seeded, never clobbered.
export type IdentityFields = {
  name?: string | null
  location?: string | null
  hours?: string | null
  style?: string | null
}

export const systemApi = {
  // Per-profile activity for the drawer badges — a bare array, not an envelope.
  status: () => get(G('/status'), StatusList),

  // Install-wide token/cost roll-up across all profiles, in one request.
  usageAll: () => get(G('/usage'), UsageRollup),

  setOnboarded: (value = true) => post(G('/onboarded'), { value }, Ok),

  listDirs: (path = '') => get(G('/fs/list?path=' + encodeURIComponent(path)), FsListing),

  // Create ONE subfolder in a host directory the picker is viewing; 400/409
  // carry a message meant to be shown as-is.
  makeDir: (path: string, name: string) => post(G('/fs/mkdir'), { path, name }, FsMkdirResult),

  googleStatus: () => get(G('/google/status'), GoogleStatus),
  googleLoginUrl: () => post(G('/google/login_url'), undefined, GoogleLoginUrl),
  googleCredentials: (content: string) => post(G('/google/credentials'), { content }, OkOrError),
  googleLogout: () => post(G('/google/logout'), undefined, Ok),

  // OpenAI ChatGPT/Codex subscription sign-in. The gateway runs a loopback OAuth
  // on localhost:1455; headless users paste the code to /codex/submit with the
  // flow `state` returned here. Account-level, so GLOBAL like Google.
  codexStatus: () => get(G('/codex/status'), CodexStatus),
  codexLoginUrl: () => post(G('/codex/login_url'), undefined, CodexLoginUrl),
  codexSubmit: (state: string, code: string) => post(G('/codex/submit'), { state, code }, Ok),
  codexLogout: () => post(G('/codex/logout'), undefined, Ok),

  // Messaging channels are install-level: a platform binds to exactly one profile
  // (or is disabled). The bind persists even if start fails (active:false + error).
  channels: () => get(G('/channels'), Channels),
  channelBind: (platform: string, profile: string | null) =>
    post(G('/channels'), { platform, profile }, Channels),
  // tokens is {ENV_NAME: value|''} — empty clears. Values are never echoed.
  channelTokens: (platform: string, tokens: Record<string, string>) =>
    post(G('/channels/token'), { platform, tokens }, Channels),

  // The universal "who the user is" doc — one install-wide memory shared by every
  // profile. The per-profile persona memory is getMemory/setMemory in settings.ts.
  globalMemory: () => get(G('/memory'), MemoryDoc),
  setGlobalMemory: (text: string) => post(G('/memory'), { text }, Ok),
  setIdentity: (fields: IdentityFields) => post(G('/identity'), fields, IdentitySeeded),
}
