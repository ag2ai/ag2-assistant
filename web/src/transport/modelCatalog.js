// The browser's own model-catalog probe, for a key pasted into the model form and
// not yet saved: it calls the provider directly, never our backend (ADR 0024).
// The shaping and parsing are pure functions in lib/modelSuggest.js.
import {
  REASON,
  browserProbeRequest,
  parseCatalogPayload,
  probeStatusReason,
} from '../lib/modelSuggest.js'

/**
 * Answers in the same {models, reason} envelope as api.llmCatalog().
 * @returns {Promise<{models: string[], reason: string}>}
 */
export async function fetchModelCatalog({ type, baseUrl = '', key = '', refresh = false }) {
  const request = browserProbeRequest({ type, baseUrl, key })
  if (!request) return { models: [], reason: REASON.NOT_PROBEABLE }
  let response
  try {
    // `refresh` asks past whatever the browser cached, like the route's ?refresh=1.
    response = await fetch(request.url, {
      headers: request.headers,
      cache: refresh ? 'reload' : 'default',
    })
  } catch {
    // A browser cannot tell a cross-origin refusal from a dead host; both are this.
    return { models: [], reason: REASON.UNREACHABLE }
  }
  const reason = probeStatusReason(response.status)
  if (reason) return { models: [], reason }
  let payload
  try {
    payload = await response.json()
  } catch {
    return { models: [], reason: REASON.NO_LIST_ENDPOINT }
  }
  const models = parseCatalogPayload(type, payload)
  return models ? { models, reason: '' } : { models: [], reason: REASON.NO_LIST_ENDPOINT }
}
