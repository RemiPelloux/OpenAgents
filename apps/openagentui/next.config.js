/** @type {import('next').NextConfig} */
const DASHBOARD_ORIGIN = process.env.OPENAGENTUI_DASHBOARD_ORIGIN || "http://127.0.0.1:9119";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        // Proxies to the FastAPI routes mounted on the existing OpenAgents
        // dashboard server (openagents_cli/openagentui_server.py). No
        // separate backend/auth is introduced — this frontend is purely a
        // canvas over that server's loopback-token-gated API.
        source: "/api/openagentui/:path*",
        destination: `${DASHBOARD_ORIGIN}/api/openagentui/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
