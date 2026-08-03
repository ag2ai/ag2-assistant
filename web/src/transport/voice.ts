// Full-duplex voice transport (Gemini Live over /api/p/{pid}/voice): captures
// mic at 16 kHz mono PCM via an AudioWorklet, streams it as binary, plays back
// 24 kHz PCM, and surfaces JSON frames (ready / transcript / turn_end / error)
// to the caller. Audio + WS only — rendering is the controller's job.

import { api as P } from '../lib/profile.ts'
import { VoiceFrame, type WireEvent } from '../schemas/events.ts'

const WORKLET = `
class PCM16 extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0][0]
    if (ch) {
      const pcm = new Int16Array(ch.length)
      for (let i = 0; i < ch.length; i++) { let s = Math.max(-1, Math.min(1, ch[i])); pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff }
      this.port.postMessage(pcm.buffer, [pcm.buffer])
    }
    return true
  }
}
registerProcessor('pcm16', PCM16)`

export type VoiceState = 'off' | 'connecting' | 'listening' | 'error'

export type VoiceHandlers = {
  onState?: (state: VoiceState, text: string) => void
  onTranscript?: (role: 'user' | 'agent', text: string, final: boolean) => void
  onTurnEnd?: (role?: string) => void
  onEvent?: (event: WireEvent) => void
  onAudio?: () => void
}

// Safari only exposes the prefixed constructor.
type AudioContextWindow = Window & { webkitAudioContext?: typeof AudioContext }

export class VoiceController {
  private readonly query: string
  private readonly h: VoiceHandlers
  private readonly inputRate: number
  private ws: WebSocket | null
  private micCtx: AudioContext | null
  private micNode: AudioWorkletNode | null
  private micStream: MediaStream | null
  private playCtx: AudioContext | null
  private playHead: number

  constructor(query: string, handlers: VoiceHandlers = {}, inputRate = 16000) {
    this.query = query // "?task=<id>" or "?chat=<id>"
    this.h = handlers  // {onState, onTranscript, onTurnEnd, onEvent, onAudio}
    this.inputRate = inputRate  // mic capture rate the active provider expects (Gemini 16k / OpenAI 24k)
    this.ws = null; this.micCtx = null; this.micNode = null; this.micStream = null
    this.playCtx = null; this.playHead = 0
  }

  async start(): Promise<boolean> {
    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
    } catch {
      this.h.onState && this.h.onState('error', 'Mic permission denied')
      return false
    }
    this.h.onState && this.h.onState('connecting', 'Connecting…')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}${P('/voice' + this.query)}`)
    this.ws.binaryType = 'arraybuffer'
    this.ws.onmessage = (ev: MessageEvent) => this._onMessage(ev)
    this.ws.onclose = () => this.stop(false)
    this.ws.onerror = () => this.h.onState && this.h.onState('error', 'Voice connection error')

    const AudioCtx = window.AudioContext || (window as AudioContextWindow).webkitAudioContext
    if (!AudioCtx) {
      this.h.onState && this.h.onState('error', 'Audio is unavailable in this browser')
      return false
    }

    // Capture context at the provider's input rate + inline AudioWorklet shipping Int16 frames.
    this.micCtx = new AudioCtx({ sampleRate: this.inputRate })
    await this.micCtx.audioWorklet.addModule(
      URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }))
    )
    const src = this.micCtx.createMediaStreamSource(this.micStream)
    this.micNode = new AudioWorkletNode(this.micCtx, 'pcm16')
    this.micNode.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(e.data)
    }
    src.connect(this.micNode)

    // 24 kHz playback context (Gemini output rate).
    this.playCtx = new AudioCtx({ sampleRate: 24000 })
    this.playHead = 0
    return true
  }

  private _onMessage(ev: MessageEvent): void {
    if (typeof ev.data === 'string') {
      let value: unknown
      try { value = JSON.parse(ev.data) } catch { return }
      const parsed = VoiceFrame.safeParse(value)
      if (!parsed.success) return
      const m = parsed.data
      // Structured AG2 events ride the same {event:{type,data}} shape as the text
      // stream, folded by the one shared reducer (tool chips/cards, task cards, …).
      if ('event' in m) { this.h.onEvent && this.h.onEvent(m.event); return }
      switch (m.type) {
        case 'ready': this.h.onState && this.h.onState('listening', 'Listening…'); break
        case 'transcript': this.h.onTranscript && this.h.onTranscript(m.role, m.text, !!m.final); break
        case 'turn_end': this.h.onTurnEnd && this.h.onTurnEnd(m.role); break
        case 'error': this.h.onState && this.h.onState('error', 'Voice error: ' + (m.message || '')); break
      }
      return
    }
    this.h.onAudio && this.h.onAudio()          // agent is replying
    this._play(new Int16Array(ev.data))
  }

  private _play(int16: Int16Array): void {
    if (!this.playCtx) return
    const f32 = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 0x8000
    const buf = this.playCtx.createBuffer(1, f32.length, 24000)
    buf.getChannelData(0).set(f32)
    const node = this.playCtx.createBufferSource(); node.buffer = buf; node.connect(this.playCtx.destination)
    const now = this.playCtx.currentTime
    if (this.playHead < now) this.playHead = now
    node.start(this.playHead); this.playHead += buf.duration   // queue back-to-back
  }

  stop(closeWS = true): void {
    if (this.micNode) { try { this.micNode.port.onmessage = null; this.micNode.disconnect() } catch {} this.micNode = null }
    if (this.micStream) { this.micStream.getTracks().forEach((t) => t.stop()); this.micStream = null }
    if (this.micCtx) { this.micCtx.close().catch(() => {}); this.micCtx = null }
    if (this.playCtx) { this.playCtx.close().catch(() => {}); this.playCtx = null }
    if (closeWS && this.ws) { try { this.ws.close() } catch {} }
    this.ws = null
    this.h.onState && this.h.onState('off', 'off')
  }
}
