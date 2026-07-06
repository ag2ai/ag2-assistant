// Guard for agent-produced link targets. A2UI surfaces (NewsDigest etc.) carry
// URLs the model/tools generated from web content, so a hostile or hallucinated
// `javascript:`/`data:` scheme could execute in the app origin if bound straight
// into an <a href>. Svelte escapes the attribute value but does NOT block the
// scheme, and these links bypass the DOMPurify path markdown.js uses. Only http(s)
// is safe to navigate to; anything else returns null so callers render plain text.
export function safeUrl(u) {
  if (typeof u !== 'string') return null
  return /^https?:\/\//i.test(u.trim()) ? u : null
}
