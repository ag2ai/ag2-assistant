<script>
  let { item } = $props()
</script>

<div class="tools">
  {#each item.names as t}
    <span class="chip">⚙ {t.name}{t.n > 1 ? ' ×' + t.n : ''}</span>
  {/each}
</div>
