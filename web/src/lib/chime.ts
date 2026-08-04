// A short, pleasant two-note chime via the Web Audio API — no audio asset to ship.
// Used to alert the user when the assistant needs their input (HITL).
let ctx: AudioContext | undefined

// Safari still ships the prefixed constructor only.
const AudioCtx = (): typeof AudioContext | undefined =>
  window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext

export function chime(): void {
  try {
    const Ctor = AudioCtx()
    if (!Ctor) return
    const audio = (ctx = ctx || new Ctor())
    if (audio.state === 'suspended') audio.resume()
    const now = audio.currentTime
    ;[880, 1175].forEach((freq, i) => {        // A5 → D6, a gentle rising ding-dong
      const osc = audio.createOscillator()
      const gain = audio.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      osc.connect(gain); gain.connect(audio.destination)
      const t = now + i * 0.16
      gain.gain.setValueAtTime(0, t)
      gain.gain.linearRampToValueAtTime(0.18, t + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22)
      osc.start(t); osc.stop(t + 0.24)
    })
  } catch { /* autoplay blocked / no audio — fail silently */ }
}
