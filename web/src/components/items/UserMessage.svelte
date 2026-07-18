<script>
  import { parseMessage, highlightSegments } from '../../lib/fileRefs.js'

  let { item } = $props()

  // Split off the `Referenced files:` block (ADR 0012) so the bubble reads naturally
  // with just the sentence, then highlight the surviving `@labels` — the same cosmetic
  // mention treatment the composer shows. Hovering a mention reveals its resolved path.
  const parsed = $derived(parseMessage(item.text || ''))
  const segs = $derived(highlightSegments(parsed.body, parsed.refs))
  const pathFor = $derived.by(() => {
    const m = new Map()
    for (const r of parsed.refs) m.set(r.label, [...(m.get(r.label) || []), r.path])
    return m
  })
</script>

<div class="msg user" class:queued={item.queued}>
  <div class="bubble" class:voice={item.voice}>{#each segs as s}{#if s.mark}<mark class="fileref" title={pathFor.get(s.text)?.join('\n')}>{s.text}</mark>{:else}{s.text}{/if}{/each}</div>
  {#if item.queued}
    <!-- Fed into the running turn. AG2 hands it to the agent when the turn drains its
         inbox — its next step, which can be a whole tool round away — so say so instead
         of letting it look unsent. Resolves to a normal bubble on the drain. -->
    <div class="hint">Queued · the agent will see this at its next step</div>
  {/if}
</div>

<style>
  /* Cosmetic File-reference mention — matches the composer's accent pill so a sent
     `@label` reads the same in the transcript as it did while typing. */
  .fileref {
    background: var(--accent-soft); color: var(--accent);
    border-radius: 4px; padding: 0 2px;
    -webkit-box-decoration-break: clone; box-decoration-break: clone;
  }
</style>
