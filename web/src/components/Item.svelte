<script lang="ts">
  import UserMessage from './items/UserMessage.svelte'
  import AgentMessage from './items/AgentMessage.svelte'
  import Tools from './items/Tools.svelte'
  import TaskCard from './items/TaskCard.svelte'
  import Deliverable from './items/Deliverable.svelte'
  import Inquiry from './items/Inquiry.svelte'
  import Subagent from './items/Subagent.svelte'
  import Note from './items/Note.svelte'
  import GenImage from './items/GenImage.svelte'
  import Attachment from './items/Attachment.svelte'
  import A2UISurface from './items/A2UISurface.svelte'
  import { ag2View } from '../store.ts'
  import { itemAg2 } from '../lib/ag2map.ts'
  import type { ThreadItem } from '../schemas/index.ts'

  type Props = { item: ThreadItem }
  let { item }: Props = $props()
  // In AG2 view, caption each item with the AG2 primitive it's a projection of.
  const prov = $derived($ag2View ? itemAg2(item.kind) : null)
  // Held apart so the unrendered-kind branch can still name it: once the chain below
  // has covered every kind, `item` there has narrowed to never.
  const kind: string = $derived(item.kind)
</script>

<!-- ag2-rise: a subtle rise+fade entrance per item (runs once on mount; gated by
     prefers-reduced-motion in base.css). Streaming updates don't re-trigger it. -->
{#if !(item.kind === 'note' && item.a2uiActionPending)}
  <div class="ag2-rise">
    <!-- Dispatched by kind rather than through a component map: each renderer takes
         its own ThreadItem variant, and the branch is what narrows `item` to it. -->
    {#if item.kind === 'user'}<UserMessage {item} />
    {:else if item.kind === 'agent'}<AgentMessage {item} />
    {:else if item.kind === 'tools'}<Tools {item} />
    {:else if item.kind === 'taskcard'}<TaskCard {item} />
    {:else if item.kind === 'deliverable'}<Deliverable {item} />
    {:else if item.kind === 'inquiry'}<Inquiry {item} />
    {:else if item.kind === 'subagent'}<Subagent {item} />
    {:else if item.kind === 'note'}<Note {item} />
    {:else if item.kind === 'genimage'}<GenImage {item} />
    {:else if item.kind === 'attachment'}<Attachment {item} />
    {:else if item.kind === 'a2ui'}<A2UISurface {item} />
    {:else if import.meta.env.DEV}
      <!-- An item kind with no renderer is a bug, not a silent skip. -->
      <pre>unrendered item: {kind}</pre>
    {/if}
    {#if prov}
      <div class="ag2tag" class:right={item.kind === 'user'} class:applayer={prov.layer === 'app'}>
        AG2 · {prov.label()}
      </div>
    {/if}
  </div>
{/if}
