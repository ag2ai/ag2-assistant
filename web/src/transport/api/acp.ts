// ACP listeners (GLOBAL, install-level, ADR 0031): one listener bound to one
// Profile at creation, never exposure-gated — so unlike connections.ts there is
// no exposure/pairing/groups table hung off one (gateway/routes/acp.py).
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import { AcpListener, AcpListenerCreated, AcpListenerList, AcpListenerTokenRotated } from '../../schemas/acp.ts'
import { Ok } from '../../schemas/primitives.ts'

const at = (cid: string, suffix = '') => G('/acp/listeners/' + encodeURIComponent(cid) + suffix)

export const acpApi = {
  acpListeners: () => get(G('/acp/listeners'), AcpListenerList).then((d) => d.listeners),

  // Create and start one; a blank token generates one, returned here exactly
  // once. A listener that will not start (bad profile, taken port) still comes
  // back 200 with its reason on the embedded row.
  createAcpListener: (profile: string, port: number, name = '', token = '') =>
    post(G('/acp/listeners'), { profile, port, name, token }, AcpListenerCreated),

  // Stops it if live and forgets the record with its token.
  deleteAcpListener: (cid: string) => del(at(cid), Ok),

  stopAcpListener: (cid: string) => post(at(cid, '/stop'), undefined, AcpListener),

  startAcpListener: (cid: string) => post(at(cid, '/start'), undefined, AcpListener),

  // Mints a fresh shared secret and restarts the listener on it, invalidating
  // every existing connection at once. The new token is answered exactly once.
  rotateAcpListenerToken: (cid: string) =>
    post(at(cid, '/rotate-token'), undefined, AcpListenerTokenRotated),
}
