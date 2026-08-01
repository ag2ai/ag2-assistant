<script>
  // The Model field: a text box that also offers real model names, each adorned with
  // what it costs and how much it holds. Modelled on the composer's `@` picker —
  // a role="listbox" popup, not a native datalist (rich rows do not render reliably)
  // and not a select-with-Other (an arbitrary name is the normal case here).
  //
  // Thin by design: every decision about which names to offer and in what order is
  // lib/modelSuggest.js. What the user typed always wins — a name off the list is
  // never blocked, never warned about and never replaced.
  import { suggestModels } from '../../lib/modelSuggest.js'

  let { value = $bindable(''), type, id = 'lf-model', placeholder = '' } = $props()

  let open = $state(false)
  let index = $state(0)
  let rows = $state([])

  const results = $derived(suggestModels({ type, query: value }))
  const optionId = (i) => `${id}-opt-${i}`

  // Reopening on every keystroke would make Escape useless, so the list opens on
  // focus and on a deliberate edit, and index resets whenever the results move.
  $effect(() => {
    results
    index = 0
  })

  function scrollTo(i) {
    rows[i]?.scrollIntoView({ block: 'nearest' })
  }

  function choose(row) {
    value = row.id
    open = false
  }

  function key(e) {
    if (e.key === 'Escape') {
      // Leaves the typed text exactly as it is — dismissing never loses work.
      if (open) { e.preventDefault(); e.stopPropagation(); open = false }
      return
    }
    if (e.key === 'ArrowDown' && !open) { e.preventDefault(); open = true; return }
    if (!open || !results.length) return
    if (e.key === 'ArrowDown') { e.preventDefault(); index = (index + 1) % results.length; scrollTo(index); return }
    if (e.key === 'ArrowUp') { e.preventDefault(); index = (index - 1 + results.length) % results.length; scrollTo(index); return }
    if (e.key === 'Home') { e.preventDefault(); index = 0; scrollTo(0); return }
    if (e.key === 'End') { e.preventDefault(); index = results.length - 1; scrollTo(index); return }
    if (e.key === 'Enter') { e.preventDefault(); choose(results[index]) }
  }
</script>

<div class="llmcombo">
  <input
    {id} {placeholder} bind:value spellcheck="false" autocomplete="off"
    role="combobox" aria-expanded={open && results.length > 0} aria-autocomplete="list"
    aria-controls={`${id}-list`} aria-activedescendant={open && results.length ? optionId(index) : undefined}
    onfocus={() => (open = true)}
    oninput={() => (open = true)}
    onblur={() => (open = false)}
    onkeydown={key}
  />
  {#if open && results.length}
    <!-- mousedown+preventDefault keeps the input focused so the pick lands before blur. -->
    <div class="llmcombolist" id={`${id}-list`} role="listbox" aria-label="Model suggestions">
      {#each results as r, i (r.id)}
        <button
          type="button" class="llmcomborow" class:sel={i === index} id={optionId(i)}
          role="option" aria-selected={i === index} bind:this={rows[i]}
          onmousedown={(e) => { e.preventDefault(); choose(r) }}
          onmouseenter={() => (index = i)}
        >
          <span class="llmcomboname">{r.label}</span>
          {#if r.label !== r.id}<span class="llmcomboid">{r.id}</span>{/if}
          <span class="llmcombometa">
            {#if r.unverified}<span class="llmcombotag">unverified</span>{/if}
            {#if r.price}<span>{r.price}</span>{/if}
            {#if r.context}<span>{r.context}</span>{/if}
          </span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  /* The popup floats over the form rather than growing it, so a long provider list
     scrolls in place. Positioned by the wrapper, which the input fills. */
  .llmcombo { position: relative; display: flex; flex-direction: column; }
  .llmcombolist {
    position: absolute; top: calc(100% + 4px); left: 0; right: 0;
    max-height: 260px; overflow-y: auto; z-index: 45;
    background: var(--surface-elevated); border: 1px solid var(--line);
    border-radius: var(--radius-sm); box-shadow: var(--shadow-lg); padding: 4px;
  }
  .llmcomborow {
    display: flex; align-items: baseline; gap: 8px; width: 100%; text-align: left;
    background: none; border: none; border-radius: var(--radius-sm);
    padding: 6px 8px; font: inherit; font-size: 13px; color: var(--ink); cursor: pointer;
  }
  .llmcomborow.sel { background: var(--surface-sunken, rgba(127, 127, 127, .14)); }
  .llmcomboname { flex: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 45%; }
  .llmcomboid { flex: none; font-size: 11px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .llmcombometa {
    flex: 1; min-width: 0; display: flex; justify-content: flex-end; gap: 8px;
    font-size: 11px; color: var(--text-muted); white-space: nowrap; overflow: hidden;
  }
  .llmcombotag { border: 1px solid var(--line); border-radius: 999px; padding: 0 5px; letter-spacing: .3px; }
</style>
