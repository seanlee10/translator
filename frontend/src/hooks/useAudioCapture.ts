import { useCallback, useEffect, useRef, useState } from "react";

export type ChunkHandler = (pcm: ArrayBuffer, sampleRate: number) => void;

export function useAudioCapture() {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(false);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  // True from the moment start() is entered until stop() (or a failed
  // start's cleanup) runs. Guards against concurrent/duplicate start()
  // calls; a ref is used instead of `active` because `active` is async
  // state and two rapid clicks can both observe active === false before
  // either setActive(true) has committed.
  const capturingRef = useRef(false);

  const refreshDevices = useCallback(async () => {
    const all = await navigator.mediaDevices.enumerateDevices();
    setDevices(all.filter((d) => d.kind === "audioinput"));
  }, []);

  // Releases whatever resources are currently held, in reverse-acquisition
  // order. Shared by stop() and start()'s failure path so a partial
  // start (e.g. addModule() 404s) can never leave the mic live and
  // unstoppable — both paths tear down identically and cannot drift.
  const release = useCallback(() => {
    if (nodeRef.current) {
      // Detach the handler before disconnecting: a message already queued
      // from the worklet can otherwise still fire onmessage with a stale
      // onChunk closure after teardown has started.
      nodeRef.current.port.onmessage = null;
      nodeRef.current.disconnect();
      nodeRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void ctxRef.current?.close();
    ctxRef.current = null;
  }, []);

  const start = useCallback(
    async (onChunk: ChunkHandler) => {
      if (capturingRef.current) return; // already active or a start is in flight
      capturingRef.current = true;
      setError(null);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            echoCancellation: true,
            noiseSuppression: true,
            channelCount: 1,
          },
        });
        streamRef.current = stream;
        await refreshDevices(); // labels populate only after permission

        // Browsers may ignore the requested rate; read back what we got.
        const ctx = new AudioContext({ sampleRate: 16000 });
        ctxRef.current = ctx;
        await ctx.audioWorklet.addModule("/pcm-worklet.js");

        const node = new AudioWorkletNode(ctx, "pcm-worklet");
        nodeRef.current = node;
        node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
          onChunk(event.data, ctx.sampleRate);
        };

        ctx.createMediaStreamSource(stream).connect(node);
        setActive(true);
      } catch (e) {
        // A partial acquisition (e.g. getUserMedia succeeded but
        // addModule() 404s) must not leave the mic live and unstoppable.
        release();
        capturingRef.current = false;
        const message =
          e instanceof DOMException && e.name === "NotAllowedError"
            ? "Microphone permission denied. Allow access in your browser's site settings and try again."
            : `Could not start audio capture: ${String(e)}`;
        setError(message);
        setActive(false);
      }
    },
    [deviceId, refreshDevices, release],
  );

  const stop = useCallback(() => {
    release();
    capturingRef.current = false;
    setActive(false);
  }, [release]);

  useEffect(() => {
    void refreshDevices();
    return stop;
  }, [refreshDevices, stop]);

  return { start, stop, devices, deviceId, setDeviceId, error, active };
}
