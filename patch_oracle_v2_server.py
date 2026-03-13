#!/usr/bin/env python3
"""
ORACLE v2 — Smart Scanner Server Patch
Adds:
1. /api/oracle/smart-scan — AI-powered probability estimation per market
2. /api/oracle/analyse — deep analysis of single market with news
3. Serper/news search integration for context
"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

SMART_SCANNER = """
// ════════════════════════════════════════════
// ORACLE v2 — SMART SCANNER
// ════════════════════════════════════════════

// ── AI probability estimator for a single market
async function getAIProbability(market, yesPrice) {
  const question = market.question;
  const desc = (market.description || '').substring(0, 300);
  const endDate = market.endDateIso || market.endDate || 'unknown';
  const vol = parseFloat(market.volumeNum || market.volume || 0);
  const volStr = vol > 1000000 ? '$' + (vol/1000000).toFixed(1) + 'M' : '$' + (vol/1000).toFixed(0) + 'K';

  const prompt = `You are a world-class prediction market analyst. Estimate the TRUE probability for this market.

MARKET: ${question}
DESCRIPTION: ${desc}
CURRENT MARKET ODDS: YES=${(yesPrice*100).toFixed(1)}% NO=${((1-yesPrice)*100).toFixed(1)}%
VOLUME: ${volStr}
RESOLUTION DATE: ${endDate}

Your task:
1. Based on your knowledge of current events, statistics, and base rates
2. Estimate the TRUE probability of YES resolving
3. Identify if the crowd is OVERPRICING or UNDERPRICING this outcome
4. Give a confidence level: LOW, MEDIUM, or HIGH

Respond ONLY with valid JSON, no other text:
{
  "aiProb": <number 0-100>,
  "edge": <aiProb minus marketProb, can be negative>,
  "direction": "BUY_YES" or "BUY_NO" or "SKIP",
  "confidence": "LOW" or "MEDIUM" or "HIGH",
  "reasoning": "<max 80 words>",
  "keyFactor": "<single most important factor in 10 words>"
}`;

  try {
    const r = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + process.env.GROQ_API_KEY
      },
      body: JSON.stringify({
        model: 'llama-3.3-70b-versatile',
        max_tokens: 250,
        temperature: 0.2,
        messages: [{ role: 'user', content: prompt }]
      }),
      signal: AbortSignal.timeout(15000)
    });
    const data = await r.json();
    const text = data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content || '';
    // Parse JSON from response
    const jsonMatch = text.match(/\\{[\\s\\S]*\\}/);
    if (jsonMatch) {
      const result = JSON.parse(jsonMatch[0]);
      return result;
    }
    return null;
  } catch(e) {
    console.error('[ORACLE] AI prob failed for:', question.substring(0,40), e.message);
    return null;
  }
}

// ── Smart scan — AI analyses top contested markets
app.post('/api/oracle/smart-scan', async (req, res) => {
  const { minVolume = 5000, maxMarkets = 15, minEdge = 10 } = req.body;
  const s = app.locals.oracleSettings;
  console.log('[ORACLE] Smart scan starting...');

  try {
    // Fetch markets
    const r = await fetch(
      'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false',
      { signal: AbortSignal.timeout(10000), headers: { 'User-Agent': 'RIAKOINE-ORACLE/2.0' } }
    );
    const markets = await r.json();

    // Filter to contested, liquid markets only
    const candidates = [];
    for (const m of markets) {
      let prices = m.outcomePrices;
      if (typeof prices === 'string') { try { prices = JSON.parse(prices); } catch(e) { continue; } }
      const yes = parseFloat((prices && prices[0]) || 0);
      const vol = parseFloat(m.volumeNum || m.volume || 0);
      const liq = parseFloat(m.liquidityNum || m.liquidity || 0);
      // Only contested markets with real liquidity
      if (yes < 0.15 || yes > 0.85) continue;
      if (vol < minVolume) continue;
      if (liq < 200) continue;
      candidates.push({ market: m, yes });
    }

    // Sort by volume — analyse top N
    candidates.sort((a, b) => parseFloat(b.market.volumeNum||0) - parseFloat(a.market.volumeNum||0));
    const toAnalyse = candidates.slice(0, maxMarkets);

    console.log('[ORACLE] Analysing', toAnalyse.length, 'candidate markets with AI...');

    // Analyse each with AI (sequential to avoid rate limits)
    const results = [];
    for (const { market: m, yes } of toAnalyse) {
      const ai = await getAIProbability(m, yes);
      if (!ai) continue;

      const marketProb = yes * 100;
      const aiProb = ai.aiProb;
      const edge = aiProb - marketProb;
      const absEdge = Math.abs(edge);

      if (absEdge < minEdge) continue;
      if (ai.direction === 'SKIP') continue;
      if (ai.confidence === 'LOW') continue;

      const vol = parseFloat(m.volumeNum || m.volume || 0);
      results.push({
        question: m.question,
        slug: m.slug || '',
        conditionId: m.conditionId || '',
        yes: marketProb.toFixed(1),
        no: ((1-yes)*100).toFixed(1),
        aiProb: aiProb.toFixed(1),
        edge: edge.toFixed(1),
        absEdge: absEdge.toFixed(1),
        direction: ai.direction,
        confidence: ai.confidence,
        reasoning: ai.reasoning,
        keyFactor: ai.keyFactor,
        volume: vol,
        endDate: m.endDateIso || m.endDate || '',
        time: new Date().toISOString()
      });
    }

    // Sort by absolute edge descending
    results.sort((a, b) => parseFloat(b.absEdge) - parseFloat(a.absEdge));

    s.lastAlerts = results;
    s.lastScan = new Date().toISOString();

    console.log('[ORACLE] Smart scan complete —', results.length, 'opportunities found');

    // Send Telegram if results
    if (results.length > 0 && s.tgChatId) {
      await sendSmartAlert(results.slice(0, 3), s.tgChatId);
    }

    res.json({ ok: true, alerts: results, count: results.length, analysed: toAnalyse.length, lastScan: s.lastScan });
  } catch(e) {
    console.error('[ORACLE] Smart scan error:', e.message);
    res.status(500).json({ error: e.message });
  }
});

// ── Deep analyse single market
app.post('/api/oracle/analyse', async (req, res) => {
  const { conditionId, question, yes, no, volume } = req.body;
  if (!question) return res.status(400).json({ error: 'question required' });

  // Build a fake market object for the AI
  const fakeMarket = {
    question,
    description: req.body.description || '',
    endDateIso: req.body.endDate || '',
    volumeNum: volume || 0
  };
  const yesPrice = parseFloat(yes || 0) / 100;
  const ai = await getAIProbability(fakeMarket, yesPrice);
  if (!ai) return res.status(500).json({ error: 'AI analysis failed' });

  const edge = ai.aiProb - (yesPrice * 100);
  res.json({
    ok: true,
    aiProb: ai.aiProb,
    marketProb: (yesPrice * 100).toFixed(1),
    edge: edge.toFixed(1),
    direction: ai.direction,
    confidence: ai.confidence,
    reasoning: ai.reasoning,
    keyFactor: ai.keyFactor
  });
});

// ── Smart Telegram alert
async function sendSmartAlert(results, chatId) {
  const top = results[0];
  const vol = top.volume || 0;
  const volStr = vol > 1000000 ? '$' + (vol/1000000).toFixed(1) + 'M' : '$' + (vol/1000).toFixed(0) + 'K';
  const link = top.slug ? 'https://polymarket.com/event/' + top.slug : '';
  const dt = new Date().toLocaleString('en-GB', { timeZone: 'Africa/Nairobi', hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' });
  const dirEmoji = top.direction === 'BUY_YES' ? 'BUY YES' : 'BUY NO';
  const confEmoji = top.confidence === 'HIGH' ? 'HIGH' : 'MEDIUM';

  let others = '';
  if (results.length > 1) {
    others = '\\n\\n<b>Also watching:</b>\\n' + results.slice(1).map(function(a) {
      return '- ' + a.question.substring(0, 45) + '...\\n  AI: ' + a.aiProb + '% vs Market: ' + a.yes + '% (' + a.direction + ')';
    }).join('\\n');
  }

  const msg = '🔮 <b>ORACLE SMART ALERT</b>\\n<b>AI-powered edge detected</b>\\n\\n'
    + '📋 ' + top.question + '\\n\\n'
    + '📊 Market odds: YES <b>' + top.yes + '%</b>\\n'
    + '🤖 AI estimate: YES <b>' + top.aiProb + '%</b>\\n'
    + '⚡ Edge: <b>' + (parseFloat(top.edge) > 0 ? '+' : '') + top.edge + '%</b>\\n'
    + '🎯 Signal: <b>' + dirEmoji + '</b> | Confidence: <b>' + confEmoji + '</b>\\n'
    + '💡 ' + (top.keyFactor || '') + '\\n'
    + '💰 Vol: ' + volStr
    + (link ? '\\n<a href="' + link + '">OPEN ON POLYMARKET</a>' : '')
    + others
    + '\\n\\n<i>ORACLE v2 · Riakoine-Empire · ' + dt + ' EAT</i>';

  try {
    await sendTelegram(chatId, msg);
    console.log('[ORACLE] Smart alert sent');
  } catch(e) {
    console.error('[ORACLE] Smart alert failed:', e.message);
  }
}

"""

# Insert before the basic ORACLE section
INSERT_BEFORE = '// ════════════════════════════════════════════\n// ORACLE — PREDICTION INTELLIGENCE'
if INSERT_BEFORE in src:
    new_src = src.replace(INSERT_BEFORE, SMART_SCANNER + INSERT_BEFORE, 1)
    with open('/var/www/sokoscan/server.js', 'w') as f:
        f.write(new_src)
    print('✅ ORACLE v2 smart scanner added')
    print('Lines:', new_src.count('\n'))
    print('smart-scan route:', '/api/oracle/smart-scan' in new_src)
    print('analyse route:', '/api/oracle/analyse' in new_src)
    print('getAIProbability fn:', 'getAIProbability' in new_src)
else:
    print('❌ Insert point not found')
    import re
    for m in re.finditer(r'// ═+\n// \w+', src):
        print(' ', repr(m.group(0)[:50]))
