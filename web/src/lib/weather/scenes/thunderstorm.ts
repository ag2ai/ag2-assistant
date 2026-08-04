// Thunderstorm banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, render, dispose }.
// Heavy GPU-instanced rain under a dark churning storm ceiling. Lightning forks a
// glowing bolt and flashes the whole scene through a bloom pass, lighting the
// clouds, puddle and the wet temperature (which the rain also strikes + ripples).
// Default preset baked in = OVERHEAD.

import * as THREE from 'three/webgpu'
import {
  Fn, Loop, vec2, vec3, float, uniform, time,
  positionLocal, normalView, uv, instanceIndex,
  max, mix, smoothstep, sin, fract, step, exp,
  mx_fractal_noise_float, pass,
} from 'three/tsl'
import { bloom } from 'three/addons/tsl/display/BloomNode.js'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'
import type { SceneContext, SceneHandle } from '../engine.ts'

// A scalar TSL node — the shader-side counterpart of a number.
type FloatNode = THREE.Node<'float'>

const hash11 = (n: FloatNode): FloatNode => fract(sin(n.mul(91.17)).mul(43758.5453))

export async function build(ctx: SceneContext): Promise<SceneHandle> {
  const { renderer, font, temperatureText } = ctx
  const tempText = (temperatureText || '19').slice(0, 4)

  // overhead preset (baked default)
  const SKY_TOP = 0x141a22
  const SKY_BOTTOM = 0x333d48
  const RAIN_FRAC = 0.85
  const RAIN_SPEED = 1.35
  const RAIN_OPACITY = 0.55
  // negative = falls toward the LEFT, matching the streaks' lean below
  const WIND = -0.16
  const DROP_RATE = 2.2
  let strikeMin = 2.4
  let strikeRange = 3.0
  let flashScale = 0.95

  // uniforms
  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))
  const uRainFrac = uniform(RAIN_FRAC)
  const uRainSpeed = uniform(RAIN_SPEED)
  const uRainOpacity = uniform(RAIN_OPACITY)
  const uWind = uniform(WIND)
  const uDropRate = uniform(DROP_RATE)
  const uFlash = uniform(0.0)   // full-scene lightning flash 0..1
  const uBolt = uniform(0.0)    // bolt visibility 0..1

  // scene
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(SKY_BOTTOM)

  const camera = new THREE.PerspectiveCamera(34, 2, 0.1, 80)
  camera.position.set(0, 0.1, 8.4)

  // sky
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(48, 24), new THREE.MeshBasicNodeMaterial())
  sky.material.depthWrite = false
  const skyGrad = mix(uSkyBottom, uSkyTop, smoothstep(0.0, 1.0, uv().y))
  sky.material.colorNode = mix(skyGrad, vec3(0.72, 0.78, 0.95), uFlash.mul(0.5)) // lightning lifts the sky
  sky.position.set(0, 0, -9)
  sky.renderOrder = -10
  scene.add(sky)

  // dark churning storm ceiling across the top
  const ceil = new THREE.Mesh(new THREE.PlaneGeometry(22, 6.5), new THREE.MeshBasicNodeMaterial())
  ceil.material.transparent = true
  ceil.material.depthWrite = false
  ceil.position.set(0, 2.6, -3.2)
  {
    const cuv = uv()
    const n = mx_fractal_noise_float(vec3(cuv.x.mul(4.2).add(time.mul(0.04)), cuv.y.mul(2.6), time.mul(0.02)), 5).mul(0.5).add(0.5)
    // storm mass rides the right of the panel — density thins toward the left so
    // the temperature region stays clear
    const dens = smoothstep(0.34, 0.86, n).mul(smoothstep(0.0, 0.42, cuv.y)).mul(smoothstep(0.28, 0.55, cuv.x))
    const dark = mix(vec3(0.04, 0.05, 0.07), vec3(0.13, 0.14, 0.17), n)
    ceil.material.colorNode = dark.add(uFlash.mul(vec3(0.55, 0.6, 0.78))) // lit from within by lightning
    ceil.material.opacityNode = dens.mul(0.97)
  }
  scene.add(ceil)

  // rain (GPU instanced)
  const RAIN_N = 1600
  const TOP = 3.4
  const SPAN = 5.4
  const streakGeo = new THREE.PlaneGeometry(0.016, 0.5)
  const rainMat = new THREE.MeshBasicNodeMaterial()
  rainMat.transparent = true
  rainMat.depthWrite = false

  const idf = float(instanceIndex)
  // rain occupies the right side only (x > 0) and extends past the frame so the
  // panel crops it — the left stays clear for the temperature
  const rx = hash11(idf.add(0.5)).mul(10.3).add(0.3)
  const rz = hash11(idf.add(1.7)).sub(0.5).mul(3.2).add(0.6)
  const speed = hash11(idf.add(2.3)).mul(3.0).add(7.0)
  const phase = hash11(idf.add(3.9)).mul(100.0)
  const fall = fract(time.mul(speed).mul(uRainSpeed).add(phase).div(SPAN)).mul(SPAN)
  const yPos = float(TOP).sub(fall)
  const xDrift = fall.mul(uWind)
  const leaned = vec3(positionLocal.x.add(positionLocal.y.mul(0.16)), positionLocal.y, positionLocal.z)
  rainMat.positionNode = leaned.add(vec3(rx.add(xDrift), yPos, rz))
  const along = uv().y
  const ends = smoothstep(0.0, 0.16, along).mul(smoothstep(1.0, 0.84, along))
  const active = step(idf, float(RAIN_N).mul(uRainFrac))
  rainMat.colorNode = vec3(0.66, 0.74, 0.86).add(uFlash.mul(0.38)) // rain catches the flash
  rainMat.opacityNode = ends.mul(uRainOpacity).mul(active)
  const rain = new THREE.InstancedMesh(streakGeo, rainMat, RAIN_N)
  rain.frustumCulled = false
  scene.add(rain)

  // impact ripple field
  const rippleField = Fn<
    [THREE.Node<'vec2'>, FloatNode, FloatNode, FloatNode, FloatNode, FloatNode],
    FloatNode
  >(([q, spreadX, spreadY, offY, rate, sharp]) => {
    const acc = float(0.0).toVar()
    Loop({ start: 0, end: 7, type: 'int' }, ({ i }) => {
      const fi = float(i)
      const cyc = time.mul(rate).add(fi.mul(1.618))
      const id = cyc.floor()
      const age = cyc.fract()
      const cx = hash11(id.add(fi.mul(3.1))).sub(0.5).mul(spreadX)
      const cy = hash11(id.add(fi.mul(7.7)).add(2.3)).sub(0.5).mul(spreadY).add(offY)
      const d = q.sub(vec2(cx, cy)).length()
      const radius = age.mul(0.5)
      const band = d.sub(radius)
      const ring = exp(band.mul(band).mul(sharp.negate()))
      const onset = smoothstep(0.0, 0.05, age)
      const fade = age.oneMinus().pow(1.5)
      acc.addAssign(ring.mul(onset).mul(fade))
    })
    return acc
  })

  // wet temperature
  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(2.2, 0.1, 1.1)
  scene.add(temperatureGroup)

  const tFace = max(normalView.z, 0.0)
  const tFres = tFace.oneMinus().pow(2.1)
  const q = positionLocal.xy
  const drops = rippleField(q, 2.8, 1.2, 0.1, uDropRate.mul(1.6), float(300.0)).mul(step(0.25, tFace))
  const rivulet = mx_fractal_noise_float(vec3(q.x.mul(9.0), q.y.mul(1.1).add(time.mul(0.55)), 0.0), 3).mul(0.5).add(0.5)
  const rivStreak = smoothstep(0.6, 0.95, rivulet).mul(tFace).mul(0.5)
  let tColor: THREE.Node<'vec3'> = vec3(0.22, 0.26, 0.32)
  tColor = mix(tColor, vec3(0.72, 0.82, 0.94), tFres)
  tColor = tColor.add(drops.mul(vec3(1.0, 1.06, 1.15)).mul(1.6))
  tColor = tColor.add(rivStreak.mul(vec3(0.4, 0.48, 0.58)))
  tColor = tColor.add(uFlash.mul(vec3(0.8, 0.85, 1.0)).mul(0.6)) // lit by lightning
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = tColor

  // unit-size glyphs, left edge at x=0 and vertically centred; frame() scales the
  // group so the number is exactly half the panel height. Face-on, no skew.
  const geometry = new TextGeometry(tempText, {
    font, size: 1, depth: 0.14, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.018, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const bb = geometry.boundingBox
  // Non-empty text always measures; no box means the font failed to load glyphs,
  // and the banner falls back to the static gradient rather than render nothing.
  if (!bb) throw new Error('no-glyph-bounds')
  geometry.translate(-bb.min.x, -(bb.max.y + bb.min.y) / 2, 0)
  const glyphH = bb.max.y - bb.min.y
  const glyphW = bb.max.x - bb.min.x
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  temperatureGroup.add(text)

  // lightning bolt
  const boltMat = new THREE.MeshBasicNodeMaterial()
  boltMat.transparent = true
  boltMat.depthWrite = false
  boltMat.blending = THREE.AdditiveBlending
  boltMat.colorNode = vec3(1.4, 1.5, 1.9) // HDR so it blooms to a glow while the core stays sharp
  boltMat.opacityNode = uBolt
  const bolt = new THREE.Mesh(new THREE.BufferGeometry(), boltMat)
  bolt.frustumCulled = false
  scene.add(bolt)

  function jaggedPath(x0: number, y0: number, segs: number, len: number, jit: number) {
    const pts: THREE.Vector3[] = []
    let x = x0
    let y = y0
    pts.push(new THREE.Vector3(x, y, -2.6))
    for (let i = 0; i < segs; i += 1) {
      y -= len / segs
      x += (Math.random() - 0.5) * jit
      pts.push(new THREE.Vector3(x, y, -2.6))
    }
    return pts
  }

  function regenBolt() {
    // strikes come down on the right side, under the storm mass
    const mainPts = jaggedPath(1.2 + Math.random() * 4.5, 3.5, 8, 4.0, 0.95)
    const geos = [new THREE.TubeGeometry(new THREE.CatmullRomCurve3(mainPts), 50, 0.034, 6, false)]
    // a fork branching off a mid vertex
    const bp = mainPts[3 + Math.floor(Math.random() * 3)]
    const forkPts = jaggedPath(bp.x, bp.y, 4, 1.7, 0.8)
    forkPts[0].copy(bp)
    geos.push(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(forkPts), 24, 0.02, 6, false))
    const merged = mergeGeometries(geos)
    bolt.geometry.dispose()
    bolt.geometry = merged
    geos.forEach((g) => g.dispose())
  }
  regenBolt()

  // bloom pipeline (reuse engine renderer)
  const postProcessing = new THREE.RenderPipeline(renderer)
  const scenePass = pass(scene, camera)
  const scenePassColor = scenePass.getTextureNode()
  const bloomPass = bloom(scenePassColor, 0.7, 0.32, 0.7) // only the HDR bolt blooms; keeps the fork sharp
  postProcessing.outputNode = scenePassColor.add(bloomPass)

  // lightning scheduler / envelopes
  let strikeTime = -1e3
  let nextStrike: number | null = null

  const spike = (a: number, t0: number, k: number) => (a < t0 ? 0 : Math.exp(-(a - t0) * k))

  function frame(t: number) {
    if (nextStrike === null) nextStrike = t + strikeMin + Math.random() * strikeRange

    // lightning scheduler
    if (t > nextStrike) {
      strikeTime = t
      regenBolt()
      nextStrike = t + strikeMin + Math.random() * strikeRange
    }
    const age = t - strikeTime
    // snappy strike: a quick flicker then gone, like real lightning (~0.18s total)
    const flash = Math.max(spike(age, 0, 16), 0.7 * spike(age, 0.05, 20), 0.4 * spike(age, 0.12, 18))
    uFlash.value = Math.min(1, flash * flashScale)
    uBolt.value = Math.min(1, Math.max(spike(age, 0, 22), 0.55 * spike(age, 0.05, 26)))

    // gentle camera drift; lookAt tracks camera.x so the drift is pure translation
    camera.position.x = Math.sin(t * 0.07) * 0.14
    camera.position.y = 0.1 + Math.sin(t * 0.1) * 0.04
    camera.lookAt(camera.position.x, -0.1, 0)

    // temperature: vertically centred, half the panel height, steady, centred in
    // the dry region left of the rain (x≈0.3). camera.zoom read at frame time —
    // the engine applies its zoom knob after build() returns.
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom
    const halfHT = k * (camera.position.z - temperatureGroup.position.z)
    const scaleT = halfHT / glyphH
    const leftEdge = camera.position.x - halfHT * camera.aspect
    temperatureGroup.scale.setScalar(scaleT)
    temperatureGroup.position.x = (leftEdge + 0.3) / 2 - (glyphW * scaleT) / 2
    temperatureGroup.position.y = -0.1
  }

  const render = () => postProcessing.render()

  return { scene, camera, frame, render }
}
