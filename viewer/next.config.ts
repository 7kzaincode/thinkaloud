import type { NextConfig } from "next";

const config: NextConfig = {
  // Local-only review tool: screenshots are served by our own API route.
  images: { unoptimized: true },
  devIndicators: false,
};

export default config;
