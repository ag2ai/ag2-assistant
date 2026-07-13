<script>
  let { item } = $props()
</script>

<div class="msg user" class:queued={item.queued}>
  <div class="bubble" class:voice={item.voice}>{item.text}</div>
  {#if item.queued}
    <!-- Fed into the running turn. AG2 hands it to the agent when the turn drains its
         inbox — its next step, which can be a whole tool round away — so say so instead
         of letting it look unsent. Resolves to a normal bubble on the drain. -->
    <div class="hint">Queued · the agent will see this at its next step</div>
  {/if}
</div>
