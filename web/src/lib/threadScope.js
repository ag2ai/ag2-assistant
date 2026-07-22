import { derived } from 'svelte/store'
import { thread } from '../store.js'
import { route } from '../router.js'

// The open Thread's Folder-grant scope token for the folder API's `chat_id` slot: an
// open Task page → `task:{id}` (route wins so a stale `$thread.chat` can't shadow it),
// else `$thread.chat` (a chat id, or a run's `task-run:{run_id}`); '' when none open.
export const threadScope = derived([thread, route], ([$thread, $route]) =>
  $route?.kind === 't' && $route.id ? 'task:' + $route.id : $thread.chat || ''
)
