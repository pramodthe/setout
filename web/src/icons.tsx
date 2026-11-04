import type { ReactNode } from "react";

const svg = (path: ReactNode, size = 16) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>;
const CUBE = <><path d="M12 3 4 7.5v9L12 21l8-4.5v-9z" /><path d="M4 7.5 12 12l8-4.5M12 12v9" /></>;
/* A cube with the face you'd be looking at filled in, as CAD programs show their views. */
const viewCube = (face: string | null) => svg(<>{face && <path d={face} fill="currentColor" stroke="none" />}{CUBE}</>, 18);
export const ICONS = {
  cube: svg(CUBE, 15),
  pointer: svg(<path d="m5 3 14 8-6 2-2 6z" />),
  hand: svg(<path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11m0-1V4.5a1.5 1.5 0 0 1 3 0V11m0-3.5a1.5 1.5 0 0 1 3 0V14c0 4-3 7-7 7-3 0-4.500-1.500-6-4l-2-3.500c-1-1.700 1.500-3 2.700-1.300L8 14" />),
  orbit: svg(<><circle cx="12" cy="12" r="2.500" /><path d="M20 8c-1.500-3-4.500-5-8-5a9 9 0 1 0 9 9" /><path d="M20 3v5h-5" /></>),
  iso: viewCube(null),
  isoOther: svg(<g transform="translate(24 0) scale(-1 1)">{CUBE}<path d="M12 12 4 7.5v9l8 4.5z" fill="currentColor" stroke="none" opacity="0.28" /></g>, 18),
  front: viewCube("M4 7.5 12 12v9l-8-4.5z"),
  side: viewCube("M12 12l8-4.5v9L12 21z"),
  top: viewCube("M12 3l8 4.5L12 12 4 7.5z"),
  perspective: svg(<><path d="M4 5.5 15 8.500v7L4 18.500z" /><path d="M15 8.500 20 10v4l-5 1.500" /><path d="M4 12h16" strokeDasharray="1.500 2.500" /></>, 18),
  undo: svg(<><path d="M8 5 3 10l5 5" /><path d="M3 10h11a6 6 0 0 1 0 12h-3" /></>),
  upload: svg(<><path d="M12 16V5M7 9l5-5 5 5" /><path d="M5 20h14" /></>),
  download: svg(<><path d="M12 4v11M7 11l5 5 5-5" /><path d="M5 20h14" /></>),
  panel: svg(<><rect x="3" y="4" width="18" height="16" rx="2.500" /><path d="M15 4v16" /></>),
  send: svg(<path d="M12 19V5M6 11l6-6 6 6" />),
  pencil: svg(<path d="M4 20h4L19 9l-4-4L4 16zM13.500 6.500l4 4" />, 14),
  trash: svg(<path d="M4 7h16M9 7V4h6v3M6.500 7l1 13h9l1-13M10 11v5M14 11v5" />, 15),
  chevron: svg(<path d="m9 6 6 6-6 6" />, 13),
  back: svg(<path d="m15 6-6 6 6 6" />, 14),
  eye: svg(<><path d="M2 12s3.600-7 10-7 10 7 10 7-3.600 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="2.800" /></>, 14),
  eyeOff: svg(<><path d="M3 3l18 18M10.500 5.200A10 10 0 0 1 12 5c6.400 0 10 7 10 7a17 17 0 0 1-3.200 4M6.200 6.300A17 17 0 0 0 2 12s3.600 7 10 7a10 10 0 0 0 4.500-1" /></>, 14),
  clay: svg(<><circle cx="12" cy="12" r="8" /><path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor" stroke="none" /></>),
  play: svg(<path d="M7 5v14l11-7z" />, 14),
  stop: svg(<rect x="6" y="6" width="12" height="12" rx="2" />, 14),
};
