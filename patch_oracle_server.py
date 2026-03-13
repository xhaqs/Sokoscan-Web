#!/usr/bin/env python3
"""
ORACLE Sprint 2 — Add to server.js:
1. Oracle settings store (in-memory)
2. /api/oracle/settings GET/POST
3. /api/oracle/scan — manual scan trigger
4. /api/oracle/alert — send Telegram alert
5. Auto-scan cron every N minutes
"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

ORACLE_ROUTES = """
// ════════════════════════════════════════════
// ORACLE — PREDICTION INTELLIGENCE
// ════════════════════════════════════════════
app.locals.oracleSettings = {
  tgChatId: process.env.TELEGRAM_CHAT_ID || '',
  edgeThreshold: 15,
  minVolume: 10000,
  scanInterval: 60,
  autoScan: false,
  lastScan: null,
  lastAlerts: []
};

// ── Oracle settings
app.get('/api/oracle/settings', (req, res) => {
  const s = app.locals.oracleSettings;
  res.json({ ok: true, settings: {
    edgeThreshold: s.edgeThreshold,
    minVolume: s.minVolume,
    scanInterval: s.scanInterval,
    autoScan: s.autoScan,
    lastScan: s.lastScan,
    alertCount: s.lastAlerts.length
  }});
});

app.post('/api/oracle/settings', (req, res) => {
  const { edgeThreshold, minVolume, scanInterval, autoScan, tgChatId } = req.body;
  const s = app.locals.oracleSettings;
  if (edgeThreshold !== undefined) s.edgeThreshold = parseFloat(edgeThreshold) || 15;
  if (minVolume !== undefined) s.minVolume = parseFloat(minVolume) || 10000;
  if (scanInterval !== undefined) s.scanInterval = parseInt(scanInterval) || 60;
  if (autoScan !== undefined) s.autoScan = !!autoScan;
  if (tgChatId !== undefined) s.tgChatId = tgChatId;
  // Restart cron if interval changed
  if (autoScan !== undefined || scanInterval !== undefined) {
    startOracleCron();
  }
  console.log('[ORACLE] Settings updated:', s);
  res.json({ ok: true });
});

// ── Oracle scan engine
async function oracleScan() {
  const s = app.locals.oracleSettings;
  console.log('[ORACLE] Scanning markets...');
  try {
    const r = await fetch(
      'https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&order=volume&ascending=false',
      { signal: AbortSignal.timeout(10000), headers: { 'User-Agent': 'RIAKOINE-ORACLE/1.0' } }
    );
    const markets = await r.json();
    s.lastScan = new Date().toISOString();
    const alerts = [];
    for (const m of markets) {
      let yes = parseFloat(m.outcomePrices && m.outcomePrices[0] || m.yes || 0);
      if (yes > 1) yes = yes / 100;
      const vol = parseFloat(m.volume || m.volumeNum || 0);
      if (vol < s.minVolume) continue;
      const edge = Math.abs(yes - 0.5) * 100;
      if (edge >= s.edgeThreshold) {
        alerts.push({
          question: m.question,
          yes: (yes * 100).toFixed(1),
          no: ((1 - yes) * 100).toFixed(1),
          edge: edge.toFixed(1),
          volume: vol,
          slug: m.slug || m.marketSlug || '',
          conditionId: m.conditionId || '',
          time: new Date().toISOString()
        });
      }
    }
    // Sort by edge descending
    alerts.sort((a, b) => parseFloat(b.edge) - parseFloat(a.edge));
    s.lastAlerts = alerts;
    console.log('[ORACLE] Scan complete — ' + alerts.length + ' alerts above ' + s.edgeThreshold + '% edge');
    // Send Telegram if alerts found
    if (alerts.length > 0 && s.tgChatId) {
      await sendOracleAlert(alerts.slice(0, 5), s.tgChatId);
    }
    return alerts;
  } catch(e) {
    console.error('[ORACLE] Scan failed:', e.message);
    return [];
  }
}

async function sendOracleAlert(alerts, chatId) {
  const top = alerts[0];
  const volStr = top.volume > 1000000
    ? '$' + (top.volume / 1000000).toFixed(1) + 'M'
    : '$' + (top.volume / 1000).toFixed(0) + 'K';
  const link = top.slug
    ? 'https://polymarket.com/event/' + top.slug
    : 'https://polymarket.com';
  const msg =
`🔮 <b>ORACLE EDGE ALERT</b>
<b>${alerts.length} market${alerts.length > 1 ? 's' : ''} above threshold</b>

🥇 <b>TOP OPPORTUNITY:</b>
📋 ${top.question}
✅ YES: <b>${top.yes}%</b> · ❌ NO: <b>${top.no}%</b>
⚡ <b>Edge: ${top.edge}%</b>
💰 Volume: ${volStr}
${link !== 'https://polymarket.com' ? `\n🔗 <a href="${link}">OPEN ON POLYMARKET</a>` : ''}
${alerts.length > 1 ? '\n<b>Other alerts:</b>\n' + alerts.slice(1, 5).map(a =>
  `• ${a.question.substring(0, 50)}... (${a.edge}% edge)`
).join('\n') : ''}

<i>ORACLE — Riakoine-Empire · ${new Date().toLocaleString('en-GB', { timeZone: 'Africa/Nairobi', hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })} EAT</i>`;
  try {
    await sendTelegram(chatId, msg);
    console.log('[ORACLE] Alert sent to Telegram');
  } catch(e) {
    console.error('[ORACLE] Telegram alert failed:', e.message);
  }
}

// ── Manual scan trigger
app.post('/api/oracle/scan', async (req, res) => {
  const { tgChatId, edgeThreshold, minVolume } = req.body;
  const s = app.locals.oracleSettings;
  if (tgChatId) s.tgChatId = tgChatId;
  if (edgeThreshold) s.edgeThreshold = parseFloat(edgeThreshold);
  if (minVolume) s.minVolume = parseFloat(minVolume);
  const alerts = await oracleScan();
  res.json({ ok: true, alerts, count: alerts.length, lastScan: s.lastScan });
});

// ── Get last alerts
app.get('/api/oracle/alerts', (req, res) => {
  const s = app.locals.oracleSettings;
  res.json({ ok: true, alerts: s.lastAlerts, lastScan: s.lastScan });
});

// ── Test Telegram
app.post('/api/oracle/test-alert', async (req, res) => {
  const { tgChatId } = req.body;
  const chatId = tgChatId || app.locals.oracleSettings.tgChatId;
  if (!chatId) return res.status(400).json({ error: 'No Telegram chat ID set' });
  const ok = await sendTelegram(chatId,
    '🔮 <b>ORACLE TEST</b>\\nTelegram alerts are working!\\n<i>Riakoine-Empire</i>'
  );
  res.json({ ok });
});

// ── Auto-scan cron
let oracleCronTimer = null;
function startOracleCron() {
  if (oracleCronTimer) clearInterval(oracleCronTimer);
  const s = app.locals.oracleSettings;
  if (!s.autoScan) {
    console.log('[ORACLE] Auto-scan disabled');
    return;
  }
  const ms = s.scanInterval * 60 * 1000;
  console.log('[ORACLE] Auto-scan every ' + s.scanInterval + ' min');
  oracleCronTimer = setInterval(oracleScan, ms);
}

"""

# Insert before GLOTTOLOG section
INSERT_BEFORE = '// ════════════════════════════════════════════\n// GLOTTOLOG'
if INSERT_BEFORE in src:
    new_src = src.replace(INSERT_BEFORE, ORACLE_ROUTES + INSERT_BEFORE, 1)
    with open('/var/www/sokoscan/server.js', 'w') as f:
        f.write(new_src)
    print('✅ ORACLE routes added to server.js')
    print('Lines:', new_src.count('\n'))
    print('oracle/scan route:', '/api/oracle/scan' in new_src)
    print('oracle/alerts route:', '/api/oracle/alerts' in new_src)
    print('sendOracleAlert fn:', 'sendOracleAlert' in new_src)
else:
    print('❌ Insert point not found')
    # Try alternative
    INSERT_BEFORE2 = '// ════════════════════════════════════════════\n// STATIC'
    if INSERT_BEFORE2 in src:
        new_src = src.replace(INSERT_BEFORE2, ORACLE_ROUTES + INSERT_BEFORE2, 1)
        with open('/var/www/sokoscan/server.js', 'w') as f:
            f.write(new_src)
        print('✅ ORACLE routes added (alt position)')
    else:
        print('Available sections:')
        import re
        for m in re.finditer(r'// ═+\n// \w+', src):
            print(' ', m.group(0)[:40])
