// Foggy banner scene. Ported from the standalone WebGPU/TSL prototype to the
// engine contract: build(ctx) -> { scene, camera, frame, render }. Shallow-fog
// preset. Mirrors the `webgpu_custom_fog_scattering` technique:
//   1. render flat-black silhouette pines into a bright, cool exponential fog
//   2. blur the scene pass (downsampled gaussian)
//   3. composite mix(sharp, blurred, densityFogFactor(viewZ))
// The blur of bright haze bleeding around dark silhouettes *is* the scattering;
// pixels deeper in the fog get the blurred version. The temperature is a dark
// silhouette in the left clearing, softly hazed by the same pass at its depth.

import * as THREE from 'three/webgpu'
import { densityFogFactor, mix, pass, reference, uniform, vec2 } from 'three/tsl'
import { gaussianBlur } from 'three/addons/tsl/display/GaussianBlurNode.js'
import { TreeGenerator } from 'three/addons/generators/TreeGenerator.js'
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js'
import type { SceneContext, SceneHandle } from '../engine.ts'

const FOG_COLOR = 0xc6cace // bright cool haze; background matches so trunks read as silhouettes

// shallow preset (the default the prototype starts with), baked in
const FOG_DENSITY = 0.085
const SCATTERING = 1.6

export async function build(ctx: SceneContext): Promise<SceneHandle> {
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
  const variants: THREE.BufferGeometry[] = []
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
  const placements: THREE.Matrix4[][] = variants.map(() => [])
  const dummy = new THREE.Object3D()
  const cols = 9
  const rows = 7
  const spacing = 1.9
  for (let i = 0; i < cols; i += 1) {
    for (let j = 0; j < rows; j += 1) {
      const x = (i - cols / 2) * spacing + (Math.random() - 0.5) * spacing * 0.7
      const z = j * spacing - rows * spacing + 2.4 + (Math.random() - 0.5) * spacing * 0.7
      // carve a clearing on the LEFT for the temperature number; the forest
      // masses to the right and gets cropped by the frame
      if (x < -0.5 && z > -5.5) continue
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

  // a couple of dominant near trunks to anchor depth on the right
  ;[[1.8, 3.6, 1.2, 1.1], [3.6, 1.8, 1.0, 0.3]].forEach(([x, z, s, ry]) => {
    const hero = new THREE.Mesh(variants[variants.length - 1], material)
    hero.position.set(x, 0, z)
    hero.rotation.y = ry
    hero.scale.setScalar(s)
    scene.add(hero)
  })

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(600, 600).rotateX(-Math.PI / 2), material)
  scene.add(ground)

  // --- temperature number in the haze ---
  // A dark silhouette like the pines — it belongs to the scene's own language and
  // needs no outline against the pale fog. Slightly fogged/blurred by the
  // scattering pass at its depth, which reads as atmosphere. Unit-size glyphs,
  // left edge at x=0 and vertically centred; frame() scales to half panel height.
  const textGroup = new THREE.Group()
  textGroup.position.set(-4.5, 1.57, 2.0)

  const geometry = new TextGeometry(tempText, {
    font, size: 1, depth: 0.14, curveSegments: 14,
    bevelEnabled: true, bevelThickness: 0.025, bevelSize: 0.018, bevelSegments: 4,
  })
  geometry.computeBoundingBox()
  const bb = geometry.boundingBox
  // Non-empty text always measures; no box means the font failed to load glyphs,
  // and the banner falls back to the static gradient rather than render nothing.
  if (!bb) throw new Error('no-glyph-bounds')
  geometry.translate(-bb.min.x, -(bb.max.y + bb.min.y) / 2, 0)
  const glyphH = bb.max.y - bb.min.y
  const glyphW = bb.max.x - bb.min.x

  const textMaterial = new THREE.MeshBasicNodeMaterial({ color: 0x111416 })
  const text = new THREE.Mesh(geometry, textMaterial)
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

  function frame(t: number) {
    // slow lateral pan so the pines drift through the haze; lookAt tracks
    // camera.x (pure translation) and z stays fixed so the steady temperature
    // doesn't breathe with a dolly
    camera.position.x = 0.4 + Math.sin(t * 0.08) * 0.4
    camera.position.y = 1.62 + Math.sin(t * 0.11) * 0.05
    camera.lookAt(camera.position.x - 0.1, 1.5, -6)

    // temperature: vertically centred on the view, half the panel height, steady,
    // centred in the clearing between the left frame edge and the forest (x≈-0.5).
    // camera.zoom read at frame time — the engine applies its zoom after build().
    const k = Math.tan((camera.fov * Math.PI) / 360) / camera.zoom
    const halfHT = k * (camera.position.z - textGroup.position.z)
    const scaleT = halfHT / glyphH
    const leftEdge = camera.position.x - halfHT * camera.aspect
    textGroup.scale.setScalar(scaleT)
    textGroup.position.x = (leftEdge - 0.5) / 2 - (glyphW * scaleT) / 2
    textGroup.position.y = 1.57 // the view centre height at the glyph's depth
  }

  const render = () => renderPipeline.render()

  return { scene, camera, frame, render }
}
