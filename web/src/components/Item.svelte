<script>
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
  import { ag2View } from '../store.js'
  import { itemAg2 } from '../lib/ag2map.js'

  let { item } = $props()
  const map = {
    user: UserMessage, agent: AgentMessage, tools: Tools,
    taskcard: TaskCard, deliverable: Deliverable, inquiry: Inquiry,
    subagent: Subagent, note: Note, genimage: GenImage, attachment: Attachment,
    a2ui: A2UISurface,
  }
  const Cmp = $derived(map[item.kind])
  // In AG2 view, caption each item with the AG2 primitive it's a projection of.
  const prov = $derived($ag2View ? itemAg2(item.kind) : null)
</script>

<!-- ag2-rise: a subtle rise+fade entrance per item (runs once on mount; gated by
     prefers-reduced-motion in base.css). Streaming updates don't re-trigger it. -->
<div class="ag2-rise">
  {#if Cmp}<Cmp {item} />{/if}
  {#if prov}
    <div class="ag2tag" class:right={item.kind === 'user'} class:applayer={prov.layer === 'app'}>
      AG2 · {prov.label}
    </div>
  {/if}
</div>
