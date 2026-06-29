// Windy banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, dispose }. Wind is invisible,
// so we draw it: streaming GPU flow-lines undulating across a bright sky,
// tumbling autumn leaves driven along the same flow, swelling gusts that surge
// everything, and a 3D temperature that leans + buffets into the wind.
// Default preset baked = GUSTY.

import * as THREE from 'three/webgpu'
import {
  vec2, vec3, float, uniform, time,
  positionLocal, normalView, uv, instanceIndex,
  max, mix, smoothstep, sin, cos, fract, step,
} from 'three/tsl'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'

const hash11 = (n) => fract(sin(n.mul(91.17)).mul(43758.5453))

const X = 7.2
const WSPAN = 14.4

export async function build(ctx) {
  const { font, temperatureText } = ctx
  const tempText = (temperatureText || '18').slice(0, 4)

  // gusty preset (baked default)
  const SKY_TOP = 0x6ba2d4
  const SKY_BOTTOM = 0xcfe4f4
  const WIND_SPEED = 1.05
  const STREAK_FRAC = 0.72
  const STREAK_OPACITY = 0.24
  const LEAF_FRAC = 0.7
  const GUST_SCALE = 0.85

  // module-scope gust scale for the frame loop
  let gustScale = GUST_SCALE

  // uniforms
  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))
  const uWindSpeed = uniform(WIND_SPEED)
  const uStreakFrac = uniform(STREAK_FRAC)
  const uStreakOpacity = uniform(STREAK_OPACITY)
  const uLeafFrac = uniform(LEAF_FRAC)
  const uGust = uniform(0.5) // 0..1 swelling gustiness

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
  function buildFlow(count, geo, mat, opts) {
    const idf = float(instanceIndex)
    const baseY = hash11(idf.add(0.5)).sub(0.5).mul(opts.spreadY)
    const zPos = hash11(idf.add(1.7)).sub(0.5).mul(opts.spreadZ).add(opts.zBase)
    const spd = hash11(idf.add(2.3)).mul(opts.speedVar).add(opts.speedMin)
    const ph = hash11(idf.add(3.9)).mul(100.0)
    const amp = hash11(idf.add(4.4)).mul(opts.ampVar).add(opts.ampMin)
    const flow = time.mul(spd).mul(uWindSpeed).mul(uGust.mul(0.7).add(0.7)).add(ph)
    const x = fract(flow.div(WSPAN)).mul(WSPAN).sub(X)
    const y = baseY.add(sin(x.mul(0.5).add(time.mul(0.6)).add(ph)).mul(amp))
    const stretch = uGust.mul(0.7).add(0.85)
    const lp = vec3(positionLocal.x.mul(stretch), positionLocal.y, positionLocal.z)
    mat.positionNode = lp.add(vec3(x, y, zPos))
    const ux = uv().x
    const ends = smoothstep(0.0, 0.2, ux).mul(smoothstep(1.0, 0.8, ux))
    const active = step(idf, float(opts.count).mul(opts.fracUniform))
    mat.colorNode = opts.color
    mat.opacityNode = ends.mul(opts.opacityUniform).mul(uGust.mul(0.45).add(0.55)).mul(active)
    const mesh = new THREE.InstancedMesh(geo, mat, count)
    mesh.frustumCulled = false
    return mesh
  }

  // thin fast flow streaks
  const STREAK_N = 900
  const streakMat = new THREE.MeshBasicNodeMaterial()
  streakMat.transparent = true
  streakMat.depthWrite = false
  const streaks = buildFlow(STREAK_N, new THREE.PlaneGeometry(0.62, 0.011), streakMat, {
    count: STREAK_N, spreadY: 4.2, spreadZ: 3.0, zBase: 0.4, speedMin: 2.6, speedVar: 1.6,
    ampMin: 0.05, ampVar: 0.28, color: vec3(0.95, 0.97, 1.0), opacityUniform: uStreakOpacity, fracUniform: uStreakFrac,
  })
  scene.add(streaks)

  // big soft high cloud wisps streaming across the upper sky
  const WISP_N = 16
  const wispMat = new THREE.MeshBasicNodeMaterial()
  wispMat.transparent = true
  wispMat.depthWrite = false
  const wisps = buildFlow(WISP_N, new THREE.PlaneGeometry(2.4, 0.5), wispMat, {
    count: WISP_N, spreadY: 2.2, spreadZ: 1.0, zBase: -2.4, speedMin: 0.9, speedVar: 0.6,
    ampMin: 0.04, ampVar: 0.12, color: vec3(1.0, 1.0, 1.0), opacityUniform: uniform(0.14), fracUniform: uniform(1.0),
  })
  // soften wisp edges into blobs
  {
    const p = uv().sub(vec2(0.5))
    const d = p.mul(vec2(1.0, 2.0)).length()
    const soft = smoothstep(0.5, 0.1, d)
    wispMat.opacityNode = soft.mul(0.16).mul(uGust.mul(0.3).add(0.7))
  }
  scene.add(wisps)

  // tumbling leaves
  const LEAF_N = 80
  const leafMat = new THREE.MeshBasicNodeMaterial()
  leafMat.transparent = true
  leafMat.depthWrite = false
  {
    const lid = float(instanceIndex)
    const ly0 = hash11(lid.add(0.5)).sub(0.5).mul(4.2)
    const lz = hash11(lid.add(1.7)).sub(0.5).mul(2.6).add(0.5)
    const lspd = hash11(lid.add(2.3)).mul(1.2).add(1.8)
    const lph = hash11(lid.add(3.9)).mul(100.0)
    const lamp = hash11(lid.add(4.4)).mul(0.5).add(0.15)
    const lsize = hash11(lid.add(5.0)).mul(0.05).add(0.045)
    const spin = hash11(lid.add(6.0)).sub(0.5).mul(9.0)
    const lflow = time.mul(lspd).mul(uWindSpeed).mul(uGust.mul(0.8).add(0.6)).add(lph)
    const lx = fract(lflow.div(WSPAN)).mul(WSPAN).sub(X)
    const ly = ly0.add(sin(lx.mul(0.6).add(time.mul(0.8)).add(lph)).mul(lamp)).add(cos(time.mul(1.6).add(lph)).mul(0.07))
    const ang = time.mul(spin).add(lph)
    const c = cos(ang)
    const s = sin(ang)
    const rx = positionLocal.x.mul(c).sub(positionLocal.y.mul(s))
    const ry = positionLocal.x.mul(s).add(positionLocal.y.mul(c))
    leafMat.positionNode = vec3(rx, ry, positionLocal.z).mul(lsize).add(vec3(lx, ly, lz))
    // soft rounded leaf, warm autumn tint per instance
    const d = uv().sub(vec2(0.5)).length()
    const lsoft = smoothstep(0.5, 0.22, d)
    const active = step(lid, float(LEAF_N).mul(uLeafFrac))
    leafMat.opacityNode = lsoft.mul(active)
    leafMat.colorNode = mix(vec3(0.82, 0.4, 0.12), vec3(0.92, 0.74, 0.22), hash11(lid.add(7.0)))
  }
  const leaves = new THREE.InstancedMesh(new THREE.PlaneGeometry(1, 1), leafMat, LEAF_N)
  leaves.frustumCulled = false
  scene.add(leaves)

  // temperature (leans into the wind)
  const temperatureGroup = new THREE.Group()
  temperatureGroup.position.set(2.0, -0.05, 1.1)
  scene.add(temperatureGroup)

  const tFace = max(normalView.z, 0.0)
  const tFres = tFace.oneMinus().pow(2.3)
  let tColor = vec3(0.30, 0.37, 0.46)                        // cool slate so it reads on the bright sky
  tColor = mix(tColor, vec3(0.66, 0.74, 0.85), tFres.mul(0.45))
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = tColor

  const geometry = new TextGeometry(tempText, {
    font, size: 1.0, depth: 0.2, curveSegments: 16,
    bevelEnabled: true, bevelThickness: 0.035, bevelSize: 0.024, bevelSegments: 5,
  })
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  geometry.translate(-(box.max.x - box.min.x) / 2, -(box.max.y - box.min.y) / 2, 0)

  // dark inverted-hull outline so it pops against the bright windy sky
  const outlineMaterial = new THREE.MeshBasicNodeMaterial()
  outlineMaterial.colorNode = vec3(0.12, 0.15, 0.19)
  outlineMaterial.side = THREE.BackSide
  const outline = new THREE.Mesh(geometry, outlineMaterial)
  outline.scale.setScalar(1.07)
  outline.position.z = -0.04
  temperatureGroup.add(outline)

  const text = new THREE.Mesh(geometry, temperatureMaterial)
  temperatureGroup.add(text)

  function frame(t) {
    // swelling, natural gustiness (layered sines) scaled per preset
    const g = 0.5 + 0.34 * Math.sin(t * 0.7) + 0.16 * Math.sin(t * 1.9 + 1.3)
    const gust = Math.max(0, Math.min(1, g)) * gustScale
    uGust.value = gust

    // the number leans into the wind and buffets on gusts
    temperatureGroup.rotation.z = -0.05 - gust * 0.14 + Math.sin(t * 7.0) * gust * 0.02
    temperatureGroup.position.x = 2.0 + gust * 0.06
    temperatureGroup.position.y = -0.05 + Math.sin(t * 0.8) * 0.02
    temperatureGroup.rotation.y = Math.sin(t * 0.2) * 0.05

    camera.position.x = Math.sin(t * 0.06) * 0.18
    camera.position.y = 0.1 + Math.sin(t * 0.09) * 0.04
    camera.lookAt(0, -0.05, 0)
  }

  return { scene, camera, frame }
}
