<script>
  import UserMessage from './items/UserMessage.svelte'
  import AgentMessage from './items/AgentMessage.svelte'
  import Tools from './items/Tools.svelte'
  import TaskCard from './items/TaskCard.svelte'
  import Deliverable from './items/Deliverable.svelte'
  import Inquiry from './items/Inquiry.svelte'
  import Note from './items/Note.svelte'

  let { item } = $props()
  const map = {
    user: UserMessage, agent: AgentMessage, tools: Tools,
    taskcard: TaskCard, deliverable: Deliverable, inquiry: Inquiry, note: Note,
  }
  const Cmp = $derived(map[item.kind])
</script>

{#if Cmp}<Cmp {item} />{/if}
