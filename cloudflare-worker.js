/**
 * ╔══════════════════════════════════════════════════════════╗
 * ║  SokoScan — Cloudflare Worker Firewall                  ║
 * ║  Deploy: Cloudflare Dashboard → Workers → New Worker    ║
 * ║  Route:  sokoscan.yourdomain.com/*                      ║
 * ╚══════════════════════════════════════════════════════════╝
 *
 *  Features:
 *  ✓ HTTP → HTTPS redirect
 *  ✓ IP-based rate limiting (120 req/min, 5-min ban)
 *  ✓ Bot / scanner / scraper detection
 *  ✓ Path traversal & SQL injection blocking
 *  ✓ Method allowlist (GET, POST, OPTIONS, HEAD only)
 *  ✓ Geo-blocking (optional — edit GEO_BLOCK array)
 *  ✓ Security headers injected on every response
 *  ✓ Server fingerprint headers stripped
 *  ✓ DDoS mitigation via Cloudflare's Anycast network
 */

// ── Config ───────────────────────────────────────────────────
const RATE_LIMIT   = 120;     // requests per window
const WINDOW_MS    = 60000;   // 60-second window
const BAN_MS       = 300000;  // 5-minute ban after limit hit

// ISO 3166-1 alpha-2 country codes to block (empty = allow all)
const GEO_BLOCK    = [];

// User-agent patterns to block
const BOT_UA = [
  /bot|crawl|spider|scraper|headless/i,
  /curl|wget|python-requests/i,
  /masscan|nmap|sqlmap|nikto|dirbuster/i,
];

// URL patterns to block immediately
const MALICIOUS_URL = [
  /\.\.[\/\\]/,           // path traversal
  /union\s+select/i,      // SQL injection
  /<script/i,             // XSS in URL
  /javascript:/i,
  /\bexec\s*\(/i,
  /\bdrop\s+table/i,
  /etc\/passwd/i,
  /\.env\b/i,
  /wp-admin/i,            // WordPress probing
];

// In-memory store (resets per worker instance)
const store = new Map();

// ── Security headers to inject ───────────────────────────────
function secHeaders(isStatic) {
  return {
    'Strict-Transport-Security': 'max-age=63072000; includeSubDomains; preload',
    'X-Frame-Options':           'DENY',
    'X-Content-Type-Options':    'nosniff',
    'X-XSS-Protection':          '1; mode=block',
    'Referrer-Policy':           'strict-origin-when-cross-origin',
    'Permissions-Policy':        'camera=(), microphone=(), geolocation=(), payment=()',
    'Cross-Origin-Opener-Policy':'same-origin',
    'Cache-Control':             isStatic
      ? 'public, max-age=31536000, immutable'
      : 'no-cache, no-store, must-revalidate',
  };
}

addEventListener('fetch', event => {
  event.respondWith(handle(event.request));
});

async function handle(req) {
  const url  = new URL(req.url);
  const ip   = req.headers.get('CF-Connecting-IP') || 'unknown';
  const ua   = req.headers.get('User-Agent') || '';
  const cf   = req.cf || {};

  // 1. HTTPS redirect
  if (url.protocol === 'http:') {
    return Response.redirect(`https://${url.host}${url.pathname}${url.search}`, 301);
  }

  // 2. Geo-block
  if (GEO_BLOCK.length && cf.country && GEO_BLOCK.includes(cf.country)) {
    return new Response('Access restricted in your region.', { status: 451 });
  }

  // 3. Bot detection
  for (const p of BOT_UA) {
    if (p.test(ua)) {
      return new Response('Automated access not permitted.', { status: 403 });
    }
  }

  // 4. Malicious URL patterns
  const raw = decodeURIComponent(req.url);
  for (const p of MALICIOUS_URL) {
    if (p.test(raw)) {
      return new Response('Request blocked.', { status: 400 });
    }
  }

  // 5. Method allowlist
  if (!['GET','POST','OPTIONS','HEAD'].includes(req.method)) {
    return new Response('Method not allowed.', { status: 405 });
  }

  // 6. Rate limiting
  const now = Date.now();
  const rec = store.get(ip) || { n: 0, t: now, banned: false, exp: 0 };

  if (rec.banned && now < rec.exp) {
    return new Response('Too many requests.', {
      status: 429,
      headers: { 'Retry-After': String(Math.ceil((rec.exp - now) / 1000)) }
    });
  }

  if (now - rec.t > WINDOW_MS) {
    rec.n = 1; rec.t = now; rec.banned = false;
  } else {
    rec.n++;
    if (rec.n > RATE_LIMIT) {
      rec.banned = true; rec.exp = now + BAN_MS;
      store.set(ip, rec);
      return new Response('Rate limit exceeded.', {
        status: 429,
        headers: { 'Retry-After': String(BAN_MS / 1000) }
      });
    }
  }
  store.set(ip, rec);

  // 7. Proxy to origin & inject headers
  const res     = await fetch(req);
  const headers = new Headers(res.headers);
  const isStatic = /\.(png|ico|woff2|jpg|svg)$/.test(url.pathname);

  Object.entries(secHeaders(isStatic)).forEach(([k, v]) => headers.set(k, v));
  headers.delete('X-Powered-By');
  headers.delete('Server');

  // Rate limit headers for client awareness
  headers.set('X-RateLimit-Limit',     String(RATE_LIMIT));
  headers.set('X-RateLimit-Remaining', String(Math.max(0, RATE_LIMIT - rec.n)));
  headers.set('X-RateLimit-Reset',     String(Math.ceil((rec.t + WINDOW_MS) / 1000)));

  return new Response(res.body, {
    status:     res.status,
    statusText: res.statusText,
    headers,
  });
}
