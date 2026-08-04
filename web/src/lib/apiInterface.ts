// When a Text model may choose the wire it speaks, what it may choose, and what it
// becomes when the choice is withdrawn. Store-free and transport-free.

// The wires a custom endpoint can be addressed over, in the order they are offered.
export const API_INTERFACES = ['openai_responses', 'openai', 'anthropic'] as const
export type ApiInterface = (typeof API_INTERFACES)[number]

// Where a type lands once it no longer names an endpoint; absent = stays put.
const SETTLES_TO: Record<string, string> = { openai: 'openai_responses' }

// Whether the type takes a Base URL at all — the field is shown for these three.
export function usesBaseUrl(type: string): boolean {
  return (API_INTERFACES as readonly string[]).includes(type)
}

// Whether the API interface control is shown: a named endpoint, and a type that
// can address one.
export function offersApiInterface(type: string, baseUrl: string | null | undefined): boolean {
  return !!baseUrl?.trim() && usesBaseUrl(type)
}

// What the type becomes when the Base URL is withdrawn.
export function settleWithoutBaseUrl(type: string): string {
  return SETTLES_TO[type] || type
}
