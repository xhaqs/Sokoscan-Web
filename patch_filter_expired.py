#!/usr/bin/env python3
"""Filter out resolved/expired markets from all Polymarket proxy routes"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

OLD = "const r = await fetch(\n      'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false',"

NEW = "const r = await fetch(\n      'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=200&order=volume&ascending=false',"

# Also find the markets handler where data is returned to client
# Add a filter to strip expired markets before sending
OLD_HANDLER = """  const markets = await r.json();
    res.json(markets);"""

NEW_HANDLER = """  const markets = await r.json();
    const now = Date.now();
    const active = markets.filter(m => {
      const end = m.endDateIso || m.endDate || '';
      if (!end) return true; // no end date = keep
      return new Date(end).getTime() > now;
    });
    res.json(active);"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    print('✅ limit bumped to 200')
else:
    print('⚠️  limit replace skipped')

if OLD_HANDLER in src:
    src = src.replace(OLD_HANDLER, NEW_HANDLER, 1)
    print('✅ expired market filter added')
else:
    # Try alternate format
    OLD_HANDLER2 = "const markets = await r.json();\n    res.json(markets);"
    NEW_HANDLER2 = """const markets = await r.json();
    const now = Date.now();
    const active = markets.filter(m => {
      const end = m.endDateIso || m.endDate || '';
      if (!end) return true;
      return new Date(end).getTime() > now;
    });
    res.json(active);"""
    if OLD_HANDLER2 in src:
        src = src.replace(OLD_HANDLER2, NEW_HANDLER2, 1)
        print('✅ expired market filter added (alt)')
    else:
        print('⚠️  could not find markets handler — check manually')

with open('/var/www/sokoscan/server.js', 'w') as f:
    f.write(src)

print('Lines:', src.count('\n'))
