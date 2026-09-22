// The CloudFront Function that makes a static export behave like a website.
//
// WHY IT IS NEEDED
//   The frontend is a Next.js static export with `trailingSlash: true`, so every route is exported
//   as a DIRECTORY containing index.html:
//
//     /            ->  out/index.html
//     /stocks/     ->  out/stocks/index.html
//
//   S3 behind Origin Access Control is object storage, not a website endpoint: it has no notion of
//   a directory index. Asking it for the key "stocks/" returns nothing. Something has to turn the
//   request path into a real object key, and a CloudFront Function on viewer-request is the
//   cheapest place to do it -- sub-millisecond, no cold start, no VPC, no Lambda@Edge.
//
// HOW IT IS TESTED
//   A CloudFront Function is not a module: it declares a global `handler` and exports nothing, so
//   it can be shipped to CloudFront verbatim. This test reads that file as text and evaluates it,
//   which keeps the deployed artifact free of test scaffolding.
//
//   Run from the repository root:  node --test infra/edge/functions/rewrite-index.test.mjs
//   (a directory argument does not resolve on Node 24; name the file)
//
// WHAT THIS CANNOT PROVE
//   That the CloudFront JavaScript runtime 2.0 accepts the file. The runtime is a restricted
//   dialect, and only a real apply finds out. That is a named risk in the milestone, not something
//   this test claims to cover.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const source = readFileSync(fileURLToPath(new URL('./rewrite-index.js', import.meta.url)), 'utf8');

// eslint-disable-next-line no-new-func -- evaluating the deployed file is the point of this test
const handler = new Function(`${source}\nreturn handler;`)();

/** The shape CloudFront hands a viewer-request function. */
const request = (uri) => ({ request: { uri, method: 'GET', headers: {}, querystring: {} } });

const rewrite = (uri) => handler(request(uri)).uri;

test('the site root becomes the exported index', () => {
  assert.equal(rewrite('/'), '/index.html');
});

test('a directory path becomes that directory index', () => {
  assert.equal(rewrite('/stocks/'), '/stocks/index.html');
});

test('a route without its trailing slash still finds the index', () => {
  // Someone typing /stocks, or a link that dropped the slash, must not 404.
  assert.equal(rewrite('/stocks'), '/stocks/index.html');
});

test('a real file is left exactly alone', () => {
  for (const uri of [
    '/index.html',
    '/404.html',
    '/favicon.ico',
    '/_next/static/chunks/main-abc123.js',
    '/_next/static/css/app.css',
    '/images/logo.svg',
  ]) {
    assert.equal(rewrite(uri), uri, `${uri} should not be rewritten`);
  }
});

test('a dot in a directory name does not make it look like a file', () => {
  // The test is "does the LAST segment contain a dot", not "does the path contain a dot".
  assert.equal(rewrite('/v1.2/notes/'), '/v1.2/notes/index.html');
});

test('the function never rewrites an api path', () => {
  // Belt and braces. The distribution does not attach this function to the /api/* behaviour at
  // all, but if that wiring were ever changed by accident, the function must not break the API.
  for (const uri of ['/api/v1/me', '/api/healthz', '/api/v1/stocks/TCS/follow']) {
    assert.equal(rewrite(uri), uri, `${uri} must reach the backend untouched`);
  }
});

test('the request object is returned, not a new one', () => {
  // CloudFront expects the (possibly modified) request back. Returning anything else drops the
  // method, headers and query string, which would break every POST.
  const event = request('/stocks/');
  const returned = handler(event);
  assert.equal(returned, event.request);
  assert.equal(returned.method, 'GET');
});
