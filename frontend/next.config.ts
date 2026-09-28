import type { NextConfig } from "next";

const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8102";

// Every public path in the backend's URL scheme (GET /api/v1 `url_scheme`,
// defined in api/public_v1.py URL_SCHEME), mapped to its backend route. Next
// forwards the query string unchanged. `:id` and `:slug` stop at the literal
// `.json` suffix; each mapping is checked by scripts/verify_public_surface.py.
const PUBLIC_TWINS: { source: string; destination: string }[] = [
  { source: "/grants/:id.json", destination: "/api/v1/opportunities/:id" },
  { source: "/grants.json", destination: "/api/v1/opportunities" },
  { source: "/fit.json", destination: "/api/v1/fit" },
  { source: "/fit/feed.xml", destination: "/api/v1/fit/feed.xml" },
  { source: "/funders.json", destination: "/api/v1/funders" },
  { source: "/funders/:slug.json", destination: "/api/v1/funders/:slug" },
  { source: "/changes.json", destination: "/api/v1/changes" },
  { source: "/changes/feed.xml", destination: "/api/v1/changes/feed.xml" },
  { source: "/stats.json", destination: "/api/v1/stats" },
  { source: "/stats/:month.json", destination: "/api/v1/stats/:month" },
  { source: "/out/:id", destination: "/out/:id" },
  // The MCP endpoint (streamable HTTP), served by the backend.
  { source: "/mcp", destination: "/mcp" },
];

const nextConfig: NextConfig = {
  // Next streams a dynamic page's <title>, meta and links into the <body>
  // for every user agent not on its list of "HTML-limited bots", which leaves
  // out curl, GPTBot, ClaudeBot and PerplexityBot. Matching every user agent
  // puts the metadata in <head> for everyone, so every reader gets the same
  // document (doctrine 5).
  htmlLimitedBots: /.*/,
  async redirects() {
    return [
      // The two setup screens were folded into the one sign-up screen at /seed.
      // Kept so bookmarks and old links still land somewhere.
      { source: "/setup", destination: "/seed", permanent: false },
    ];
  },
  async rewrites() {
    return {
      // Before the app's own routes, so no public page can shadow a twin.
      beforeFiles: PUBLIC_TWINS.map(({ source, destination }) => ({
        source,
        destination: `${apiUrl}${destination}`,
      })),
      afterFiles: [
        {
          source: "/api/:path*",
          destination: `${apiUrl}/api/:path*`,
        },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
