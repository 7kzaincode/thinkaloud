import path from "path";
import type { NextConfig } from "next";

const config: NextConfig = {
  // The desktop app ships the standalone server (desktop/ packages .next/standalone).
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname),
  // Local-only review tool: screenshots are served by our own API route.
  images: { unoptimized: true },
  devIndicators: false,
  agentRules: false,
};

export default config;
