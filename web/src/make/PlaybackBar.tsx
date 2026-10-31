import type { JSX } from "react";

export default function PlaybackBar({
  playing, label, progress, count, onToggle, onScrub,
}: {
  playing: boolean;
  label: string;
  progress: number;
  count: number;
  onToggle: () => void;
  onScrub: (progress: number) => void;
}): JSX.Element {
  return (
    <div className="make-play">
      <button type="button" className="primary" data-make="play" disabled={!count} onClick={onToggle}>
        {playing ? "Pause" : "Play"}
      </button>
      <input
        data-make="scrub"
        type="range"
        min={0}
        max={count || 1}
        step={0.01}
        value={Math.min(progress, count || 1)}
        disabled={!count}
        aria-label="Install sequence"
        onChange={(event) => onScrub(Number(event.target.value))}
      />
      <span className="make-step" data-make="step">{label}</span>
    </div>
  );
}
