// Windy banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, dispose }. Wind is invisible,
// so we draw it: streaming GPU flow-lines riding fixed wave paths across a
// bright sky, with swelling gusts that surge the flow. The lines live on the
// right of the panel; the steady temperature holds the left.
// Default preset baked = GUSTY.

import * as THREE from 'three/webgpu'
import {
  vec3, float, uniform,
  positionLocal, normalView, uv, instanceIndex,
  max, mix, smoothstep, sin, fract, step,
} from 'three/tsl'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'
import type { SceneContext, SceneHandle } from '../engine.ts'

// A scalar TSL node — the shader-side counterpart of a number.
type FloatNode = THREE.Node<'float'>

const hash11 = (n: FloatNode): FloatNode => fract(sin(n.mul(91.17)).mul(43758.5453))

const X = 7.2
const WSPAN = 14.4

export async function build(ctx: SceneContext): Promise<SceneHandle> {
  const { font, temperatureText } = ctx
  const tempText = (temperatureText || '18').slice(0, 4)

  // gusty preset (baked default)
  const SKY_TOP = 0x6ba2d4
  const SKY_BOTTOM = 0xcfe4f4
  const WIND_SPEED = 1.05
  const STREAK_FRAC = 0.72
  const STREAK_OPACITY = 0.24
  const GUST_SCALE = 0.85

  // module-scope gust scale for the frame loop
  let gustScale = GUST_SCALE

  // uniforms
  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))
  const uWindSpeed = uniform(WIND_SPEED)
  const uStreakFrac = uniform(STREAK_FRAC)
  const uStreakOpacity = uniform(STREAK_OPACITY)
  const uGust = uniform(0.5) // 0..1 swelling gustiness
  // Flow phase, integrated on the CPU each frame at a gust-modulated rate.
  // Multiplying absolute time by gust(t) made the phase SHRINK as gusts faded,
  // visibly reversing the streaks — integration guarantees one-way flow.
  const uFlowTime = uniform(0)

  // scene
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(SKY_BOTTOM)

  const camera = new THREE.PerspectiveCamera(34, 2, 0.1, 80)
  camera.position.set(0, 0.1, 8.4)

  // sky
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(48, 24), new THREE.MeshBasicNodeMaterial())
  sky.material.depthWrite = false
  sky.material.colorNode = mix(uSkyBottom, uSkyTop, smoothstep(0.0, 1.0, uv().y))
  sky.position.set(0, 0, -9)
  sky.renderOrder = -10
  scene.add(sky)

  // wind flow-lines (GPU instanced)
  // One layer of streaks: `count` instances of `geo`, placed by the shader from the
  // per-layer knobs below.
  type FlowOptions = {
    count: number
    spreadY: number
    spreadZ: number
    zBase: number
    speedMin: number
    speedVar: number
    ampMin: number
    ampVar: number
    color: THREE.Node<'vec3'>
    opacityUniform: FloatNode
    fracUniform: FloatNode
  }
  function buildFlow(
    count: number,
    geo: THREE.BufferGeometry,
    mat: THREE.MeshBasicNodeMaterial,
    opts: FlowOptions,
  ) {
    const idf = float(instanceIndex)
    const baseY = hash11(idf.add(0.5)).sub(0.5).mul(opts.spreadY)
    const zPos = hash11(idf.add(1.7)).sub(0.5).mul(opts.spreadZ).add(opts.zBase)
    const spd = hash11(idf.add(2.3)).mul(opts.speedVar).add(opts.speedMin)
    const ph = hash11(idf.add(3.9)).mul(100.0)
    const amp = hash11(idf.add(4.4)).mul(opts.ampVar).add(opts.ampMin)
    const flow = uFlowTime.mul(spd).mul(uWindSpeed).add(ph)
    const x = fract(flow.div(WSPAN)).mul(WSPAN).sub(X)
    // purely spatial undulation — each streak rides a fixed wave path as it
    // streams, rather than bobbing in place (no time term)
    const y = baseY.add(sin(x.mul(0.5).add(ph)).mul(amp))
    const stretch = uGust.mul(0.7).add(0.85)
    const lp = vec3(positionLocal.x.mul(stretch), positionLocal.y, positionLocal.z)
    mat.positionNode = lp.add(vec3(x, y, zPos))
    const ux = uv().x
    const fade = smoothstep(0.0, 0.2, ux).mul(smoothstep(1.0, 0.8, ux))
    const active = step(idf, float(opts.count).mul(opts.fracUniform))
    // the wind lives on the right of the panel: flow fades in around the centre
    // and streams off the right edge, leaving the left clear for the temperature
    const rightSide = smoothstep(0.0, 2.2, x)
    mat.colorNode = opts.color
    mat.opacityNode = fade.mul(opts.opacityUniform).mul(uGust.mul(0.45).add(0.55)).mul(active).mul(rightSide)
    const mesh = new THREE.InstancedMesh(geo, mat, count)
    mesh.frustumCulled = false
    return mesh
  }

  // thin fast flow streaks
  const STREAK_N = 900
  const streakMat = new THREE.MeshBasicNodeMaterial()
  streakMat.transparent = true
  streakMat.depthWrite = false
  const streaks = buildFlow(STREAK_N, new THREE.PlaneGeometry(0.62, 0.024), streakMat, {
    count: STREAK_N, spreadY: 4.2, spreadZ: 3.0, zBase: 0.4, speedMin: 2.6, speedVar: 1.6,
    ampMin: 0.05, ampVar: 0.28, color: vec3(0.95, 0.97, 1.0), opacityUniform: uStreakOpacity, fracUniform: uStreakFrac,
  })
  scene.add(streaks)

  // temperature
  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(2.0, -0.05, 1.1)
  scene.add(temperatureGroup)

  // dark slate number with a subtle facing lift — reads cleanly against the
  // bright sky without needing an outline
  const tFace = max(normalView.z, 0.0)
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = mix(vec3(0.14, 0.18, 0.24), vec3(0.26, 0.32, 0.4), tFace)

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

  let prevT: number | null = null

  function frame(t: number) {
    // swelling, natural gustiness (layered sines) scaled per preset
    const g = 0.5 + 0.34 * Math.sin(t * 0.7) + 0.16 * Math.sin(t * 1.9 + 1.3)
    const gust = Math.max(0, Math.min(1, g)) * gustScale
    uGust.value = gust

    // advance the flow phase at a gust-modulated rate — strictly forward
    if (prevT === null) prevT = t
    uFlowTime.value += Math.max(0, t - prevT) * (0.7 + 0.7 * gust)
    prevT = t

    // gentle camera drift; lookAt tracks camera.x so the drift is pure translation
    camera.position.x = Math.sin(t * 0.06) * 0.14
    camera.position.y = 0.1 + Math.sin(t * 0.09) * 0.04
    camera.lookAt(camera.position.x, -0.05, 0)

    // temperature: vertically centred, half the panel height, steady, centred in
    // the clear region between the left frame edge and where the wind flow fades
    // in (x≈0.6). camera.zoom read at frame time — engine applies zoom post-build.
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom
    const halfHT = k * (camera.position.z - temperatureGroup.position.z)
    const scaleT = halfHT / glyphH
    const leftEdge = camera.position.x - halfHT * camera.aspect
    temperatureGroup.scale.setScalar(scaleT)
    temperatureGroup.position.x = (leftEdge + 0.6) / 2 - (glyphW * scaleT) / 2
    temperatureGroup.position.y = -0.05
  }

  return { scene, camera, frame }
}
