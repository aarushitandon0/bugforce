/** @type {import('next').NextConfig} */
const nextConfig = {
  // Every screen fetches its data client-side from the BugForge API, so the
  // site is plain static files: Amplify serves them from a zip deploy, no Git
  // connection or SSR compute needed. Routes that carry an id use a query
  // string (/solve/?id=...) because a static export can't know ids at build.
  output: "export",
  trailingSlash: true,
  reactStrictMode: true,
};

export default nextConfig;
