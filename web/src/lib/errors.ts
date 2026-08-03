// Message of a thrown value. A catch binding is `unknown` under strict mode, so
// every `String(e.message || e)` in the UI funnels through here instead of a cast.

export function errText(e: unknown): string {
  if (e && typeof e === 'object' && 'message' in e && e.message) return String(e.message)
  return String(e)
}
