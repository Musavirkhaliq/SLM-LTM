import { API_BASE, USE_MOCKS } from "@/lib/config";
import { ApiClient, type FetchLike } from "./client";
import { createResources } from "./resources";

/**
 * In mock mode every request is answered in-process by `lib/mocks/fetch` (lazy-loaded so it is
 * not part of the bundle's critical path); otherwise requests go to the gateway at API_BASE.
 */
const fetchImpl: FetchLike = USE_MOCKS
  ? async (input, init) => (await import("@/lib/mocks/fetch")).mockFetch(input, init)
  : (input, init) => fetch(input, init);

export const apiClient = new ApiClient({ baseUrl: USE_MOCKS ? "" : API_BASE, fetchImpl });
export const api = createResources(apiClient);

export { ApiError } from "./client";
export * from "./types";
