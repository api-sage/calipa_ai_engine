import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The browser tests build into their own folder so they can run beside `pnpm dev`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
