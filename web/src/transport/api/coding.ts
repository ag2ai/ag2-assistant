// CLI coding agents: the Settings "Coding agents" card and one adapter's model
// catalog (app.py 862-894, 2100-2117).
import { globalApi as G } from '../../lib/profile.ts'
import { get } from '../http.ts'
import { CodingAgents, CodingCatalog } from '../../schemas/index.ts'

export const codingApi = {
  // In Docker with AG2ASSISTANT_ACP_BRIDGE set this reflects the host bridge;
  // an unreachable bridge reports connected:false rather than raising.
  codingAgents: () => get(G('/coding/agents'), CodingAgents),

  // agent is 'claude' | 'codex'. An empty catalog carries a reason so the form
  // can say why it fell back to a free-text model field; refresh skips the TTL cache.
  codingModels: (agent: string, refresh = false) =>
    get(G(`/coding/${agent}/models${refresh ? '?refresh=1' : ''}`), CodingCatalog),
}
