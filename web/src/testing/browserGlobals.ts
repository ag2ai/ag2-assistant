// Minimal browser globals for modules that reach router.js while initialising.
// router.js reads location and registers popstate/hashchange listeners at module
// load, so a `node --test` file must install these BEFORE importing such a module
// (dynamic import). Only what the load path touches is provided — this is not a DOM.

type Listener = (event: unknown) => void

export type BrowserGlobals = {
  listeners: Map<string, Listener[]>
  dispatch: (type: string, event?: unknown) => void
}

export function installBrowserGlobals(url = 'http://localhost/app/'): BrowserGlobals {
  const listeners = new Map<string, Listener[]>()
  const parsed = new URL(url)
  const loc = {
    href: parsed.href,
    pathname: parsed.pathname,
    hash: parsed.hash,
    search: parsed.search,
    origin: parsed.origin,
    assign() {},
    replace() {},
  }
  const win = {
    addEventListener(type: string, fn: Listener) {
      listeners.set(type, [...(listeners.get(type) ?? []), fn])
    },
    removeEventListener(type: string, fn: Listener) {
      listeners.set(type, (listeners.get(type) ?? []).filter((f) => f !== fn))
    },
    location: loc,
  }
  const history = {
    pushState(_state: unknown, _title: string, path: string) {
      const next = new URL(path, parsed.origin)
      loc.href = next.href
      loc.pathname = next.pathname
      loc.hash = next.hash
      loc.search = next.search
    },
    replaceState(state: unknown, title: string, path: string) {
      history.pushState(state, title, path)
    },
  }
  define('location', loc)
  define('window', win)
  define('history', history)
  return {
    listeners,
    dispatch: (type, event) => {
      for (const fn of listeners.get(type) ?? []) fn(event)
    },
  }
}

function define(name: string, value: unknown): void {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
}
