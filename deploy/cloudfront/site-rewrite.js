// CloudFront Function (runtime cloudfront-js-2.0), viewer-request event, on the
// site behavior only. The accounts build is one page: a page path such as
// /workspace, /access-status, /invite, /admin or /sign-in-error loads
// /index.html, and the page's router picks the view. A path whose last segment
// has a dot is a file and keeps its path, so a missing file stays a 404.
// /auth/* and /api/* are separate behaviors and never reach this function.
function handler(event) {
  var request = event.request;
  var last = request.uri.substring(request.uri.lastIndexOf("/") + 1);
  if (last === "" || last.indexOf(".") === -1) {
    request.uri = "/index.html";
  }
  return request;
}
