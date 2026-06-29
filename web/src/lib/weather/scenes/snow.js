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

const hash11 = (n) => fract(sin(n.mul(91.17)).mul(43758.5453))

export async function build(ctx) {
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
  const fx = hash11(idf.add(0.5)).sub(0.5).mul(11.0)
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
  let tColor = vec3(0.27, 0.33, 0.42)                       // dark cold steel-blue so it reads + caps pop
  tColor = mix(tColor, vec3(0.6, 0.68, 0.8), tFres.mul(0.35)) // faint cool rim sheen
  tColor = mix(tColor, vec3(0.99, 1.0, 1.0), snowMask)     // white snow caps on top
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = tColor

  const geometry = new TextGeometry(tempText, {
    font, size: 1.0, depth: 0.2, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.028, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  geometry.translate(-(box.max.x - box.min.x) / 2, -(box.max.y - box.min.y) / 2, 0)
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  text.rotation.y = -0.14
  text.rotation.x = 0.03
  temperatureGroup.add(text)

  // snow drift (ground)
  const drift = new THREE.Mesh(new THREE.PlaneGeometry(20, 9), new THREE.MeshBasicNodeMaterial())
  drift.rotation.x = -Math.PI / 2
  drift.position.set(0, -1.65, 0.5)
  drift.material.transparent = true
  drift.material.depthWrite = false
  {
    const pq = positionLocal.xy
    const bump = mx_fractal_noise_float(vec3(pq.x.mul(0.5), pq.y.mul(0.5), 0.0), 3).mul(0.5).add(0.5)
    const depthFade = smoothstep(-4.5, 1.5, positionLocal.y)
    // faint twinkle: a sparse grid of cells that flash on staggered cycles
    const cell = pq.mul(7.0).floor()
    const tw = hash11(cell.x.add(cell.y.mul(31.7)).add(time.mul(2.0).floor()))
    const sparkle = step(0.992, tw).mul(0.6)
    const base = mix(vec3(0.80, 0.85, 0.9), vec3(0.97, 0.99, 1.0), bump)
    drift.material.colorNode = base.add(sparkle)
    drift.material.opacityNode = depthFade.mul(0.95)
  }
  scene.add(drift)

  let startT = null

  function frame(t) {
    if (startT === null) startT = t
    // snow builds up on the number, then holds
    uSnowAccum.value = Math.min(1, Math.max(0, (t - startT) / buildSeconds))
    temperatureGroup.position.y = -0.05 + Math.sin(t * 0.6) * 0.025
    temperatureGroup.rotation.y = Math.sin(t * 0.18) * 0.05
    camera.position.x = Math.sin(t * 0.06) * 0.2
    camera.position.y = 0.1 + Math.sin(t * 0.09) * 0.05
    camera.lookAt(0, -0.05, 0)
  }

  return { scene, camera, frame }
}
