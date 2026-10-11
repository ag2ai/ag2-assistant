// Throwaway sidebar layouts A/B/C on the existing route, selected with ?variant=.
import { get, writable } from 'svelte/store'
import { route } from '../../router.ts'
import type { Tab } from '../../lib/route.ts'

export type SidebarVariant = 'A' | 'B' | 'C'
export const prototypeEnabled = import.meta.env.DEV && new URLSearchParams(location.search).has('variant')
const initial = new URLSearchParams(location.search).get('variant')
export const sidebarVariant = writable<SidebarVariant>(initial === 'B' || initial === 'C' ? initial : 'A')
export const sidebarSection = writable<Tab>(get(route).tab)
export const sidebarPanelOpen = writable(true)
export const prototypeNames = { A: 'VS Code', B: 'Compact workspace', C: 'Collapsible explorer' }

export function keepPrototypeVariant() {
  const url = new URL(location.href)
  url.searchParams.set('variant', get(sidebarVariant))
  history.replaceState({}, '', url)
}

export function cycleSidebarVariant(direction: number) {
  const variants: SidebarVariant[] = ['A', 'B', 'C']
  sidebarVariant.set(variants[(variants.indexOf(get(sidebarVariant)) + direction + 3) % 3])
  sidebarPanelOpen.set(true)
  keepPrototypeVariant()
}

window.addEventListener('popstate', () => {
  const next = new URLSearchParams(location.search).get('variant')
  if (next === 'A' || next === 'B' || next === 'C') sidebarVariant.set(next)
})
