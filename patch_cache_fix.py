#!/usr/bin/env python3
"""
Fix ORACLE market cache — use offset pagination instead of broken category tags
Fetches 500+ markets in 5 batches of 100 with different offsets
"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

OLD = """const POLYMARKET_QUERIES = [
  { tag: 'politics',   url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=politics' },
  { tag: 'crypto',     url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=crypto' },
  { tag: 'sports',     url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=sports' },
  { tag: 'economics',  url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&tag=economics' },
  { tag: 'science',    url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false' },
];"""

NEW = """const POLYMARKET_QUERIES = [
  { tag: 'vol-0',    url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&offset=0' },
  { tag: 'vol-100',  url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&offset=100' },
  { tag: 'vol-200',  url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&offset=200' },
  { tag: 'vol-300',  url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false&offset=300' },
  { tag: 'new',      url: 'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=createdAt&ascending=false&offset=0' },
];"""

if OLD in src:
    src = src.replace(OLD, NEW, 1)
    with open('/var/www/sokoscan/server.js', 'w') as f:
        f.write(src)
    print('✅ Cache queries fixed — now uses pagination')
    print('Lines:', src.count('\n'))
else:
    print('❌ Not found — checking what is in file...')
    import re
    m = re.search(r'const POLYMARKET_QUERIES.*?\];', src, re.DOTALL)
    if m:
        print('Found:', m.group(0)[:200])
    else:
        print('POLYMARKET_QUERIES not found at all')
