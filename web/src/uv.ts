/* Box-projected UVs for non-indexed triangle soups in millimetres.
   Each triangle is projected on the axis plane its normal faces most, so a
   texture with real-world size `repeatMm` lands at true scale on every face. */

export function boxProjectUVs(pos: ArrayLike<number>, repeatMm: number): Float32Array {
  const verts = Math.floor(pos.length / 3);
  const uv = new Float32Array(verts * 2);
  for (let t = 0; t + 2 < verts; t += 3) {
    const a = t * 3;
    const ux = pos[a + 3] - pos[a];
    const uy = pos[a + 4] - pos[a + 1];
    const uz = pos[a + 5] - pos[a + 2];
    const vx = pos[a + 6] - pos[a];
    const vy = pos[a + 7] - pos[a + 1];
    const vz = pos[a + 8] - pos[a + 2];
    const nx = Math.abs(uy * vz - uz * vy);
    const ny = Math.abs(uz * vx - ux * vz);
    const nz = Math.abs(ux * vy - uy * vx);
    for (let k = 0; k < 3; k++) {
      const p = (t + k) * 3;
      const x = pos[p];
      const y = pos[p + 1];
      const z = pos[p + 2];
      let u: number;
      let w: number;
      if (nx >= ny && nx >= nz) {
        u = z; // faces +-x: the plan length runs along the face
        w = y;
      } else if (ny >= nz) {
        u = x; // faces +-y: floors and tops
        w = z;
      } else {
        u = x; // faces +-z: the section face
        w = y;
      }
      uv[(t + k) * 2] = u / repeatMm;
      uv[(t + k) * 2 + 1] = w / repeatMm;
    }
  }
  return uv;
}
