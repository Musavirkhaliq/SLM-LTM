/** Small deterministic helpers so the seeded mock data is stable between reloads. */

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Deterministic pseudo-random in [0,1) for an integer key (stateless). */
export function hash01(n: number, salt = 0): number {
  return mulberry32(Math.floor(n) * 2654435761 + salt)();
}

/** Deterministic UUID-shaped id: kind selects a namespace, n is the sequence. */
export function mkId(kind: number, n: number): string {
  const hex = (v: number, len: number) => v.toString(16).padStart(len, "0");
  return `${hex(kind, 8)}-0000-4000-8000-${hex(n, 12)}`;
}

let liveCounter = 1000;
/** Fresh id for entities created at runtime. */
export function liveId(kind: number): string {
  return mkId(kind, ++liveCounter);
}

const ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
export function randomString(len: number): string {
  let out = "";
  const buf = new Uint32Array(len);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(buf);
  else for (let i = 0; i < len; i++) buf[i] = Math.floor(Math.random() * 4294967295);
  for (let i = 0; i < len; i++) out += ALNUM[buf[i]! % ALNUM.length];
  return out;
}

export const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
