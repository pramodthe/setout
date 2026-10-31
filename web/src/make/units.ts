/** Blender is Z-up and authors millimetres. glTF and three.js are Y-up metres. */

export function zUpMmToYUpMetres(x: number, y: number, z: number): [number, number, number] {
  return [x / 1000, z / 1000, -y / 1000];
}

/** A scene already exported Y-up in metres stays under a few metres across.
    A file still in millimetres is tens or hundreds of units across. */
export function needsZUpMmConversion(maxAbs: number): boolean {
  return maxAbs > 20;
}
