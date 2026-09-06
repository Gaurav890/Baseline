import type { NextConfig } from "next";

if (process.env.NODE_ENV === "production") {
  const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!configuredApiUrl) {
    throw new Error("NEXT_PUBLIC_API_URL is required for a production frontend build");
  }
  const parsedApiUrl = new URL(configuredApiUrl);
  const localHost = parsedApiUrl.hostname === "localhost" || parsedApiUrl.hostname === "127.0.0.1";
  if (parsedApiUrl.protocol !== "https:" && !(localHost && parsedApiUrl.protocol === "http:")) {
    throw new Error("NEXT_PUBLIC_API_URL must use HTTPS outside local development");
  }
}

const nextConfig: NextConfig = {
  agentRules: false,
  output: "standalone",
};

export default nextConfig;
