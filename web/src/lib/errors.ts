// Message of a thrown value. A catch binding is `unknown` under strict mode, so
// every `String(e.message || e)` in the UI funnels through here instead of a cast.

// `fallback` stands in for a thrown value that carries no message at all.
export function errText(e: unknown, fallback = ''): string {
  if (e && typeof e === 'object' && 'message' in e && e.message) return String(e.message)
  return fallback || String(e)
}
