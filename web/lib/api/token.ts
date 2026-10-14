import type { TokenPair } from "./types";

const REFRESH_KEY = "slm.refresh_token";

/** Decode the (unverified) payload of a JWT. The server remains the authority. */
export function decodeJwt<T extends Record<string, unknown> = Record<string, unknown>>(token: string): T | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const bin = atob(b64);
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes)) as T;
  } catch {
    return null;
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

/**
 * Holds the access JWT in memory only (never persisted) and the opaque refresh token in
 * localStorage so a page reload can silently re-authenticate.
 */
export class TokenStore {
  private access: string | null = null;
  private expiresAt = 0;
  private refreshToken: string | null = null;
  private listeners = new Set<() => void>();

  constructor(private readonly persist = true) {
    if (persist) this.refreshToken = safeStorage()?.getItem(REFRESH_KEY) ?? null;
  }

  getAccessToken(): string | null {
    return this.access;
  }
  getRefreshToken(): string | null {
    return this.refreshToken;
  }
  /** true when the access token is missing or within `skewMs` of expiring */
  isExpiring(skewMs = 15_000): boolean {
    return !this.access || Date.now() + skewMs >= this.expiresAt;
  }

  set(pair: TokenPair): void {
    this.access = pair.access_token;
    this.expiresAt = Date.now() + pair.expires_in * 1000;
    if (pair.refresh_token) {
      this.refreshToken = pair.refresh_token;
      if (this.persist) {
        try {
          safeStorage()?.setItem(REFRESH_KEY, pair.refresh_token);
        } catch {
          /* storage unavailable: session lives for this tab only */
        }
      }
    }
    this.emit();
  }

  clear(): void {
    this.access = null;
    this.expiresAt = 0;
    this.refreshToken = null;
    if (this.persist) {
      try {
        safeStorage()?.removeItem(REFRESH_KEY);
      } catch {
        /* ignore */
      }
    }
    this.emit();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    this.listeners.forEach((l) => l());
  }
}
