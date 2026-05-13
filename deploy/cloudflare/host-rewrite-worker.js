// Cloudflare Worker: rewrite Host header for tenant custom domains.
//
// Purpose:
//   Railway's edge only accepts requests for hostnames it knows about
//   (proxy.sites.katek.app and *.katek.app). When a client points their
//   own domain (e.g. www.acme.com) at this worker via CNAME, the incoming
//   Host header is "www.acme.com" — Railway would reject it.
//
//   This worker rewrites Host to proxy.sites.katek.app so Railway accepts
//   the request, and stashes the original host in X-Original-Host so the
//   Django middleware can still resolve the tenant by its custom domain.
//
// Deploy: paste this into a Cloudflare Worker bound to the custom-domain
// routes. No build step.

export default {
  async fetch(request) {
    const originalHost = request.headers.get('host');

    // Only rewrite if not a katek.app subdomain
    if (originalHost && !originalHost.endsWith('.katek.app') && originalHost !== 'katek.app') {
      const newHeaders = new Headers(request.headers);
      newHeaders.set('host', 'proxy.sites.katek.app');
      newHeaders.set('x-original-host', originalHost);

      const newRequest = new Request(
        request.url.replace(originalHost, 'proxy.sites.katek.app'),
        {
          method: request.method,
          headers: newHeaders,
          body: request.body,
          redirect: 'manual',
        }
      );

      return fetch(newRequest);
    }

    return fetch(request);
  }
};
