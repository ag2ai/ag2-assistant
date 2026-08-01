// The browser's own model-catalog probe, for a key that has been pasted into the
// model form and not yet saved. It calls the provider directly: that key reaches
// the provider that owns it and never our backend, our logs or our process memory
// until the user commits to creating a Secret from it (ADR 0024). The gateway
// route next door is not the general answer it looks like — it authenticates by
// secret_id only and accepts no key material.
//
// Deliberately thin: the request shaping and the payload parsing are pure
// functions in lib/modelSuggest.js, tested there without a network. Only this
// call is untested, and there is nothing in it to get wrong.
import { browserProbeRequest, parseCatalogPayload, probeStatusReason } from '../lib/modelSuggest.js'

/**
 * Answers in the same {models, reason} envelope as api.llmCatalog(), so the form
 * treats both paths alike.
 * @returns {Promise<{models: string[], reason: string}>}
 */
export async function fetchModelCatalog({ type, baseUrl = '', key = '', refresh = false }) {
  const request = browserProbeRequest({ type, baseUrl, key })
  if (!request) return { models: [], reason: 'not_probeable' }
  let response
  try {
    // `refresh` is the re-read control asking past whatever the browser cached —
    // the counterpart of the gateway route's ?refresh=1.
    response = await fetch(request.url, {
      headers: request.headers,
      cache: refresh ? 'reload' : 'default',
    })
  } catch {
    // A cross-origin refusal is indistinguishable from a dead host in a browser,
    // so both read as unreachable — less precise than the gateway path manages
    // for the same endpoint, and an accepted cost of the rule.
    return { models: [], reason: 'unreachable' }
  }
  const reason = probeStatusReason(response.status)
  if (reason) return { models: [], reason }
  let payload
  try {
    payload = await response.json()
  } catch {
    return { models: [], reason: 'no_list_endpoint' }
  }
  const models = parseCatalogPayload(type, payload)
  return models ? { models, reason: '' } : { models: [], reason: 'no_list_endpoint' }
}
