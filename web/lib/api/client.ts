import { parseChatStream } from "./sse";
import { TokenStore } from "./token";
import type { ApiProblem, ChatCompletionChunk, ChatCompletionRequest, TokenPair } from "./types";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly problem?: ApiProblem;
  constructor(status: number, message: string, problem?: ApiProblem, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
    this.code = code;
  }
}

export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  form?: FormData;
  headers?: Record<string, string>;
  /** attach the bearer token (default true) */
  auth?: boolean;
  signal?: AbortSignal;
  /** return the raw Response instead of parsing JSON */
  raw?: boolean;
}

export interface ApiClientOptions {
  baseUrl: string;
  fetchImpl: FetchLike;
  tokens?: TokenStore;
  /** called when refresh fails and the session is no longer valid */
  onUnauthorized?: () => void;
}

export function buildQuery(query?: RequestOptions["query"]): string {
  if (!query) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function toApiError(res: Response): Promise<ApiError> {
  let problem: ApiProblem | undefined;
  try {
    problem = (await res.clone().json()) as ApiProblem;
  } catch {
    /* non-JSON body */
  }
  // control plane: RFC 7807; /v1: OpenAI-shaped
  const message =
    problem?.detail ?? problem?.title ?? problem?.error?.message ?? (res.statusText || `Request failed (${res.status})`);
  return new ApiError(res.status, message, problem, problem?.error?.code);
}

export class ApiClient {
  readonly tokens: TokenStore;
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;
  onUnauthorized?: () => void;
  private refreshing: Promise<boolean> | null = null;

  constructor(opts: ApiClientOptions) {
    this.baseUrl = opts.baseUrl;
    this.fetchImpl = opts.fetchImpl;
    this.tokens = opts.tokens ?? new TokenStore();
    this.onUnauthorized = opts.onUnauthorized;
  }

  /** Single-flight refresh: concurrent 401s share one refresh call (refresh tokens rotate). */
  refresh(): Promise<boolean> {
    if (this.refreshing) return this.refreshing;
    const rt = this.tokens.getRefreshToken();
    if (!rt) return Promise.resolve(false);
    this.refreshing = (async () => {
      try {
        const res = await this.fetchImpl(`${this.baseUrl}/api/v1/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ refresh_token: rt }),
        });
        if (!res.ok) {
          this.tokens.clear();
          return false;
        }
        this.tokens.set((await res.json()) as TokenPair);
        return true;
      } catch {
        return false;
      } finally {
        this.refreshing = null;
      }
    })();
    return this.refreshing;
  }

  private async send(method: string, path: string, opts: RequestOptions, retry: boolean): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
    const auth = opts.auth !== false;
    if (auth) {
      if (this.tokens.getAccessToken() && this.tokens.isExpiring() && this.tokens.getRefreshToken()) {
        await this.refresh();
      }
      const token = this.tokens.getAccessToken();
      if (token) headers.Authorization = `Bearer ${token}`;
    }
    let body: BodyInit | undefined;
    if (opts.form) {
      body = opts.form; // browser sets the multipart boundary
    } else if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    const res = await this.fetchImpl(`${this.baseUrl}${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      body,
      signal: opts.signal,
    });
    if (res.status === 401 && auth && retry && this.tokens.getRefreshToken()) {
      if (await this.refresh()) return this.send(method, path, opts, false);
      this.onUnauthorized?.();
    } else if (res.status === 401 && auth && !this.tokens.getRefreshToken()) {
      this.onUnauthorized?.();
    }
    return res;
  }

  async request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const res = await this.send(method, path, opts, true);
    if (!res.ok) throw await toApiError(res);
    if (opts.raw) return res as unknown as T;
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  get<T>(path: string, opts?: RequestOptions) {
    return this.request<T>("GET", path, opts);
  }
  post<T>(path: string, body?: unknown, opts?: RequestOptions) {
    return this.request<T>("POST", path, { ...opts, body });
  }
  put<T>(path: string, body?: unknown, opts?: RequestOptions) {
    return this.request<T>("PUT", path, { ...opts, body });
  }
  patch<T>(path: string, body?: unknown, opts?: RequestOptions) {
    return this.request<T>("PATCH", path, { ...opts, body });
  }
  delete<T>(path: string, opts?: RequestOptions) {
    return this.request<T>("DELETE", path, opts);
  }

  /** Stream `/v1/chat/completions` (OpenAI SSE). `apiKey` overrides the session JWT. */
  async *chatStream(
    req: ChatCompletionRequest,
    opts: { signal?: AbortSignal; apiKey?: string } = {},
  ): AsyncGenerator<ChatCompletionChunk, void, void> {
    const res = await this.send(
      "POST",
      "/v1/chat/completions",
      {
        body: { ...req, stream: true, stream_options: { include_usage: true } },
        signal: opts.signal,
        headers: { Accept: "text/event-stream", ...(opts.apiKey ? { Authorization: `Bearer ${opts.apiKey}` } : {}) },
        auth: !opts.apiKey,
      },
      true,
    );
    if (!res.ok) throw await toApiError(res);
    if (!res.body) throw new ApiError(res.status, "Streaming is not supported by this response");
    yield* parseChatStream(res.body);
  }
}
