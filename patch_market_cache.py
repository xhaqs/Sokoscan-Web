#!/usr/bin/env python3
"""
ORACLE — Smart Market Cache
- Fetches markets by category every 30 minutes
- Serves from cache instantly
- Covers Politics, Crypto, Sports, Economics, Science
- Total ~400-500 unique markets always fresh
"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

CACHE_CODE = """
// ════════════════════════════════════════════
// ORACLE — SMART MARKET CACHE
// ════════════════════════════════════════════

app.locals.marketCache = {
  markets: [],
  lastFetch: 0,
  fetching: false
};

const CACHE_TTL = 30 * 60 * 1000; // 30 minutes

const POLYMARKET_QUERIES = [
  { tag: 'politics',   url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=politics' },
  { tag: 'crypto',     url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=crypto' },
  { tag: 'sports',     url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=sports' },
  { tag: 'economics',  url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=economics' },
  { tag: 'science',    url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false' },
];

async function fetchAndCacheMarkets() {
  if (app.locals.marketCache.fetching) return;
  app.locals.marketCache.fetching = true;
  console.log('[ORACLE] Refreshing market cache...');

  try {
    const seen = new Set();
    const all = [];
    const now = Date.now();

    for (const q of POLYMARKET_QUERIES) {
      try {
        const r = await fetch(q.url, {
          signal: AbortSignal.timeout(10000),
          headers: { 'User-Agent': 'RIAKOINE-ORACLE/2.0' }
        });
        const data = await r.json();
        for (const m of data) {
          if (!m.conditionId || seen.has(m.conditionId)) continue;
          // Skip expired
          const end = m.endDateIso || m.endDate || '';
          if (end && new Date(end).getTime() <= now) continue;
          seen.add(m.conditionId);
          all.push(m);
        }
      } catch(e) {
        console.error('[ORACLE] Cache fetch error for', q.tag, e.message);
      }
    }

    // Sort by volume descending
    all.sort((a, b) => parseFloat(b.volumeNum || b.volume || 0) - parseFloat(a.volumeNum || a.volume || 0));

    app.locals.marketCache.markets = all;
    app.locals.marketCache.lastFetch = Date.now();
    console.log('[ORACLE] Cache updated —', all.length, 'active markets');
  } catch(e) {
    console.error('[ORACLE] Cache refresh failed:', e.message);
  } finally {
    app.locals.marketCache.fetching = false;
  }
}

// Serve from cache, auto-refresh if stale
async function getMarkets() {
  const cache = app.locals.marketCache;
  const age = Date.now() - cache.lastFetch;
  if (cache.markets.length === 0 || age > CACHE_TTL) {
    await fetchAndCacheMarkets();
  }
  return app.locals.marketCache.markets;
}

// Auto-refresh every 30 minutes
setInterval(fetchAndCacheMarkets, CACHE_TTL);

// Prime cache on boot after 3 second delay
setTimeout(fetchAndCacheMarkets, 3000);

"""

# Now replace the main markets route to use cache
OLD_MARKETS_ROUTE = """app.get('/api/polymarket/markets', async (req, res) => {"""

NEW_MARKETS_ROUTE = """app.get('/api/polymarket/markets', async (req, res) => {
  // Serve from smart cache
  try {
    const markets = await getMarkets();
    const age = Math.round((Date.now() - app.locals.marketCache.lastFetch) / 1000);
    res.set('X-Cache-Age', age + 's');
    res.set('X-Market-Count', markets.length);
    return res.json(markets);
  } catch(e) {
    console.error('[ORACLE] Markets route error:', e.message);
  }
  // Fallback to direct fetch
  try {"""

# Find the old markets route body and replace it
import re

# Find the full old route
old_route_match = re.search(
    r"app\.get\('/api/polymarket/markets',.*?^}\);",
    src, re.DOTALL | re.MULTILINE
)

if old_route_match:
    old_route = old_route_match.group(0)
    print('Found old markets route, length:', len(old_route))

    NEW_FULL_ROUTE = """app.get('/api/polymarket/markets', async (req, res) => {
  // Serve from smart cache
  try {
    const markets = await getMarkets();
    const age = Math.round((Date.now() - app.locals.marketCache.lastFetch) / 1000);
    res.set('X-Cache-Age', age + 's');
    res.set('X-Market-Count', markets.length);
    return res.json(markets);
  } catch(e) {
    console.error('[ORACLE] Markets route error:', e.message);
    // Fall through to direct fetch
  }
  // Fallback direct fetch
  try {
    const r = await fetch(
      'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=200&order=volume&ascending=false',
      { signal: AbortSignal.timeout(10000), headers: { 'User-Agent': 'RIAKOINE-ORACLE/2.0' } }
    );
    const markets = await r.json();
    const now = Date.now();
    const active = markets.filter(m => {
      const end = m.endDateIso || m.endDate || '';
      if (!end) return true;
      return new Date(end).getTime() > now;
    });
    res.json(active);
  } catch(e) {
    res.status(502).json({ error: 'Markets unavailable' });
  }
});"""

    src = src.replace(old_route, NEW_FULL_ROUTE, 1)
    print('✅ Markets route replaced with cache-backed version')
else:
    print('❌ Could not find old markets route')

# Also update the search route to use cache
OLD_SEARCH_FALLBACK = """    // Fallback — filter from main markets
    try {
      const r2 = await fetch(
        'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false',
        { signal: AbortSignal.timeout(8000), headers: { 'User-Agent': 'RIAKOINE-ORACLE/2.0' } }
      );
      const all = await r2.json();
      const filtered = all.filter(m => m.question && m.question.toLowerCase().includes(q.toLowerCase()));
      res.json(filtered);
    } catch(e2) {
      res.status(502).json({ error: 'Search unavailable' });
    }"""

NEW_SEARCH_FALLBACK = """    // Fallback — filter from cache
    try {
      const all = await getMarkets();
      const filtered = all.filter(m => m.question && m.question.toLowerCase().includes(q.toLowerCase()));
      res.json(filtered);
    } catch(e2) {
      res.status(502).json({ error: 'Search unavailable' });
    }"""

if OLD_SEARCH_FALLBACK in src:
    src = src.replace(OLD_SEARCH_FALLBACK, NEW_SEARCH_FALLBACK, 1)
    print('✅ Search fallback now uses cache')
else:
    print('⚠️  Search fallback not updated (may already be ok)')

# Insert cache code before the polymarket proxy section
INSERT_BEFORE = '// ════════════════════════════════════════════\n// POLYMARKET PROXY'
if INSERT_BEFORE in src:
    src = src.replace(INSERT_BEFORE, CACHE_CODE + INSERT_BEFORE, 1)
    print('✅ Cache system inserted')
else:
    print('❌ Insert point not found')

with open('/var/www/sokoscan/server.js', 'w') as f:
    f.write(src)

print('Lines:', src.count('\n'))
print('getMarkets present:', 'getMarkets' in src)
print('fetchAndCacheMarkets present:', 'fetchAndCacheMarkets' in src)
print('setInterval present:', 'setInterval(fetchAndCacheMarkets' in src)
