import type { NextConfig } from "next";

const config: NextConfig = {
  experimental: {
    // Streaming + improved error UX on the App Router.
    reactCompiler: false,
  },
  // Loosen the build so an API that's temporarily offline doesn't block it.
  eslint: {
    ignoreDuringBuilds: false,
  },
};

export default config;
