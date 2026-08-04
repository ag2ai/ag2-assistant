// Connections (GLOBAL, install-level): one configured instance of a messaging
// platform, plus the three tables hung off it — which profiles it can reach, who
// may speak to it, and where each of its group conversations lands (app.py
// 2089-2300). A platform connects as many times as you want, so every route below
// is keyed by a connection id, never by the platform.
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import {
  Connection,
  ConnectionExposure,
  ConnectionGroups,
  ConnectionList,
  ConnectionPairing,
  Ok,
  PairingCodeIssued,
} from '../../schemas/index.ts'

const at = (cid: string, suffix = '') => G('/connections/' + encodeURIComponent(cid) + suffix)

export const connectionsApi = {
  connections: () => get(G('/connections'), ConnectionList).then((d) => d.connections),

  // Create and start one; `tokens` is {ENV_NAME: value}, a blank name takes the
  // platform's next default. One that fails to start answers 200 with active:false.
  createConnection: (platform: string, name: string, tokens: Record<string, string>) =>
    post(G('/connections'), { platform, name, tokens }, Connection),

  renameConnection: (cid: string, name: string) => post(at(cid), { name }, Connection),

  // Replace every token and restart on them, keeping the connection's id. One that
  // will not start is rolled back and 400s with the reason.
  replaceConnectionTokens: (cid: string, tokens: Record<string, string>) =>
    post(at(cid, '/token'), { tokens }, Connection),

  // Stop it and forget it, with its tokens, Peers, paired accounts, pairing code,
  // default-profile entry and exposure records.
  deleteConnection: (cid: string) => del(at(cid), Ok),

  // Where this connection's conversations land by default (profile:null clears it).
  // A profile withdrawn from every surface is refused with 400.
  connectionDefault: (cid: string, profile: string | null) =>
    post(at(cid, '/default'), { profile }, Connection),

  // Default-allow: a profile nobody withdrew reads true on every surface.
  connectionExposure: (cid: string) => get(at(cid, '/exposure'), ConnectionExposure),

  // Expose or withdraw one profile on one surface → that same view; withdrawing the
  // default's last surface clears the default, so re-render from the response.
  setConnectionExposure: (cid: string, profile: string, surface: string, exposed: boolean) =>
    post(at(cid, '/exposure'), { profile, surface, exposed }, ConnectionExposure),

  // Paired accounts, a grant to this one connection and no other (ADR 0021).
  connectionPairing: (cid: string) => get(at(cid, '/pairing'), ConnectionPairing),

  // Pair by numeric account id or by @handle; a handle is a pending invitation.
  connectionPair: (cid: string, value: string) =>
    post(at(cid, '/pairing'), { value }, ConnectionPairing),

  connectionUnpair: (cid: string, key: string) =>
    del(at(cid, '/pairing/' + encodeURIComponent(key)), ConnectionPairing),

  // Mints this connection's one live code, replacing its earlier one — and answers
  // with that code alone, not the roster.
  connectionPairingCode: (cid: string) =>
    post(at(cid, '/pairing/code'), undefined, PairingCodeIssued),

  // Group chats on this connection, with the profiles reachable on THIS
  // connection's group surface — what a group may be re-pointed at (ADR 0022).
  connectionGroups: (cid: string) => get(at(cid, '/groups'), ConnectionGroups),

  connectionGroupProfile: (cid: string, chatId: string, profile: string) =>
    post(at(cid, '/groups/' + encodeURIComponent(chatId) + '/profile'), { profile }, ConnectionGroups),
}
