<script lang="ts">
  // "Basic" weather glyphs — the middle tier of the app-wide `animations` setting. Simple
  // flat vector scenes with compositor-cheap CSS animation (transform/opacity
  // only, no canvas, no WebGPU). Same composition as the 3D scenes: the glyph
  // rides the right of the pill and is cropped by it; the HTML temperature
  // (rendered by WeatherBanner) holds the left.
  type Props = { condition?: string }
  let { condition = 'cloudy' }: Props = $props()
</script>

<div class="g g-{condition}" aria-hidden="true">
  {#if condition === 'sunny'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <circle class="sun" cx="78" cy="26" r="52" fill="#ffc531" />
    </svg>
  {:else if condition === 'partly-cloudy'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <circle class="sun" cx="72" cy="30" r="34" fill="#ffc531" />
      <g class="cloud">
        <ellipse cx="46" cy="66" rx="26" ry="15" fill="#eef2f5" />
        <ellipse cx="66" cy="62" rx="20" ry="12" fill="#e2e8ee" />
        <ellipse cx="30" cy="70" rx="16" ry="10" fill="#f4f7f9" />
      </g>
    </svg>
  {:else if condition === 'cloudy'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <g class="cloud">
        <ellipse cx="52" cy="52" rx="34" ry="19" fill="#eef2f5" />
        <ellipse cx="78" cy="46" rx="24" ry="15" fill="#dfe6ec" />
        <ellipse cx="28" cy="58" rx="20" ry="12" fill="#f4f7f9" />
        <ellipse cx="64" cy="62" rx="26" ry="13" fill="#e6ebf0" />
      </g>
    </svg>
  {:else if condition === 'foggy'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <rect class="fog f1" x="-10" y="26" width="120" height="9" rx="4.5" fill="#c9ced1" />
      <rect class="fog f2" x="-10" y="44" width="120" height="9" rx="4.5" fill="#bdc4c7" />
      <rect class="fog f3" x="-10" y="62" width="120" height="9" rx="4.5" fill="#ced4d6" />
    </svg>
  {:else if condition === 'rainy' || condition === 'thunderstorm'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <!-- two identical line fields stacked one period apart; translating by one
           period and snapping back loops seamlessly -->
      <g class="rainrot">
        <g class="rainfall">
          {#each [0, 1] as copy}
            <g transform="translate(0 {copy * -60})">
              {#each [4, 18, 32, 46, 60, 74, 88] as x, i}
                <line x1={x} y1={(i % 3) * 8} x2={x} y2={(i % 3) * 8 + 14} stroke={condition === 'thunderstorm' ? '#9fb1c8' : '#a9bdd6'} stroke-width="2.6" stroke-linecap="round" />
                <line x1={x + 7} y1={(i % 3) * 8 + 30} x2={x + 7} y2={(i % 3) * 8 + 44} stroke={condition === 'thunderstorm' ? '#9fb1c8' : '#a9bdd6'} stroke-width="2.6" stroke-linecap="round" />
              {/each}
            </g>
          {/each}
        </g>
      </g>
      {#if condition === 'thunderstorm'}
        <polygon class="bolt" points="56,8 38,52 52,52 40,88 68,42 53,42 66,8" fill="#ffd94d" />
      {/if}
    </svg>
  {:else if condition === 'snow'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <g class="snowfall">
        {#each [0, 1] as copy}
          <g transform="translate(0 {copy * -100})">
            {#each [[8, 10, 3], [30, 34, 2.4], [52, 6, 3.4], [74, 46, 2.2], [92, 22, 3], [18, 66, 2.6], [44, 82, 3.2], [66, 70, 2.4], [86, 88, 2.8], [58, 40, 2]] as [x, y, r]}
              <circle cx={x} cy={y} r={r} fill="#fbfdff" />
            {/each}
          </g>
        {/each}
      </g>
    </svg>
  {:else if condition === 'windy'}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <!-- two copies side by side; translating one period left and snapping back
           loops the stream in ONE direction -->
      <g class="windflow w1"><g>{#each [0, 1] as c}<line x1={c * 120} y1="30" x2={c * 120 + 46} y2="30" stroke="#f2f7fb" stroke-width="3.4" stroke-linecap="round" /><line x1={c * 120 + 66} y1="30" x2={c * 120 + 96} y2="30" stroke="#f2f7fb" stroke-width="3.4" stroke-linecap="round" />{/each}</g></g>
      <g class="windflow w2"><g>{#each [0, 1] as c}<line x1={c * 120 + 20} y1="52" x2={c * 120 + 84} y2="52" stroke="#e6eff6" stroke-width="3.4" stroke-linecap="round" />{/each}</g></g>
      <g class="windflow w3"><g>{#each [0, 1] as c}<line x1={c * 120 + 8} y1="72" x2={c * 120 + 52} y2="72" stroke="#eef4f9" stroke-width="3.4" stroke-linecap="round" /><line x1={c * 120 + 70} y1="72" x2={c * 120 + 104} y2="72" stroke="#eef4f9" stroke-width="3.4" stroke-linecap="round" />{/each}</g></g>
    </svg>
  {:else}
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
      <g class="cloud"><ellipse cx="55" cy="52" rx="34" ry="19" fill="#eef2f5" /></g>
    </svg>
  {/if}
</div>

<style>
  /* the glyph occupies the right side; field scenes (rain/snow/wind/fog) sit
     near panel height, blob scenes (sun/cloud) run oversized so the pill crops
     them like the 3D tiers */
  .g { position: absolute; top: 50%; right: 2%; height: 112%; aspect-ratio: 1; transform: translateY(-50%); }
  .g-sunny, .g-partly-cloudy, .g-cloudy { right: -8%; height: 185%; aspect-ratio: 1; }
  .g svg { width: 100%; height: 100%; overflow: visible; }

  .sun { animation: pulse 6s ease-in-out infinite; transform-origin: 78% 26%; transform-box: view-box; }
  @keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.045); } }

  .cloud { animation: drift 14s ease-in-out infinite; }
  @keyframes drift { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(3.5%); } }

  .fog { animation: breathe 7s ease-in-out infinite; }
  .fog.f2 { animation-delay: -2.3s; }
  .fog.f3 { animation-delay: -4.6s; }
  @keyframes breathe { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }

  /* rain: lean the field, then loop it downward by exactly one period */
  .rainrot { transform: rotate(14deg); transform-origin: 50% 50%; transform-box: view-box; }
  .rainfall { animation: fall 1.1s linear infinite; }
  @keyframes fall { from { transform: translateY(0); } to { transform: translateY(60%); } }

  .bolt { animation: flash 4.2s linear infinite; transform-origin: 50% 50%; }
  @keyframes flash {
    0%, 86%, 96%, 100% { opacity: 0; }
    88%, 94% { opacity: 1; }
    91% { opacity: 0.25; }
  }

  .snowfall { animation: snowfall 9s linear infinite; }
  @keyframes snowfall { from { transform: translateY(0); } to { transform: translateY(100%); } }

  .windflow { animation: stream 2.6s linear infinite; }
  .windflow.w2 { animation-duration: 3.4s; animation-delay: -1.2s; }
  .windflow.w3 { animation-duration: 2.1s; animation-delay: -0.6s; }
  @keyframes stream { from { transform: translateX(-120%); } to { transform: translateX(0); } }
</style>
