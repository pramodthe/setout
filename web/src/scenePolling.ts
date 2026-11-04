import type { Scene } from "./scene";

/* Retry reads after a lost connection; never retry an upload or an edit. */
export function watchScene(url: string, onScene: (scene: Scene) => void, onError: (message: string | null) => void): () => void {
  const controller = new AbortController();
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const poll = async () => {
    let again = true;
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (stopped) return;
      if (response.status === 404) {
        again = false;
        onError("This scene could not be found");
        return;
      }
      if (!response.ok) throw new Error("Scene unavailable");
      const scene = await response.json() as Scene;
      if (stopped) return;
      onScene(scene);
      onError(null);
      again = scene.state === "working";
    } catch {
      if (!stopped) onError("Connection lost. Reconnecting…");
    } finally {
      if (!stopped && again) timer = setTimeout(() => void poll(), 2000);
    }
  };
  void poll();
  return () => { stopped = true; clearTimeout(timer); controller.abort(); };
}
