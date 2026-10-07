// Next.js configuration, as a function of the phase.
//
// PRODUCTION is a static export (ADR 006): plain files for S3 + CloudFront, no Node server, and
// therefore none of the features that need one (rewrites, redirects, custom headers, middleware).
//
// DEVELOPMENT is the same app served by `next dev`, plus one dev-only rewrite that forwards /api to
// the backend. It exists so the browser sees ONE origin (http://localhost:3000) exactly as it will
// behind CloudFront: the session cookie and the backend's Origin check then work unchanged and no
// CORS headers are needed anywhere.

const DEVELOPMENT = 'phase-development-server';

/** @param {string} phase */
export default function nextConfig(phase) {
  if (phase === DEVELOPMENT) {
    const backend = process.env.BACKEND_ORIGIN ?? 'http://127.0.0.1:8000';
    return {
      trailingSlash: true,
      // With trailingSlash on, Next would redirect /api/v1/me to /api/v1/me/, which the backend
      // does not have. The backend's routes have no trailing slash.
      skipTrailingSlashRedirect: true,
      // `next dev` would otherwise write AI-assistant instruction files (AGENTS.md and others)
      // into this folder. Such files are the owner's decision, not a dependency's.
      agentRules: false,
      async rewrites() {
        return [{ source: '/api/:path*', destination: `${backend}/api/:path*` }];
      },
    };
  }

  return {
    output: 'export',
    trailingSlash: true, // each route exports as route/index.html
    images: { unoptimized: true }, // there is no image-optimisation server
  };
}
