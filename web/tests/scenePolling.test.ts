import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import { test } from "node:test";
import { watchScene } from "../src/scenePolling.ts";

test("an initial network failure retries and clears its message on recovery", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const fetch = t.mock.method(globalThis, "fetch", async () => {
    if (calls++ === 0) throw new TypeError("Failed to fetch");
    return Response.json({ state: "ready" });
  });
  const scenes: string[] = [];
  const errors: (string | null)[] = [];
  const stop = watchScene("/scene", (scene) => scenes.push(scene.state), (error) => errors.push(error));
  t.after(stop);
  await setImmediate();
  assert.deepEqual(errors, ["Connection lost. Reconnecting…"]);
  t.mock.timers.tick(2000);
  await setImmediate();
  assert.deepEqual(scenes, ["ready"]);
  assert.equal(errors.at(-1), null);
  t.mock.timers.tick(10000);
  assert.equal(fetch.mock.callCount(), 2);
});

test("a temporary server failure during a build preserves the scene and resumes polling", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const replies = [Response.json({ state: "working" }), new Response(null, { status: 503 }), Response.json({ state: "ready" })];
  t.mock.method(globalThis, "fetch", async () => replies.shift()!);
  const scenes: string[] = [];
  const errors: (string | null)[] = [];
  t.after(watchScene("/scene", (scene) => scenes.push(scene.state), (error) => errors.push(error)));
  await setImmediate();
  t.mock.timers.tick(2000);
  await setImmediate();
  assert.deepEqual(scenes, ["working"]);
  assert.equal(errors.at(-1), "Connection lost. Reconnecting…");
  t.mock.timers.tick(2000);
  await setImmediate();
  assert.deepEqual(scenes, ["working", "ready"]);
  assert.equal(errors.at(-1), null);
});

test("a missing scene stops retrying", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response(null, { status: 404 }));
  const errors: (string | null)[] = [];
  t.after(watchScene("/scene", () => assert.fail("missing scene was published"), (error) => errors.push(error)));
  await setImmediate();
  t.mock.timers.tick(10000);
  assert.deepEqual(errors, ["This scene could not be found"]);
  assert.equal(fetch.mock.callCount(), 1);
});

test("reads do not overlap and unmount cancels the request and ignores a late response", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let resolve!: (response: Response) => void;
  let signal: AbortSignal | undefined;
  const fetch = t.mock.method(globalThis, "fetch", (_url, options) => {
    signal = options.signal;
    return new Promise<Response>((done) => { resolve = done; });
  });
  const stop = watchScene("/scene", () => assert.fail("stale scene was published"), () => assert.fail("stale error was published"));
  t.mock.timers.tick(10000);
  assert.equal(fetch.mock.callCount(), 1);
  stop();
  assert.equal(signal?.aborted, true);
  resolve(Response.json({ state: "working" }));
  await setImmediate();
  t.mock.timers.tick(10000);
  assert.equal(fetch.mock.callCount(), 1);
});
