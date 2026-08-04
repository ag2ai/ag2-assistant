// Lazy WebGPU weather-banner engine. The whole of three.js is pulled into a
// separate chunk that only loads when a WeatherPanel actually renders on a
// WebGPU-capable browser — the main app bundle stays three-free.
//
// Each scene module exports `build(ctx)` and returns a handle:
//   { scene, camera, frame(t), render?(t), dispose?() }
// The engine owns the renderer, the canvas-fit, and the animation loop.

import * as THREE from 'three/webgpu'
import { FontLoader } from 'three/addons/loaders/FontLoader.js'

let _fontPromise = null
export function loadFont() {
  if (!_fontPromise) {
    const url = new URL('./helvetiker_bold.typeface.json', import.meta.url).href
    _fontPromise = new FontLoader().loadAsync(url)
  }
  return _fontPromise
}

// condition (from the WeatherPanel `condition` enum) -> scene module loader
const SCENES = {
  sunny: () => import('./scenes/sunny.js'),
  'partly-cloudy': () => import('./scenes/cloudy.js'),
  cloudy: () => import('./scenes/cloudy.js'),
  foggy: () => import('./scenes/foggy.js'),
  rainy: () => import('./scenes/rainy.js'),
  thunderstorm: () => import('./scenes/thunderstorm.js'),
  snow: () => import('./scenes/snow.js'),
  windy: () => import('./scenes/windy.js'),
}

export function supportsWebGPU() {
  return typeof navigator !== 'undefined' && !!navigator.gpu
}

export function hasScene(condition) {
  return Object.prototype.hasOwnProperty.call(SCENES, condition)
}

export async function createBanner(canvas, condition, opts = {}) {
  if (!supportsWebGPU()) throw new Error('no-webgpu')
  const loader = SCENES[condition] || SCENES.cloudy
  const mod = await loader()

  const renderer = new THREE.WebGPURenderer({ canvas, antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  await renderer.init()

  const font = await loadFont()
  const ctx = { THREE, renderer, canvas, font, temperatureText: opts.temperatureText || '' }
  const built = await mod.build(ctx)
  const camera = built.camera
  const renderFrame = built.render || (() => renderer.render(built.scene, camera))

  // Composition knob: telephoto magnification about the view centre (crops the
  // scene edges). Scenes read camera.zoom in their frame() to compute framing.
  const zoom = Number(opts.zoom) || 1
  if (zoom !== 1 && camera.isPerspectiveCamera) {
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
      if (camera.isPerspectiveCamera) {
        camera.aspect = w / h
        camera.updateProjectionMatrix()
      }
    }
  }

  function loop(now) {
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
      try { built.dispose && built.dispose() } catch (e) { /* ignore */ }
      try { renderer.dispose && renderer.dispose() } catch (e) { /* ignore */ }
    },
  }
}
