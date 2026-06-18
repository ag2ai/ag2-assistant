// A short, pleasant two-note chime via the Web Audio API — no audio asset to ship.
// Used to alert the user when the assistant needs their input (HITL).
let ctx
export function chime() {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)()
    if (ctx.state === 'suspended') ctx.resume()
    const now = ctx.currentTime
    ;[880, 1175].forEach((freq, i) => {        // A5 → D6, a gentle rising ding-dong
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      osc.connect(gain); gain.connect(ctx.destination)
      const t = now + i * 0.16
      gain.gain.setValueAtTime(0, t)
      gain.gain.linearRampToValueAtTime(0.18, t + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22)
      osc.start(t); osc.stop(t + 0.24)
    })
  } catch { /* autoplay blocked / no audio — fail silently */ }
}
