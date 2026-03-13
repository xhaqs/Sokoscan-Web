#!/usr/bin/env python3
"""Add Polymarket search route to server.js"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

SEARCH_ROUTE = """
// ── Polymarket search by keyword
app.get('/api/polymarket/search', async (req, res) => {
  const q = req.query.q || '';
  if (!q) return res.json([]);
  try {
    const url = 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=50&order=volume&ascending=false&search=' + encodeURIComponent(q);
    const r = await fetch(url, {
      signal: AbortSignal.timeout(8000),
      headers: { 'User-Agent': 'RIAKOINE-ORACLE/2.0' }
    });
    const data = await r.json();
    res.json(data);
  } catch(e) {
    // Fallback — filter from main markets
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
    }
  }
});

"""

INSERT_BEFORE = '// ── Polymarket search by keyword'
if INSERT_BEFORE in src:
    print('✅ Search route already exists')
else:
    INSERT_AFTER = '// ════════════════════════════════════════════\n// POLYMARKET PROXY'
    if INSERT_AFTER in src:
        # Find end of polymarket markets route
        idx = src.find("app.get('/api/polymarket/market/")
        end_idx = src.find('\n});', idx) + 4
        new_src = src[:end_idx] + '\n' + SEARCH_ROUTE + src[end_idx:]
        with open('/var/www/sokoscan/server.js', 'w') as f:
            f.write(new_src)
        print('✅ Search route added')
        print('Lines:', new_src.count('\n'))
        print('route present:', '/api/polymarket/search' in new_src)
    else:
        # Try inserting before oracle smart scanner
        INSERT_BEFORE2 = '// ════════════════════════════════════════════\n// ORACLE v2'
        if INSERT_BEFORE2 in src:
            new_src = src.replace(INSERT_BEFORE2, SEARCH_ROUTE + INSERT_BEFORE2, 1)
            with open('/var/www/sokoscan/server.js', 'w') as f:
                f.write(new_src)
            print('✅ Search route added (alt position)')
        else:
            print('❌ Insert point not found')
            import re
            for m in re.finditer(r'// ═+\n// \w+', src):
                print(' ', repr(m.group(0)[:50]))
