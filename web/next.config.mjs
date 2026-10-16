import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

const gatewayUrl = process.env.GATEWAY_INTERNAL_URL || "http://gateway:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  compress: false,
  outputFileTracingRoot: here,
  experimental: {
    middlewareClientMaxBodySize: "500mb",
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${gatewayUrl}/api/:path*`,
      },
      {
        source: "/v1/:path*",
        destination: `${gatewayUrl}/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
