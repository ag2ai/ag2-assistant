import { writable } from 'svelte/store'

const BASE = '/app'

function parse() {
  const p = location.pathname
  let m
  if ((m = p.match(/^\/app\/t\/(.+)$/))) return { name: 'task', id: decodeURIComponent(m[1]) }
  if ((m = p.match(/^\/app\/c\/(.+)$/))) return { name: 'chat', id: decodeURIComponent(m[1]) }
  return { name: 'home' }
}

export const route = writable(parse())

export function go(path) {
  const full = BASE + path
  if (location.pathname !== full) history.pushState({}, '', full)
  route.set(parse())
}

export function newChatId() {
  return 'web-' + Math.random().toString(36).slice(2, 10)
}

window.addEventListener('popstate', () => route.set(parse()))
