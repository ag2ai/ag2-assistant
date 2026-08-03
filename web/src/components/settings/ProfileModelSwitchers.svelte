<script lang="ts">
  // Profile editor → Model tab (ADR 0015, redesign §5): the per-profile model switchers —
  // Text + Voice — that set this profile's Active override (which shared model is Active
  // *for it*). Stacked as two labelled rows so each model is its own clear choice; Text
  // reuses the composer switcher's look, Voice is the parallel over the shared live-config
  // store.
  import ProfileTextSwitcher from './ProfileTextSwitcher.svelte'
  import ProfileLiveSwitcher from './ProfileLiveSwitcher.svelte'
</script>

<div class="pmodels">
  <div class="pmodel">
    <div class="pmodel-k">Text model</div>
    <div class="pmodel-sub">Drives chat replies for this profile.</div>
    <ProfileTextSwitcher />
  </div>
  <div class="pmodel">
    <div class="pmodel-k">Voice model</div>
    <div class="pmodel-sub">Used when you talk to this profile out loud.</div>
    <ProfileLiveSwitcher />
  </div>
</div>

<style>
  .pmodels { display: flex; flex-direction: column; gap: 20px; }
  .pmodel { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
  .pmodel-k { font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-eyebrow); color: var(--text-muted); font-weight: var(--fw-semibold); }
  .pmodel-sub { font-size: var(--text-xs); color: var(--text-muted); margin-top: -3px; margin-bottom: 2px; }
</style>
