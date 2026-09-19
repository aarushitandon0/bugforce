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
