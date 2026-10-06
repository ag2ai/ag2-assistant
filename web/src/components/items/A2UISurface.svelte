<script lang="ts">
  import { untrack } from 'svelte'
  import { cardSources, projectSourceEvent } from '../../lib/cardSources.ts'
  import CardSourceControls from './CardSourceControls.svelte'
  import type { WireEvent } from '../../schemas/events.ts'
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import A2UIComposing from './A2UIComposing.svelte'
  import CardDraftSave from './CardDraftSave.svelte'
  import CardInstanceSave from './CardInstanceSave.svelte'
  import { currentDraft } from '../../lib/cardDrafts.ts'
  import { a2uiComposingSurfaceId, str, withA2UIValue, SURFACE_TITLE } from '../../lib/a2ui.ts'
  import type { A2UIAction, A2UIData } from '../../lib/a2ui.ts'
  import { a2uiAction } from '../../controller.ts'
  import { thread } from '../../store.ts'
  import type { ThreadItem } from '../../schemas/events.ts'

  type Props = { item: Extract<ThreadItem, { kind: 'a2ui' }>; passive?: boolean; filePath?: string }
  let { item, passive = false, filePath = '' }: Props = $props()
  const sources = $derived(cardSources(item.data))
  const sourceIds = $derived(Object.keys(sources))
  const data = $derived(item.data || {})
  const components = $derived(item.components || item.component._components || [item.component])
  const rootKind = $derived(str(item.component.component).toLowerCase())
  // A surface with no layout has nothing to draw — a record that carried data alone.
  const hasLayout = $derived(!!rootKind)
  // A feature Card draws its own frame and heading; the generic chrome is skipped.
  const isFeature = $derived(rootKind === 'card' && str(item.component.variant) === 'feature')
  const title = $derived(item.title || SURFACE_TITLE)
  const isComposingUpdate = $derived(!passive && $thread.items.some(
    (entry) => entry.kind === 'agent' && entry.streaming && a2uiComposingSurfaceId(entry.text) === item.surfaceId
  ))
  const actionPending = $derived(!passive && $thread.items.some(
    (entry) => entry.kind === 'note' && entry.a2uiActionPending && entry.surfaceId === item.surfaceId
  ))
  let edits: Record<string, unknown> = {}
  const seenSourceUpdates = new Map<string, number | undefined>()
  let inputData: A2UIData = $state({})

  $effect(() => {
    const latestData = data
    const states = item.sourceStates || {}
    for (const [id, state] of Object.entries(states)) {
      if (state.status === 'updated' && (!seenSourceUpdates.has(id) || seenSourceUpdates.get(id) !== state.created_at)) {
        seenSourceUpdates.set(id, state.created_at)
        const source = sources[id]
        if (source) for (const path of Object.keys(edits)) {
          const prefix = source.path || ''
          if (Object.keys(source.fields).some(name => path === prefix + '/' + name.replace(/~/g, '~0').replace(/\//g, '~1') || path.startsWith(prefix + '/' + name.replace(/~/g, '~0').replace(/\//g, '~1') + '/'))) delete edits[path]
        }
      }
    }
    inputData = untrack(() => Object.entries(edits).reduce((values, [path, value]) => withA2UIValue(values, path, value), latestData))
  })

  function setInputValue(path: string, value: unknown) {
    edits[path] = value
    inputData = withA2UIValue(inputData, path, value)
  }

  // The click carries the data model this instance holds alongside the envelope.
  function submitAction(action: A2UIAction) {
    if (passive) return
    a2uiAction(
      {
        version: item.version || 'v1.0',
        action: { ...action, surfaceId: item.surfaceId, timestamp: new Date().toISOString() },
      },
      { surfaceId: item.surfaceId, data: inputData },
    )
  }
  function sourceEvent(event: WireEvent) {
    if (filePath) {
      if (projectSourceEvent(item, event, filePath)) item = { ...item }
    } else {
      thread.update(current => {
        const items = current.items.map(entry => {
          if (entry.kind !== 'a2ui') return entry
          const updated = { ...entry }
          return projectSourceEvent(updated, event) ? updated : entry
        })
        return { ...current, items }
      })
    }
  }
</script>

{#snippet sourceControls(id: string)}
  <CardSourceControls source={sources[id]} refreshState={item.sourceStates?.[id]} target={filePath ? { path: filePath, source_id: id } : { chat_id: $thread.chat, surface_id: item.surfaceId, source_id: id }} onEvent={sourceEvent} />
{/snippet}

{#if isComposingUpdate}
  <A2UIComposing />
{:else if isFeature}
  <BasicA2UIComponent {sourceIds} {sourceControls} component={item.component} {components} data={inputData} {passive} onDataChange={setInputValue} onAction={submitAction} />
{:else if hasLayout}
<div class="a2ui">
  <div class="a2ui-head">
    <span class="a2ui-mark"><Icon name="sparkles" size={15} /></span>
    <span class="a2ui-headtext">
      <span class="a2ui-eyebrow">Overview</span>
      <span class="a2ui-title">{title}</span>
    </span>
    <span class="a2ui-catalog" title={item.catalogId}>AG2 catalog</span>
  </div>

  <BasicA2UIComponent {sourceIds} {sourceControls} component={item.component} {components} data={inputData} {passive} onDataChange={setInputValue} onAction={submitAction} />
</div>
{/if}
{#if !passive && !isComposingUpdate && hasLayout}
  <CardInstanceSave {item} localData={() => inputData} />
{/if}
{#if !passive && currentDraft($thread.items, item)}
  <CardDraftSave {item} />
{/if}
{#if actionPending}
  <div class="a2ui-action-pending" role="status" aria-label="Submitting action"><Icon name="rotate-cw" size={14} /></div>
{/if}
