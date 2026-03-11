#!/usr/bin/env python3
# patch_tutor.py — injects /api/tutor proxy into server.js
# Run: python3 /tmp/patch_tutor.py

import re

SERVER = '/root/server.js'

ROUTE = '''
// ── BABEL AI Tutor proxy
app.post('/api/tutor', async (req, res) => {
  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': process.env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify(req.body)
    });
    const data = await response.json();
    res.json(data);
  } catch(e) {
    res.status(500).json({error:{message:'Proxy error: '+e.message}});
  }
});

'''

with open(SERVER, 'r') as f:
    content = f.read()

if '/api/tutor' in content:
    print('✅ /api/tutor route already exists — skipping')
else:
    # Insert before app.listen
    patched = re.sub(r'(app\.listen\(PORT)', ROUTE + r'\1', content)
    with open(SERVER, 'w') as f:
        f.write(patched)
    print('✅ /api/tutor route injected into server.js')

# Verify
with open(SERVER, 'r') as f:
    lines = f.readlines()
for i, line in enumerate(lines, 1):
    if '/api/tutor' in line:
        print(f'   Found at line {i}: {line.strip()}')
