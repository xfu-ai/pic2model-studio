const align4 = (value: number) => (value + 3) & ~3;

const cubePositions = new Float32Array([
  -0.5, -0.5, 0.5, 0.5, -0.5, 0.5, 0.5, 0.5, 0.5, -0.5, 0.5, 0.5,
  0.5, -0.5, -0.5, -0.5, -0.5, -0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5,
  0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, 0.5, 0.5, 0.5,
  -0.5, -0.5, -0.5, -0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, 0.5, -0.5,
  -0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, -0.5, -0.5, 0.5, -0.5,
  -0.5, -0.5, -0.5, 0.5, -0.5, -0.5, 0.5, -0.5, 0.5, -0.5, -0.5, 0.5,
]);

const cubeNormals = new Float32Array([
  0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1,
  0, 0, -1, 0, 0, -1, 0, 0, -1, 0, 0, -1,
  1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0,
  -1, 0, 0, -1, 0, 0, -1, 0, 0, -1, 0, 0,
  0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0,
  0, -1, 0, 0, -1, 0, 0, -1, 0, 0, -1, 0,
]);

const cubeIndices = new Uint16Array([
  0, 1, 2, 0, 2, 3,
  4, 5, 6, 4, 6, 7,
  8, 9, 10, 8, 10, 11,
  12, 13, 14, 12, 14, 15,
  16, 17, 18, 16, 18, 19,
  20, 21, 22, 20, 22, 23,
]);

function createDefaultPreviewModelGlb() {
  const positionOffset = 0;
  const normalOffset = positionOffset + cubePositions.byteLength;
  const indexOffset = normalOffset + cubeNormals.byteLength;
  const binaryLength = align4(indexOffset + cubeIndices.byteLength);
  const binary = new Uint8Array(binaryLength);
  binary.set(new Uint8Array(cubePositions.buffer), positionOffset);
  binary.set(new Uint8Array(cubeNormals.buffer), normalOffset);
  binary.set(new Uint8Array(cubeIndices.buffer), indexOffset);

  const primitive = (material: number) => ({
    attributes: { POSITION: 0, NORMAL: 1 },
    indices: 2,
    material,
    mode: 4,
  });
  const gltf = {
    asset: {
      version: "2.0",
      generator: "Pic2Model Studio procedural asset beacon",
      copyright: "Pic2Model Studio project",
    },
    scene: 0,
    scenes: [{ name: "Asset Beacon Preview", nodes: [0] }],
    nodes: [
      { name: "Asset Beacon", children: [1, 2, 3, 4, 5, 6, 7, 8, 9] },
      { name: "Foundation", mesh: 1, translation: [0, -0.92, 0], scale: [1.35, 0.14, 0.9] },
      { name: "Amber Plinth", mesh: 0, translation: [0, -0.73, 0], scale: [0.95, 0.11, 0.62] },
      { name: "Left Arch", mesh: 0, translation: [-0.42, 0.02, 0], rotation: [0, 0, -0.156434, 0.987688], scale: [0.2, 1.12, 0.24] },
      { name: "Right Arch", mesh: 0, translation: [0.42, 0.02, 0], rotation: [0, 0, 0.156434, 0.987688], scale: [0.2, 1.12, 0.24] },
      { name: "Portal Bridge", mesh: 1, translation: [0, -0.08, 0], scale: [0.62, 0.13, 0.3] },
      { name: "Signal Core", mesh: 2, translation: [0, 1.02, 0], rotation: [0, 0, 0.382683, 0.92388], scale: [0.34, 0.34, 0.34] },
      { name: "Rear Spine", mesh: 2, translation: [0, 0.05, -0.38], scale: [0.12, 0.74, 0.12] },
      { name: "Left Foot", mesh: 1, translation: [-0.9, -0.67, 0], scale: [0.28, 0.18, 0.42] },
      { name: "Right Foot", mesh: 1, translation: [0.9, -0.67, 0], scale: [0.28, 0.18, 0.42] },
    ],
    meshes: [
      { name: "Amber Alloy", primitives: [primitive(0)] },
      { name: "Graphite Structure", primitives: [primitive(1)] },
      { name: "Mint Signal", primitives: [primitive(2)] },
    ],
    materials: [
      {
        name: "Amber Alloy",
        pbrMetallicRoughness: {
          baseColorFactor: [0.82, 0.48, 0.1, 1],
          metallicFactor: 0.58,
          roughnessFactor: 0.3,
        },
      },
      {
        name: "Graphite Structure",
        pbrMetallicRoughness: {
          baseColorFactor: [0.12, 0.1, 0.08, 1],
          metallicFactor: 0.72,
          roughnessFactor: 0.42,
        },
      },
      {
        name: "Mint Signal",
        emissiveFactor: [0.03, 0.35, 0.24],
        pbrMetallicRoughness: {
          baseColorFactor: [0.1, 0.72, 0.52, 1],
          metallicFactor: 0.22,
          roughnessFactor: 0.24,
        },
      },
    ],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 24,
        type: "VEC3",
        min: [-0.5, -0.5, -0.5],
        max: [0.5, 0.5, 0.5],
      },
      { bufferView: 1, componentType: 5126, count: 24, type: "VEC3" },
      { bufferView: 2, componentType: 5123, count: 36, type: "SCALAR" },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: positionOffset, byteLength: cubePositions.byteLength, target: 34962 },
      { buffer: 0, byteOffset: normalOffset, byteLength: cubeNormals.byteLength, target: 34962 },
      { buffer: 0, byteOffset: indexOffset, byteLength: cubeIndices.byteLength, target: 34963 },
    ],
    buffers: [{ byteLength: binaryLength }],
  };

  const json = new TextEncoder().encode(JSON.stringify(gltf));
  const jsonLength = align4(json.byteLength);
  const totalLength = 12 + 8 + jsonLength + 8 + binaryLength;
  const glb = new Uint8Array(totalLength);
  const view = new DataView(glb.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, totalLength, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  glb.fill(0x20, 20, 20 + jsonLength);
  glb.set(json, 20);
  const binaryHeader = 20 + jsonLength;
  view.setUint32(binaryHeader, binaryLength, true);
  view.setUint32(binaryHeader + 4, 0x004e4942, true);
  glb.set(binary, binaryHeader + 8);
  return glb;
}

export function localDefaultPreviewModelUrl() {
  return URL.createObjectURL(
    new Blob([createDefaultPreviewModelGlb()], { type: "model/gltf-binary" }),
  );
}
