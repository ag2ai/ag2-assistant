// Lazy WebGPU weather-banner engine. The whole of three.js is pulled into a
// separate chunk that only loads when a WeatherPanel actually renders on a
// WebGPU-capable browser — the main app bundle stays three-free.
//
// Each scene module exports `build(ctx)` and returns a SceneHandle; the engine owns
// the renderer, the canvas-fit, and the animation loop.

import * as THREE from 'three/webgpu'
import { FontLoader, type Font } from 'three/addons/loaders/FontLoader.js'

// What a scene gets from the engine. `THREE` rides along so a scene could stay
// namespace-free, though all eight import it directly.
export type SceneContext = {
  THREE: typeof THREE
  renderer: THREE.WebGPURenderer
  canvas: HTMLCanvasElement
  font: Font
  temperatureText: string
}

// What a scene hands back. `render` replaces the plain renderer.render call (a
// post-process pipeline); `frame` advances the animation at the wall-clock second.
export type SceneHandle = {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  frame?: (t: number) => void
  render?: (t: number) => void
  dispose?: () => void
}

export type SceneModule = { build: (ctx: SceneContext) => SceneHandle | Promise<SceneHandle> }

export type BannerOptions = { temperatureText?: string; zoom?: number }

export type Banner = { dispose: () => void }

let _fontPromise: Promise<Font> | null = null
export function loadFont(): Promise<Font> {
  if (!_fontPromise) {
    const url = new URL('./helvetiker_bold.typeface.json', import.meta.url).href
    _fontPromise = new FontLoader().loadAsync(url)
  }
  return _fontPromise
}

// condition (from the WeatherPanel `condition` enum) -> scene module loader
const SCENES: Record<string, (() => Promise<SceneModule>) | undefined> = {
  sunny: () => import('./scenes/sunny.ts'),
  'partly-cloudy': () => import('./scenes/cloudy.ts'),
  cloudy: () => import('./scenes/cloudy.ts'),
  foggy: () => import('./scenes/foggy.ts'),
  rainy: () => import('./scenes/rainy.ts'),
  thunderstorm: () => import('./scenes/thunderstorm.ts'),
  snow: () => import('./scenes/snow.ts'),
  windy: () => import('./scenes/windy.ts'),
}

export function supportsWebGPU(): boolean {
  // `gpu` is not declared in lib.dom, so this is a key check, not a property read.
  return typeof navigator !== 'undefined' && 'gpu' in navigator && !!navigator.gpu
}

export function hasScene(condition: string): boolean {
  return Object.prototype.hasOwnProperty.call(SCENES, condition)
}

export async function createBanner(
  canvas: HTMLCanvasElement,
  condition: string,
  opts: BannerOptions = {},
): Promise<Banner> {
  if (!supportsWebGPU()) throw new Error('no-webgpu')
  const loader = SCENES[condition] || SCENES.cloudy
  if (!loader) throw new Error('no-scene')
  const mod = await loader()

  const renderer = new THREE.WebGPURenderer({ canvas, antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  await renderer.init()

  const font = await loadFont()
  const ctx: SceneContext = { THREE, renderer, canvas, font, temperatureText: opts.temperatureText || '' }
  const built = await mod.build(ctx)
  const camera = built.camera
  const renderFrame = built.render || (() => renderer.render(built.scene, camera))

  // Composition knob: telephoto magnification about the view centre (crops the
  // scene edges). Scenes read camera.zoom in their frame() to compute framing.
  const zoom = Number(opts.zoom) || 1
  if (zoom !== 1) {
    camera.zoom *= zoom
    camera.updateProjectionMatrix()
  }

  let disposed = false
  function fit() {
    const w = Math.max(1, Math.round(canvas.clientWidth || canvas.width || 1))
    const h = Math.max(1, Math.round(canvas.clientHeight || canvas.height || 1))
    const pr = renderer.getPixelRatio()
    if (canvas.width !== Math.round(w * pr) || canvas.height !== Math.round(h * pr)) {
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }
  }

  function loop(now: number) {
    if (disposed) return
    fit()
    const t = (now || 0) / 1000
    if (built.frame) built.frame(t)
    renderFrame(t)
  }

  renderer.setAnimationLoop(loop)

  return {
    dispose() {
      if (disposed) return
      disposed = true
      renderer.setAnimationLoop(null)
      try { built.dispose && built.dispose() } catch { /* ignore */ }
      try { renderer.dispose() } catch { /* ignore */ }
    },
  }
}
