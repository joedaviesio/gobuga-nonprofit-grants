import type { NextConfig } from "next";

const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8102";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      // The two setup screens were folded into the one sign-up screen at /seed.
      // Kept so bookmarks and old links still land somewhere.
      { source: "/setup", destination: "/seed", permanent: false },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
