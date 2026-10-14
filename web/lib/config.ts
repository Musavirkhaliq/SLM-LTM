/**
 * Runtime configuration. The mock layer is used when NEXT_PUBLIC_USE_MOCKS=true, or when
 * NEXT_PUBLIC_API_BASE is unset (the default in dev) unless USE_MOCKS is explicitly "false".
 */
const rawBase = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim().replace(/\/+$/, "");
const rawMocks = (process.env.NEXT_PUBLIC_USE_MOCKS ?? "").trim().toLowerCase();

export const API_BASE: string = rawBase;
export const USE_MOCKS: boolean = rawMocks === "true" || (rawBase === "" && rawMocks !== "false");

/** Public gateway URL shown in endpoint URLs / code snippets. */
export const PUBLIC_GATEWAY_URL: string = rawBase || "https://api.slm-studio.example";
