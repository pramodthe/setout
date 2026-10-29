import { useEffect, useRef } from "react";
import type { JSX, MutableRefObject, ReactNode } from "react";
import type { Scene } from "./types";
import { Viewport3D } from "./viewport";

interface Props {
  scene: Scene | null;
  visible: Record<string, boolean>;
  explode: number;
  clipOn: boolean;
  clipZ: number;
  seqOn: boolean;
  seqIdx: number;
  selected: string | null;
  onPick: (id: string) => void;
  hud: ReactNode;
  intro?: boolean; // play the build-up animation when a scene loads
  apiRef?: MutableRefObject<Viewport3D | null>; // imperative camera API
}

export default function ViewportView(p: Props): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const vp = useRef<Viewport3D | null>(null);
  const onPick = useRef(p.onPick);
  onPick.current = p.onPick;
  const apiRef = p.apiRef;

  useEffect(() => {
    const v = new Viewport3D(ref.current!, (id) => onPick.current(id));
    vp.current = v;
    if (apiRef) apiRef.current = v;
    return () => {
      v.dispose();
      vp.current = null;
      if (apiRef) apiRef.current = null;
    };
  }, [apiRef]);

  useEffect(() => {
    if (p.scene) vp.current?.loadScene(p.scene, { intro: p.intro });
    else vp.current?.clear();
  }, [p.scene, p.intro]);

  useEffect(() => {
    vp.current?.setState({
      visible: p.visible,
      seqOn: p.seqOn,
      seqIdx: p.seqIdx,
      explode: p.explode,
      clipOn: p.clipOn,
      clipZ: p.clipZ,
    });
  }, [p.visible, p.seqOn, p.seqIdx, p.explode, p.clipOn, p.clipZ, p.scene]);

  useEffect(() => {
    vp.current?.setSelected(p.selected);
  }, [p.selected, p.scene]);

  return (
    <div id="gl" ref={ref}>
      {p.hud}
    </div>
  );
}
