<script lang="ts">
  import Icon from '../Icon.svelte'

  let { text, language, count }: { text: string; language?: string; count: number } = $props()
  let expanded = $state(false)
  const id = $props.id()
</script>

<div class="execution-entry">
  <div class="execution-heading">
    {#if language}<span class="execution-language">{language}</span>{/if}
    <pre class="execution-preview">{text.split(/\r?\n/, 1)[0]}</pre>
    {#if count > 1}<span class="run-count">×{count}</span>{/if}
    <button type="button" class="detail-toggle" aria-expanded={expanded} aria-controls={id}
      aria-label={expanded ? 'Hide details' : 'Show details'} title={expanded ? 'Hide details' : 'Show details'}
      onclick={() => expanded = !expanded}><Icon name="chevron-down" size={14} /></button>
  </div>
  {#if expanded}<pre {id} class="execution-source" role="region" aria-label="Execution details">{text}</pre>{/if}
</div>

<style>
  .execution-entry { flex: 1; min-width: 0; }
  .execution-heading { display: flex; align-items: center; gap: 6px; }
  .execution-language, .run-count { flex: none; color: var(--muted); font-size: 10px; white-space: nowrap; }
  pre { margin: 0; font-family: var(--mono, ui-monospace, monospace); font-size: 11px; line-height: 1.5; white-space: pre-wrap; overflow-wrap: anywhere; }
  .execution-preview { flex: 1; min-width: 0; white-space: pre; text-overflow: ellipsis; overflow: hidden; }
  .detail-toggle { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 26px; height: 26px; padding: 0; background: var(--bg); border: 1px solid var(--line); border-radius: 6px; color: var(--muted); cursor: pointer; }
  .detail-toggle:hover { color: var(--ink); border-color: var(--line-strong); }
  .detail-toggle[aria-expanded="true"] :global(svg) { transform: rotate(180deg); }
  .detail-toggle:focus-visible, .execution-source:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .execution-source { margin-top: 8px; padding: 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); max-height: 200px; overflow: auto; overscroll-behavior: contain; }
</style>
