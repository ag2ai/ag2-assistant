<script lang="ts">
  import { api } from '../transport/api/index.ts'
  import { StreamClient } from '../transport/stream.ts'
  import { route, openAsideFile } from '../router.ts'
  import { profileEpoch } from '../store.ts'
  import { errText } from '../lib/errors.ts'
  import { asComponent, asComponents } from '../lib/a2ui.ts'
  import { projectScreenEvent } from '../lib/screens.ts'
  import { CardSource, type CardSourceEvent } from '../schemas/card_source.ts'
  import type { Screen } from '../schemas/screen.ts'
  import type { WireEvent } from '../schemas/events.ts'
  import AppBar from './AppBar.svelte'
  import BasicA2UIComponent from './items/BasicA2UIComponent.svelte'
  import CardSourceControls from './items/CardSourceControls.svelte'

  let screen: Screen | null = $state(null)
  let error = $state('')
  let states: Record<string, CardSourceEvent> = $state({})
  const components = $derived.by(() => screen ? asComponents(screen.message.component._components) : [])
  const sourceIds = $derived.by(() => Object.keys(screen?.sources || {}))
  let duringLoad: WireEvent[] | null = null
  function sourceEvent(event: WireEvent) {
    if (duringLoad) duringLoad.push(event)
    if (!screen) return
    states = { ...states, ...projectScreenEvent(screen, event) }
    screen = { ...screen }
  }
  $effect(() => {
    const path = $route.id
    const epoch = $profileEpoch
    let stale = false
    let loading = false
    screen = null; error = ''; states = {}
    if (!path) return
    const load = async () => {
      if (loading || stale) return
      const buffer: WireEvent[] = []
      loading = true; duringLoad = buffer
      try {
        const result = await api.screen(path)
        if (stale || epoch !== $profileEpoch) return
        for (const event of buffer) projectScreenEvent(result, event)
        screen = result; error = ''
      } catch (cause) { if (!stale) { error = errText(cause); screen = null } }
      finally { loading = false; if (duringLoad === buffer) duringLoad = null }
    }
    const stream = new StreamClient('', {
      onEvent: event => { if (!stale && epoch === $profileEpoch) sourceEvent(event) },
      onReady: () => { void load() },
    }, '/card-sources/stream').connect()
    void load()
    const timer = setInterval(() => { void load() }, 5000)
    return () => { stale = true; duringLoad = null; clearInterval(timer); stream.close() }
  })
</script>

<AppBar title={screen?.title || 'Screens'} />
<div class="thread">
  <div class="screen-actions">
    {#if $route.id}<button class="open" onclick={() => openAsideFile($route.id)}>Edit Screen</button>{/if}
  </div>
  {#if error}
    <div class="empty" role="alert"><h1>Screen could not be opened</h1><p>{error}</p><p>Repair its layout or referenced instance in Files.</p></div>
  {:else if screen}
    <div class="screen-content">
      <BasicA2UIComponent component={asComponent(screen.message.component)} {components} data={screen.message.data} passive {sourceIds} {sourceControls} />
    </div>
  {:else if !$route.id}
    <div class="empty"><h1>Screens</h1><p>Pick a Screen from the sidebar, or ask the assistant to create a dashboard.</p></div>
  {:else}<p role="status">Loading Screen…</p>{/if}
</div>

{#snippet sourceControls(id: string)}
  {@const target = screen!.sources[id]}
  <CardSourceControls source={CardSource.parse(target.source)} refreshState={states[id]}
    target={{ path: target.path, source_id: target.source_id }} onEvent={sourceEvent} />
{/snippet}

<style>
  .screen-content { width: 100%; max-width: 1280px; margin: 0 auto; padding: 16px 24px 48px; }
  .screen-actions { display: flex; justify-content: end; padding: 12px 24px 0; }
</style>
