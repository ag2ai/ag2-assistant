import { derived } from 'svelte/store'
import { route } from '../router.js'
import { scopeToken } from './route.js'

// The open Thread's Folder-grant scope token for the folder API's `chat_id` slot,
// derived from the route — the source of truth for what's on screen (`$thread` isn't
// reset on nav, so it can go stale). `scopeToken` maps kind → token (lib/route.js).
export const threadScope = derived(route, scopeToken)
