<script lang="ts">
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import A2UIComposing from './A2UIComposing.svelte'
  import { a2uiComposingSurfaceId, str, withA2UIValue, SURFACE_TITLE } from '../../lib/a2ui.ts'
  import type { A2UIAction, A2UIData } from '../../lib/a2ui.ts'
  import { a2uiAction } from '../../controller.ts'
  import { thread } from '../../store.ts'
  import type { ThreadItem } from '../../schemas/events.ts'

  type Props = { item: Extract<ThreadItem, { kind: 'a2ui' }> }
  let { item }: Props = $props()
  const data = $derived(item.data || {})
  const components = $derived(item.components || item.component._components || [item.component])
  const rootKind = $derived(str(item.component.component).toLowerCase())
  // A surface with no layout has nothing to draw — a record that carried data alone.
  const hasLayout = $derived(!!rootKind)
  // A feature Card draws its own frame and heading; the generic chrome is skipped.
  const isFeature = $derived(rootKind === 'card' && str(item.component.variant) === 'feature')
  const title = $derived(item.title || SURFACE_TITLE)
  const isComposingUpdate = $derived($thread.items.some(
    (entry) => entry.kind === 'agent' && entry.streaming && a2uiComposingSurfaceId(entry.text) === item.surfaceId
  ))
  const actionPending = $derived($thread.items.some(
    (entry) => entry.kind === 'note' && entry.a2uiActionPending && entry.surfaceId === item.surfaceId
  ))
  let inputData: A2UIData = $state({})

  $effect(() => {
    inputData = data
  })

  function setInputValue(path: string, value: unknown) {
    inputData = withA2UIValue(inputData, path, value)
  }

  function submitAction(action: A2UIAction) {
    a2uiAction({
      version: item.version || 'v1.0',
      action: { ...action, surfaceId: item.surfaceId, timestamp: new Date().toISOString() },
    })
  }
</script>

{#if isComposingUpdate}
  <A2UIComposing />
{:else if isFeature}
  <BasicA2UIComponent component={item.component} {components} data={inputData} onDataChange={setInputValue} onAction={submitAction} />
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

  <BasicA2UIComponent component={item.component} {components} data={inputData} onDataChange={setInputValue} onAction={submitAction} />
</div>
{/if}
{#if actionPending}
  <div class="a2ui-action-pending" role="status" aria-label="Submitting action"><Icon name="rotate-cw" size={14} /></div>
{/if}
