/**
 * Two modes, one app.
 *
 * Default (deployed): every screen fetches its data client-side from the
 * BugForge API, so the site is plain static files -- Amplify serves them from
 * a zip deploy, no Git connection or SSR compute needed. Routes that carry an
 * id use a query string (/solve/?id=...) because a static export can't know
 * ids at build.
 *
 * BUGFORGE_LOCAL_API set (running against LocalStack): the dev server proxies
 * /api/* to that URL instead. This is not a convenience -- sign-in needs it.
 * The session cookie is HttpOnly and cross-site, and on plain http a browser
 * will only keep such a cookie for a localhost origin; proxying makes the API
 * part of the web app's own origin, so the cookie is first-party and the
 * GitHub OAuth callback can be a stable http://localhost:3100/... URL that a
 * GitHub OAuth app will accept. A static export can't proxy, so this mode
 * drops `output: "export"` and is for `next dev` only.
 */
const localApi = process.env.BUGFORGE_LOCAL_API?.replace(/\/+$/, "");

/**
 * In proxy mode the only correct value for NEXT_PUBLIC_API_URL is "/api", so
 * take it rather than trust the environment. Git Bash on Windows rewrites a
 * value that looks like a unix path before the process ever sees it, so
 * `NEXT_PUBLIC_API_URL=/api npm run dev` arrives as "C:/Program Files/Git/api"
 * and every fetch fails on an unparseable URL -- which the client reports as
 * "network error: the API did not respond", pointing at the API rather than at
 * the shell. Setting it here is ahead of the bundler reading NEXT_PUBLIC_*.
 */
if (localApi && process.env.NEXT_PUBLIC_API_URL !== "/api") {
  if (process.env.NEXT_PUBLIC_API_URL) {
    console.warn(
      `[bugforge] NEXT_PUBLIC_API_URL was ${JSON.stringify(process.env.NEXT_PUBLIC_API_URL)}; ` +
        `using "/api", which is what the dev-server proxy serves.`,
    );
  }
  process.env.NEXT_PUBLIC_API_URL = "/api";
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  ...(localApi ? {} : { output: "export" }),
  trailingSlash: true,
  reactStrictMode: true,
  ...(localApi
    ? {
        // The API's paths have no trailing slash and must not be redirected
        // into one; /api/challenges is a different route from /api/challenges/.
        skipTrailingSlashRedirect: true,
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${localApi}/:path*` }];
        },
      }
    : {}),
};

export default nextConfig;
