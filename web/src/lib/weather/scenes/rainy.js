// Rainy banner scene. Ported from the standalone prototype to the engine
// contract: build(ctx) -> { scene, camera, frame, dispose }. Steady-rain preset.

import * as THREE from 'three/webgpu'
import {
  Fn, Loop, vec2, vec3, float, uniform, time,
  positionLocal, normalView, uv, instanceIndex,
  max, mix, smoothstep, sin, fract, step, exp,
  mx_fractal_noise_float,
} from 'three/tsl'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'

const hash11 = (n) => fract(sin(n.mul(91.17)).mul(43758.5453))

export async function build(ctx) {
  const { font, temperatureText } = ctx
  const tempText = (temperatureText || '14').slice(0, 4)

  // steady-rain preset
  const SKY_TOP = 0x2f3743
  const SKY_BOTTOM = 0x5d6772
  const RAIN_FRAC = 0.78
  const RAIN_SPEED = 1.0
  const RAIN_OPACITY = 0.46
  const WIND = 0.12
  const DROP_RATE = 1.4

  const uRainFrac = uniform(RAIN_FRAC)
  const uRainSpeed = uniform(RAIN_SPEED)
  const uRainOpacity = uniform(RAIN_OPACITY)
  const uWind = uniform(WIND)
  const uDropRate = uniform(DROP_RATE)
  const uSkyTop = uniform(new THREE.Color(SKY_TOP))
  const uSkyBottom = uniform(new THREE.Color(SKY_BOTTOM))

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

  // rain (GPU instanced)
  const RAIN_N = 1500
  const TOP = 3.4
  const SPAN = 5.4
  const streakGeo = new THREE.PlaneGeometry(0.016, 0.46)
  const rainMat = new THREE.MeshBasicNodeMaterial()
  rainMat.transparent = true
  rainMat.depthWrite = false
  const idf = float(instanceIndex)
  const rx = hash11(idf.add(0.5)).sub(0.5).mul(10.4)
  const rz = hash11(idf.add(1.7)).sub(0.5).mul(3.2).add(0.6)
  const speed = hash11(idf.add(2.3)).mul(3.0).add(6.0)
  const phase = hash11(idf.add(3.9)).mul(100.0)
  const fall = fract(time.mul(speed).mul(uRainSpeed).add(phase).div(SPAN)).mul(SPAN)
  const yPos = float(TOP).sub(fall)
  const xDrift = fall.mul(uWind)
  const leaned = vec3(positionLocal.x.add(positionLocal.y.mul(0.16)), positionLocal.y, positionLocal.z)
  rainMat.positionNode = leaned.add(vec3(rx.add(xDrift), yPos, rz))
  const along = uv().y
  const ends = smoothstep(0.0, 0.16, along).mul(smoothstep(1.0, 0.84, along))
  const active = step(idf, float(RAIN_N).mul(uRainFrac))
  rainMat.colorNode = vec3(0.74, 0.82, 0.92)
  rainMat.opacityNode = ends.mul(uRainOpacity).mul(active)
  const rain = new THREE.InstancedMesh(streakGeo, rainMat, RAIN_N)
  rain.frustumCulled = false
  scene.add(rain)

  // impact ripple field
  const rippleField = Fn(([q, spreadX, spreadY, offY, rate, sharp]) => {
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
  let tColor = vec3(0.24, 0.28, 0.34)
  tColor = mix(tColor, vec3(0.72, 0.82, 0.94), tFres)
  tColor = tColor.add(drops.mul(vec3(1.0, 1.06, 1.15)).mul(1.6))
  tColor = tColor.add(rivStreak.mul(vec3(0.4, 0.48, 0.58)))
  const temperatureMaterial = new THREE.MeshBasicNodeMaterial()
  temperatureMaterial.colorNode = tColor

  const geometry = new TextGeometry(tempText, {
    font, size: 1.0, depth: 0.2, curveSegments: 14,
    bevelEnabled: true, bevelThickness: 0.035, bevelSize: 0.024, bevelSegments: 4,
  })
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  geometry.translate(-(box.max.x - box.min.x) / 2, -(box.max.y - box.min.y) / 2, 0)
  const text = new THREE.Mesh(geometry, temperatureMaterial)
  text.rotation.y = -0.14
  text.rotation.x = 0.04
  temperatureGroup.add(text)

  // puddle
  const puddle = new THREE.Mesh(new THREE.PlaneGeometry(20, 9), new THREE.MeshBasicNodeMaterial())
  puddle.rotation.x = -Math.PI / 2
  puddle.position.set(0, -1.7, 0.5)
  puddle.material.transparent = true
  puddle.material.depthWrite = false
  {
    const pq = positionLocal.xy.mul(0.32)
    const rings = rippleField(pq, 6.0, 3.0, 0.0, uDropRate.mul(2.2), float(650.0)).mul(1.4)
    const depthFade = smoothstep(-4.0, 2.0, positionLocal.y)
    const base = mix(uSkyBottom, uSkyTop, depthFade).mul(0.4)
    puddle.material.colorNode = base.add(rings.mul(vec3(0.6, 0.68, 0.8)))
    puddle.material.opacityNode = depthFade.mul(0.85).add(0.1)
  }
  scene.add(puddle)

  function frame(t) {
    temperatureGroup.position.y = 0.1 + Math.sin(t * 0.7) * 0.03
    temperatureGroup.rotation.y = Math.sin(t * 0.2) * 0.05
    camera.position.x = Math.sin(t * 0.07) * 0.22
    camera.position.y = 0.1 + Math.sin(t * 0.1) * 0.05
    camera.lookAt(0, -0.1, 0)
  }

  return { scene, camera, frame }
}
