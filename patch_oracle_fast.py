#!/usr/bin/env python3
"""
ORACLE v2 — Add smart-scan-fast route to server.js
Filters markets by endDate window before AI analysis
"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

FAST_ROUTE = """
// ── Fast market AI scan (closing within N hours)
app.post('/api/oracle/smart-scan-fast', async (req, res) => {
  const { hoursWindow = 24, minVolume = 500, maxMarkets = 10, minEdge = 10 } = req.body;
  const s = app.locals.oracleSettings;
  console.log('[ORACLE] Fast scan — window:', hoursWindow + 'h');

  try {
    const r = await fetch(
      'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false',
      { signal: AbortSignal.timeout(10000), headers: { 'User-Agent': 'RIAKOINE-ORACLE/2.0' } }
    );
    const markets = await r.json();
    const now = Date.now();
    const cutoff = now + (hoursWindow * 60 * 60 * 1000);

    // Filter to fast-closing, contested, liquid markets
    const candidates = [];
    for (const m of markets) {
      const endMs = new Date(m.endDateIso || m.endDate || 0).getTime();
      if (!endMs || endMs < now || endMs > cutoff) continue;

      let prices = m.outcomePrices;
      if (typeof prices === 'string') { try { prices = JSON.parse(prices); } catch(e) { continue; } }
      const yes = parseFloat((prices && prices[0]) || 0);
      const vol = parseFloat(m.volumeNum || m.volume || 0);
      const liq = parseFloat(m.liquidityNum || m.liquidity || 0);

      if (yes < 0.08 || yes > 0.92) continue;
      if (vol < minVolume) continue;

      const hoursLeft = (endMs - now) / 3600000;
      candidates.push({ market: m, yes, hoursLeft });
    }

    candidates.sort((a, b) => a.hoursLeft - b.hoursLeft);
    const toAnalyse = candidates.slice(0, maxMarkets);
    console.log('[ORACLE] Fast scan candidates:', toAnalyse.length);

    const results = [];
    for (const { market: m, yes, hoursLeft } of toAnalyse) {
      const ai = await getAIProbability(m, yes);
      if (!ai) continue;
      const marketProb = yes * 100;
      const edge = ai.aiProb - marketProb;
      const absEdge = Math.abs(edge);
      if (absEdge < minEdge) continue;
      if (ai.direction === 'SKIP') continue;

      const vol = parseFloat(m.volumeNum || m.volume || 0);
      const timeLeftStr = hoursLeft < 1
        ? Math.round(hoursLeft * 60) + 'm'
        : hoursLeft < 24
          ? hoursLeft.toFixed(1) + 'h'
          : Math.round(hoursLeft / 24) + 'd';

      results.push({
        question: m.question,
        slug: m.slug || '',
        conditionId: m.conditionId || '',
        yes: marketProb.toFixed(1),
        no: ((1 - yes) * 100).toFixed(1),
        aiProb: ai.aiProb.toFixed(1),
        edge: edge.toFixed(1),
        absEdge: absEdge.toFixed(1),
        direction: ai.direction,
        confidence: ai.confidence,
        reasoning: ai.reasoning,
        keyFactor: ai.keyFactor,
        volume: vol,
        hoursLeft: hoursLeft.toFixed(1),
        timeLeft: timeLeftStr,
        endDate: m.endDateIso || m.endDate || '',
        time: new Date().toISOString()
      });
    }

    results.sort((a, b) => parseFloat(b.absEdge) - parseFloat(a.absEdge));
    console.log('[ORACLE] Fast scan complete —', results.length, 'edges found');

    if (results.length > 0 && s.tgChatId) {
      await sendSmartAlert(results.slice(0, 3), s.tgChatId);
    }

    res.json({ ok: true, alerts: results, count: results.length, analysed: toAnalyse.length });
  } catch(e) {
    console.error('[ORACLE] Fast scan error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

"""

INSERT_BEFORE = '// ── Smart Telegram alert'
if INSERT_BEFORE in src:
    new_src = src.replace(INSERT_BEFORE, FAST_ROUTE + INSERT_BEFORE, 1)
    with open('/var/www/sokoscan/server.js', 'w') as f:
        f.write(new_src)
    print('✅ smart-scan-fast route added')
    print('Lines:', new_src.count('\n'))
    print('route present:', '/api/oracle/smart-scan-fast' in new_src)
else:
    print('❌ Insert point not found')
    # Show available markers
    import re
    for m in re.finditer(r'// ── \w', src):
        print(' ', repr(src[m.start():m.start()+40]))
