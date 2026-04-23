import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
  // Monorepo root — silences Next's multiple-lockfiles warning.
  outputFileTracingRoot: path.join(__dirname, "../.."),
  env: {
    API_BASE: process.env.API_BASE ?? "http://localhost:8000",
  },
};

export default nextConfig;
