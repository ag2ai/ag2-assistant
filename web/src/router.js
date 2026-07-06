import { writable } from 'svelte/store'
import { getActiveProfileId } from './lib/profile.js'

const BASE = '/app'

// Routes carry the profile id: /app/{pid}/, /app/{pid}/c/{sid}, /app/{pid}/t/{tid}.
function parse() {
  const p = location.pathname
  let m
  if ((m = p.match(/^\/app\/([^/]+)\/t\/(.+)$/))) return { name: 'task', pid: decodeURIComponent(m[1]), id: decodeURIComponent(m[2]) }
  if ((m = p.match(/^\/app\/([^/]+)\/c\/(.+)$/))) return { name: 'chat', pid: decodeURIComponent(m[1]), id: decodeURIComponent(m[2]) }
  if ((m = p.match(/^\/app\/([^/]+)\/?$/))) return { name: 'home', pid: decodeURIComponent(m[1]) }
  // Bare /app/ or any legacy/unknown shape → home with no pid (boot resolves it).
  return { name: 'home', pid: null }
}

export const route = writable(parse())

// The pid segment for URLs: the one in the current path if any, else the active id.
function currentPid() {
  const r = parse()
  return r.pid || getActiveProfileId()
}

// go('/c/xxx') → /app/{pid}/c/xxx. Pass a pid explicitly to switch profiles.
export function go(path, pid = currentPid()) {
  const full = BASE + '/' + pid + path
  if (location.pathname !== full) history.pushState({}, '', full)
  route.set(parse())
}

// Replace the current URL with /app/{pid}/ (used on boot to canonicalise a
// bare /app/ or a stale pid into the resolved profile). replaceState so the
// bare URL doesn't linger in history.
export function redirectToProfile(pid) {
  const full = BASE + '/' + pid + '/'
  if (location.pathname !== full) history.replaceState({}, '', full)
  route.set(parse())
}

export function newChatId() {
  return 'web-' + Math.random().toString(36).slice(2, 10)
}

window.addEventListener('popstate', () => route.set(parse()))
