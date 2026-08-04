// Snowy banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, dispose }. Blizzard preset.
//
// GPU-instanced snowflakes drift and sway down a cold sky. The signature: snow
// settles on the temperature — white caps build up on the up-facing surfaces of
// the 3D digits over time (uSnowAccum), the parallel to rain striking the number.

import * as THREE from 'three/webgpu'
import {
  vec2, vec3, float, uniform, time,
  positionLocal, positionWorld, normalView, normalWorld, uv, instanceIndex,
  max, mix, smoothstep, sin, fract, step,
  mx_fractal_noise_float,
} from 'three/tsl'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'
import type { SceneContext, SceneHandle } from '../engine.ts'

// A scalar TSL node — the shader-side counterpart of a number.
type FloatNode = THREE.Node<'float'>

const hash11 = (n: FloatNode): FloatNode => fract(sin(n.mul(91.17)).mul(43758.5453))

export async function build(ctx: SceneContext): Promise<SceneHandle> {
  const { font, temperatureText } = ctx
  const tempText = (temperatureText || '-2').slice(0, 4)

  // blizzard preset (baked from applyPreset('blizzard'))
  const SKY_TOP = 0x8c9eb0
  const SKY_BOTTOM = 0xc6d1d8
  const SNOW_FRAC = 1.0
  const SNOW_SPEED = 1.5
  const SNOW_OPACITY = 1.0
  const SWAY = 1.7
  const buildSeconds = 4

  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))
  const uSnowFrac = uniform(SNOW_FRAC)
  const uSnowSpeed = uniform(SNOW_SPEED)
  const uSnowOpacity = uniform(SNOW_OPACITY)
  const uSwayAmp = uniform(SWAY)
  const uSnowAccum = uniform(0.0) // 0..1, how much snow has built up on the number

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

  // snowflakes (GPU instanced)
  const SNOW_N = 1300
  const TOP = 3.4
  const SPAN = 5.6
  const flakeGeo = new THREE.PlaneGeometry(1, 1)
  const snowMat = new THREE.MeshBasicNodeMaterial()
  snowMat.transparent = true
  snowMat.depthWrite = false

  const idf = float(instanceIndex)
  // snow occupies the right side only (x > 0) and extends past the frame so the
  // panel crops it — the left stays clear for the temperature
  const fx = hash11(idf.add(0.5)).mul(10.5).add(0.3)
  const fz = hash11(idf.add(1.7)).sub(0.5).mul(3.4).add(0.4)
  const size = hash11(idf.add(2.1)).pow(2.0).mul(0.08).add(0.024) // mostly small, a few large (depth)
  const speed = hash11(idf.add(2.3)).mul(0.35).add(0.4)            // slow, varied descent
  const phase = hash11(idf.add(3.9)).mul(100.0)
  const swayAmp = hash11(idf.add(4.4)).mul(0.28).add(0.08)
  const swaySpd = hash11(idf.add(5.1)).mul(0.6).add(0.5)
  const fallA = fract(time.mul(speed).mul(uSnowSpeed).add(phase).div(SPAN)).mul(SPAN)
  const yPos = float(TOP).sub(fallA)
  const sway = sin(time.mul(swaySpd).add(phase).add(fallA.mul(1.2))).mul(swayAmp).mul(uSwayAmp)
  const quad = positionLocal.mul(size) // scale the flake billboard
  snowMat.positionNode = quad.add(vec3(fx.add(sway), yPos, fz))

  const dCenter = uv().sub(vec2(0.5)).length()
  const soft = smoothstep(0.5, 0.08, dCenter) // soft round flake
  const activeFlake = step(idf, float(SNOW_N).mul(uSnowFrac))
  snowMat.colorNode = vec3(0.96, 0.98, 1.0)
  snowMat.opacityNode = soft.mul(uSnowOpacity).mul(activeFlake)

  const snow = new THREE.InstancedMesh(flakeGeo, snowMat, SNOW_N)
  snow.frustumCulled = false
  scene.add(snow)

  // temperature with settling snow
  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(2.2, -0.05, 1.1)
  scene.add(temperatureGroup)

  // up-facing surfaces accumulate snow; an uneven noisy snow line creeps down as
  // uSnowAccum grows. Front/side faces keep the cold steel base colour.
  const up = smoothstep(0.06, 0.5, normalWorld.y)           // catch angled-up faces too
  const edgeNoise = mx_fractal_noise_float(positionWorld.mul(7.0), 3).mul(0.5).add(0.5)
  const coverage = up.mul(uSnowAccum.mul(1.35))
  const snowMask = smoothstep(0.34, 0.6, coverage.mul(0.78).add(edgeNoise.mul(0.4)))
  const tFace = max(normalView.z, 0.0)
  const tFres = tFace.oneMinus().pow(2.4)
  let tColor: THREE.Node<'vec3'> = vec3(0.27, 0.33, 0.42)                       // dark cold steel-blue so it reads + caps pop
  tColor = mix(tColor, vec3(0.6, 0.68, 0.8), tFres.mul(0.35)) // faint cool rim sheen
  tColor = mix(tColor, vec3(0.99, 1.0, 1.0), snowMask)     // white snow caps on top
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

  let startT: number | null = null

  function frame(t: number) {
    if (startT === null) startT = t
    // snow builds up on the number, then holds
    uSnowAccum.value = Math.min(1, Math.max(0, (t - startT) / buildSeconds))

    // gentle camera drift for a little life; lookAt tracks camera.x so the drift
    // is a pure translation
    camera.position.x = Math.sin(t * 0.06) * 0.14
    camera.position.y = 0.1 + Math.sin(t * 0.09) * 0.04
    camera.lookAt(camera.position.x, -0.05, 0)

    // temperature: vertically centred, half the panel height, and horizontally
    // centred in the clear region between the left frame edge and where the
    // snowfall begins (x≈0.3). Pinned to the camera so it stays steady;
    // camera.zoom read at frame time — the engine applies its zoom after build().
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom
    const halfHT = k * (camera.position.z - temperatureGroup.position.z)
    const scaleT = halfHT / glyphH
    const leftEdge = camera.position.x - halfHT * camera.aspect
    temperatureGroup.scale.setScalar(scaleT)
    temperatureGroup.position.x = (leftEdge + 0.3) / 2 - (glyphW * scaleT) / 2
    temperatureGroup.position.y = -0.05
  }

  return { scene, camera, frame }
}
