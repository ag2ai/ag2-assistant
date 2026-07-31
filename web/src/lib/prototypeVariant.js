// PROTOTYPE PLUMBING — throwaway. Delete with the prototype.
//
// The app's real router owns the URL *hash* (router.js), so a prototype variant
// switch can't live there without fighting it. The variant lives in the URL
// *search* param instead — `?variant=B` — which the router never touches, so it
// survives reloads and can be shared, and the two never collide.
import { writable } from 'svelte/store'

const read = () => new URLSearchParams(location.search).get('variant') || ''

export const variant = writable(read())

export function setVariant(v) {
  const u = new URL(location.href)
  if (v) u.searchParams.set('variant', v)
  else u.searchParams.delete('variant')
  history.replaceState(history.state, '', u)
  variant.set(v)
}

window.addEventListener('popstate', () => variant.set(read()))

// Vite sets this false in `vite build` — a stray merge can't ship the switcher.
export const PROTOTYPE_ENABLED = import.meta.env.DEV
