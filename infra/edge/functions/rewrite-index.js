// CloudFront Function, viewer-request, attached to the STATIC behaviour only.
//
// WHAT IT DOES
//   Turns a request path into the object key the Next.js static export actually wrote.
//   With `trailingSlash: true` every route exports as a directory containing index.html:
//
//     /            ->  /index.html
//     /stocks/     ->  /stocks/index.html
//     /stocks      ->  /stocks/index.html
//     /_next/...js ->  unchanged (it is already a real file)
//
//   S3 behind Origin Access Control is object storage: it has no directory index, so without this
//   every route but a literal file would 404.
//
// WHY IT IS WRITTEN IN OLD-FASHIONED JAVASCRIPT
//   CloudFront Functions run in a restricted sandbox, not Node. Rather than depend on which ECMAScript
//   edition the runtime supports, this uses only indexOf, charAt and substring, which have worked
//   everywhere since forever. The logic is proven offline by rewrite-index.test.mjs; the runtime's
//   acceptance of the file is proven only by a real apply.
//
// WHY IT RETURNS THE REQUEST OBJECT
//   CloudFront expects the (possibly modified) request back. Returning a new object would drop the
//   method, headers and query string, breaking every POST.

function handler(event) {
    var request = event.request;
    var uri = request.uri;

    // The distribution does not attach this function to /api/*, so this should never be reached.
    // It is here so that if that wiring is ever changed by accident, the API still works: appending
    // /index.html to /api/v1/me would 404 every call.
    if (uri.indexOf('/api/') === 0) {
        return request;
    }

    // A path that already ends in a slash names a directory: serve that directory's index.
    if (uri.charAt(uri.length - 1) === '/') {
        request.uri = uri + 'index.html';
        return request;
    }

    // Otherwise, decide by the LAST segment only. A dot earlier in the path (say /v1.2/notes) is
    // part of a directory name, not an extension, so the test is not "does the path contain a dot".
    var lastSlash = uri.lastIndexOf('/');
    var lastSegment = uri.substring(lastSlash + 1);

    if (lastSegment.indexOf('.') === -1) {
        request.uri = uri + '/index.html';
    }

    return request;
}
