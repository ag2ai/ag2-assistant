<script lang="ts">
  import { api } from '../transport/api/index.ts'
  import { StreamClient } from '../transport/stream.ts'
  import { projectSourceEvent } from '../lib/cardSources.ts'
  import type { CardSourceEvent } from '../schemas/card_source.ts'
  import { asComponent, asComponents } from '../lib/a2ui.ts'
  import { errText } from '../lib/errors.ts'
  import A2UISurface from './items/A2UISurface.svelte'
  import type { CardInstance } from '../schemas/card_instance.ts'
  import type { ThreadItem } from '../schemas/events.ts'

  let { path, revision }: { path: string; revision: string } = $props()
  let instance: CardInstance | null = $state(null)
  let error = $state('')
  let sourceStates: Record<string, CardSourceEvent> = $state({})
  const item = $derived.by((): Extract<ThreadItem, { kind: 'a2ui' }> | null => {
    if (!instance) return null
    const message = instance.message
    return { id: 0, kind: 'a2ui', surfaceId: message.surface_id, version: message.version,
      catalogId: message.catalog_id, title: message.title, intent: message.intent,
      component: asComponent(message.component), components: asComponents(message.component._components), data: message.data, sourceStates }
  })
  $effect(() => {
    const p = path
    void revision
    let stale = false
    instance = null; error = ''; sourceStates = {}
    const load = () => api.cardInstance(p)
      .then(result => { if (!stale) instance = result })
      .catch(cause => { if (!stale) error = errText(cause) })
    const stream = new StreamClient('', {
      onReady: () => { void load() },
      onEvent: event => {
        if (stale || !item || !instance) return
        const updated = { ...item }
        if (!projectSourceEvent(updated, event, p)) return
        sourceStates = updated.sourceStates || {}
        instance = { ...instance, message: { ...instance.message, data: updated.data } }
      },
    }, '/card-sources/stream').connect()
    void load()
    return () => { stale = true; stream.close() }
  })
</script>

{#if error}
  <p role="alert">This Card instance could not be previewed: {error}</p>
  <p class="muted">Open Edit to repair the source, or download the file.</p>
{:else if item}
  <p class="muted">Saved instance · Actions and inputs are inactive.</p>
  <A2UISurface {item} passive filePath={path} />
{:else}
  <p class="muted" role="status">Loading instance…</p>
{/if}
