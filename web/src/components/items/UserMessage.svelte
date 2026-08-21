<script lang="ts">
  import { parseMessage, highlightSegments } from '../../lib/fileRefs.ts'
  import type { ParsedRef } from '../../lib/fileRefs.ts'
  import { openAsideFile } from '../../router.ts'
  import { revealFolder } from '../../store.ts'
  import type { ThreadItem } from '../../schemas/events.ts'
  import { m } from '../../paraglide/messages.js'

  type Props = { item: Extract<ThreadItem, { kind: 'user' }> }
  let { item }: Props = $props()

  // Split off the `Referenced files:` block (ADR 0012) so the bubble reads naturally
  // with just the sentence, then highlight the surviving `@labels` — the same cosmetic
  // mention treatment the composer shows. Hovering a mention reveals its resolved path.
  const parsed = $derived(parseMessage(item.text || ''))
  const segs = $derived(highlightSegments(parsed.body, parsed.refs))
  // Keep each label's resolved refs (path + kind), so clicking a chip can open a file
  // in the preview rail but browse a directory in the Files tree — a folder has no
  // preview (ADR 0012). Same-named refs share a label; the first drives the click.
  const refsFor = $derived.by(() => {
    const byLabel = new Map<string, ParsedRef[]>()
    for (const r of parsed.refs) byLabel.set(r.label, [...(byLabel.get(r.label) || []), r])
    return byLabel
  })
  function openRef(ref: ParsedRef | undefined) {
    if (!ref) return
    if (ref.kind === 'directory') revealFolder(ref.path)
    else openAsideFile(ref.path)
  }
</script>

<div class="msg user" class:queued={item.queued}>
  <div class="bubble" class:voice={item.voice}>{#each segs as s}{#if s.mark}{@const refs = refsFor.get(s.text) || []}<button type="button" class="fileref" title={refs.map((r) => r.path).join('\n')} onclick={() => openRef(refs[0])}>{s.text}</button>{:else}{s.text}{/if}{/each}</div>
  {#if item.queued}
    <!-- Fed into the running turn. AG2 hands it to the agent when the turn drains its
         inbox — its next step, which can be a whole tool round away — so say so instead
         of letting it look unsent. Resolves to a normal bubble on the drain. -->
    <div class="hint">{m.thread_queued_hint()}</div>
  {/if}
</div>

<style>
  /* File-reference mention — matches the composer's accent pill so a sent `@label`
     reads the same in the transcript as it did while typing. It's a button: clicking a
     file opens it in the preview rail; clicking a directory browses it in the Files tree
     (ADR 0012). Reset the native button chrome so it stays inline with the sentence and
     inherits the bubble's type. */
  .fileref {
    font: inherit; border: 0; cursor: pointer;
    background: var(--accent-soft); color: var(--accent);
    border-radius: 4px; padding: 0 2px;
    -webkit-box-decoration-break: clone; box-decoration-break: clone;
  }
  .fileref:hover { text-decoration: underline; }
</style>
