// Foggy banner scene. Ported from the standalone WebGPU/TSL prototype to the
// engine contract: build(ctx) -> { scene, camera, frame, render }. Shallow-fog
// preset. Mirrors the `webgpu_custom_fog_scattering` technique:
//   1. render flat-black silhouette pines into a bright, cool exponential fog
//   2. blur the scene pass (downsampled gaussian)
//   3. composite mix(sharp, blurred, densityFogFactor(viewZ))
// The blur of bright haze bleeding around dark silhouettes *is* the scattering;
// pixels deeper in the fog get the blurred version. The temperature number
// rides forward/back on Z, so it naturally dissolves into the haze as it recedes.

import * as THREE from 'three/webgpu'
import { densityFogFactor, mix, pass, reference, uniform, vec2 } from 'three/tsl'
import { gaussianBlur } from 'three/addons/tsl/display/GaussianBlurNode.js'
import { TreeGenerator } from 'three/addons/generators/TreeGenerator.js'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'

const FOG_COLOR = 0xc6cace // bright cool haze; background matches so trunks read as silhouettes

// shallow preset (the default the prototype starts with), baked in
const FOG_DENSITY = 0.085
const SCATTERING = 1.6
const TEXT_TRAVEL = 1.4
const TEXT_BASE = -1.2

export async function build(ctx) {
  const { renderer, font, temperatureText } = ctx
  const tempText = (temperatureText || '12').slice(0, 4)

  const scene = new THREE.Scene()
  scene.fog = new THREE.FogExp2(FOG_COLOR, FOG_DENSITY)
  scene.background = new THREE.Color(FOG_COLOR)

  const camera = new THREE.PerspectiveCamera(50, 2, 0.1, 120)
  camera.position.set(0.4, 1.62, 8)

  // --- forest of flat-black silhouettes ---
  // the fog + scattering blur do all the shaping, so MeshBasicNodeMaterial
  // (unlit) is all we need
  const material = new THREE.MeshBasicNodeMaterial({ color: 0x000000 })

  const generator = new TreeGenerator(material)
  const variants = []
  for (let v = 0; v < 5; v += 1) {
    const mesh = generator
      .setSeed(v + 1)
      .setTrunkLength(2.6 + v * 0.3) // crowns ride high in the fog
      .setTrunkRadius(0.06)
      .setTaper(0.28)
      .setLevels(4)
      .setChildren([7, 5, 4])
      .setBranchAngle([55, 50, 46])
      .setAngleVariance(22)
      .setLengthRatio(0.46)
      .setMinLength(0.04)
      .setDroop(0.05)
      .setUpPull(0.42)
      .setGnarl([0, 0.14, 0.24, 0.34])
      .setSectionLength(0.34)
      .setRadialSegments(7)
      .setRadiusExponent(2.5)
      .setMinRadius(0.0023)
      .setTrunkClear(0.72) // tall clean bole, crown only at the top
      .build()
    variants.push(mesh.geometry)
  }

  // place a stand of pines, but keep a clearing centre-right so the number reads
  const placements = variants.map(() => [])
  const dummy = new THREE.Object3D()
  const cols = 9
  const rows = 7
  const spacing = 1.9
  for (let i = 0; i < cols; i += 1) {
    for (let j = 0; j < rows; j += 1) {
      const x = (i - cols / 2) * spacing + (Math.random() - 0.5) * spacing * 0.7
      const z = j * spacing - rows * spacing + 2.4 + (Math.random() - 0.5) * spacing * 0.7
      // carve a clearing for the temperature number
      if (x > 0.6 && x < 5.2 && z > -4.5 && z < 1.5) continue
      const v = Math.floor(Math.random() * variants.length)
      dummy.position.set(x, 0, z)
      const scale = 0.85 + Math.random() * 0.4
      dummy.rotation.set((Math.random() - 0.5) * 0.05, Math.random() * Math.PI * 2, (Math.random() - 0.5) * 0.05)
      dummy.scale.set(scale, scale * (0.9 + Math.random() * 0.3), scale)
      dummy.updateMatrix()
      placements[v].push(dummy.matrix.clone())
    }
  }
  variants.forEach((geometry, v) => {
    const list = placements[v]
    if (!list.length) return
    const mesh = new THREE.InstancedMesh(geometry, material, list.length)
    for (let k = 0; k < list.length; k += 1) mesh.setMatrixAt(k, list[k])
    mesh.instanceMatrix.needsUpdate = true
    scene.add(mesh)
  })

  // a couple of dominant near trunks to anchor depth on the left
  ;[[-1.4, 3.6, 1.2, 1.1], [-3.2, 1.8, 1.0, 0.3]].forEach(([x, z, s, ry]) => {
    const hero = new THREE.Mesh(variants[variants.length - 1], material)
    hero.position.set(x, 0, z)
    hero.rotation.y = ry
    hero.scale.setScalar(s)
    scene.add(hero)
  })

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(600, 600).rotateX(-Math.PI / 2), material)
  scene.add(ground)

  // --- temperature number through the haze ---
  const textGroup = new THREE.Group()
  textGroup.position.set(2.7, 1.55, 0)

  const geometry = new TextGeometry(tempText, {
    font,
    size: 1.15,
    depth: 0.22,
    curveSegments: 14,
    bevelEnabled: true,
    bevelThickness: 0.03,
    bevelSize: 0.022,
    bevelSegments: 4,
  })
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  geometry.translate(-(box.max.x - box.min.x) / 2, -(box.max.y - box.min.y) / 2, 0)

  // black inverted-hull outline: a slightly larger copy drawn back-faces-only,
  // sitting just behind the white glyphs so only a dark rim shows — gives the
  // number a crisp edge against the pale fog
  const outlineMaterial = new THREE.MeshBasicNodeMaterial({ color: 0x0a0d0e, side: THREE.BackSide })
  const outline = new THREE.Mesh(geometry, outlineMaterial)
  outline.rotation.y = -0.12
  outline.scale.setScalar(1.07)
  outline.position.z = -0.03
  outline.renderOrder = 0
  textGroup.add(outline)

  // bright, unlit number — the scattering pass blurs + fogs it into the haze
  // automatically as it travels back in Z
  const textMaterial = new THREE.MeshBasicNodeMaterial({ color: 0xf4f7f7 })
  const text = new THREE.Mesh(geometry, textMaterial)
  text.rotation.y = -0.12
  text.renderOrder = 1
  textGroup.add(text)

  scene.add(textGroup)

  // --- scattering pipeline (the heart of the effect) ---
  const renderPipeline = new THREE.RenderPipeline(renderer)

  const density = reference('density', 'float', scene.fog)
  const scatteringUniform = uniform(SCATTERING)

  const scenePass = pass(scene, camera)
  const scenePassColor = scenePass.getTextureNode('output')
  const scenePassViewZ = scenePass.getViewZNode()

  const sceneColorBlurred = gaussianBlur(scenePassColor, vec2(scatteringUniform), 4, { resolutionScale: 0.5 })

  const fogFactor = densityFogFactor(density).context({ getViewZ: () => scenePassViewZ })
  renderPipeline.outputNode = mix(scenePassColor, sceneColorBlurred, fogFactor)

  function frame(t) {
    // float forward/back through the fog: near + clear -> deep + dissolved, on a
    // slow eased cycle, with a gentle lateral + vertical drift so it truly "floats"
    const cycle = Math.sin(t * 0.45) * 0.5 + 0.5 // 0..1, eased
    textGroup.position.z = TEXT_BASE + cycle * TEXT_TRAVEL
    textGroup.position.x = 2.7 + Math.sin(t * 0.33) * 0.18
    textGroup.position.y = 1.55 + Math.sin(t * 0.6) * 0.12
    textGroup.rotation.y = -0.12 + Math.sin(t * 0.22) * 0.06
    textGroup.rotation.x = Math.sin(t * 0.27) * 0.03

    // slow lateral pan + breathing dolly so the pines drift through the haze
    camera.position.x = 0.4 + Math.sin(t * 0.08) * 0.55
    camera.position.y = 1.62 + Math.sin(t * 0.11) * 0.07
    camera.position.z = 8 + Math.sin(t * 0.05) * 0.7
    camera.lookAt(0.3 + Math.sin(t * 0.06) * 0.2, 1.5, -6)
  }

  const render = () => renderPipeline.render()

  return { scene, camera, frame, render }
}
