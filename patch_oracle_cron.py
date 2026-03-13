#!/usr/bin/env python3
"""
ORACLE Sprint 3:
1. Daily 8am EAT cron — smart scan + Telegram summary
2. /api/oracle/roi — track positions P&L from server
3. Auto-scan cron activation endpoint
"""

with open('/var/www/sokoscan/server.js', 'r') as f:
    src = f.read()

CRON_CODE = """
// ════════════════════════════════════════════
// ORACLE — DAILY CRON + ROI TRACKER
// ════════════════════════════════════════════

// ── Daily 8am EAT summary cron
function scheduleDailyOracleCron() {
  function msUntilNext8amEAT() {
    const now = new Date();
    // EAT = UTC+3
    const eatNow = new Date(now.getTime() + 3 * 60 * 60 * 1000);
    const next8am = new Date(eatNow);
    next8am.setUTCHours(5, 0, 0, 0); // 8am EAT = 5am UTC
    if (next8am <= eatNow) next8am.setUTCDate(next8am.getUTCDate() + 1);
    return next8am.getTime() - now.getTime();
  }

  function runDailyScan() {
    const s = app.locals.oracleSettings;
    console.log('[ORACLE] Daily 8am EAT scan running...');
    // Run smart scan
    fetch('http://localhost:' + (process.env.PORT || 3000) + '/api/oracle/smart-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minEdge: s.edgeThreshold || 12, minVolume: s.minVolume || 5000, maxMarkets: 15 })
    })
    .then(r => r.json())
    .then(d => {
      const count = d.count || 0;
      const dt = new Date().toLocaleString('en-GB', { timeZone: 'Africa/Nairobi', weekday: 'short', day: '2-digit', month: 'short' });
      if (s.tgChatId) {
        const msg = count > 0
          ? '🌅 <b>ORACLE DAILY BRIEFING</b>\\n' + dt + ' · 8:00 AM EAT\\n\\n'
            + '📊 <b>' + count + ' edge opportunit' + (count > 1 ? 'ies' : 'y') + ' detected</b>\\n'
            + (d.alerts || []).slice(0, 3).map(a =>
                '\\n• ' + a.question.substring(0, 50) + '...\\n'
                + '  AI: ' + a.aiProb + '% vs Market: ' + a.yes + '% → <b>' + a.direction.replace('_', ' ') + '</b>'
              ).join('')
            + '\\n\\n<i>Open ORACLE to analyse and bet</i>'
          : '🌅 <b>ORACLE DAILY BRIEFING</b>\\n' + dt + ' · 8:00 AM EAT\\n\\n'
            + '📊 No strong edges today — markets are efficient\\n'
            + '<i>Check back tomorrow or lower threshold in Settings</i>';
        sendTelegram(s.tgChatId, msg).catch(e => console.error('[ORACLE] Daily TG failed:', e.message));
      }
      console.log('[ORACLE] Daily scan complete — ' + count + ' edges');
    })
    .catch(e => console.error('[ORACLE] Daily scan error:', e.message));
    // Schedule next day
    setTimeout(runDailyScan, msUntilNext8amEAT());
  }

  const ms = msUntilNext8amEAT();
  const hrs = (ms / 3600000).toFixed(1);
  console.log('[ORACLE] Daily 8am EAT scan scheduled in ' + hrs + ' hours');
  setTimeout(runDailyScan, ms);
}

// Start daily cron on server boot
scheduleDailyOracleCron();

// ── Manual daily summary trigger
app.post('/api/oracle/daily-summary', async (req, res) => {
  const { tgChatId } = req.body;
  const s = app.locals.oracleSettings;
  const chatId = tgChatId || s.tgChatId;
  if (!chatId) return res.status(400).json({ error: 'No Telegram chat ID' });
  try {
    const r = await fetch('http://localhost:' + (process.env.PORT || 3000) + '/api/oracle/smart-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minEdge: s.edgeThreshold || 12, minVolume: s.minVolume || 5000, maxMarkets: 15 })
    });
    const d = await r.json();
    const count = d.count || 0;
    const dt = new Date().toLocaleString('en-GB', { timeZone: 'Africa/Nairobi', weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    const msg = count > 0
      ? '🔮 <b>ORACLE SUMMARY</b>\\n' + dt + ' EAT\\n\\n'
        + '<b>' + count + ' edge' + (count > 1 ? 's' : '') + ' found:</b>\\n'
        + (d.alerts || []).slice(0, 5).map((a, i) =>
            (i + 1) + '. ' + a.question.substring(0, 45) + '...\\n'
            + '   AI: <b>' + a.aiProb + '%</b> vs Mkt: ' + a.yes + '% | <b>' + a.direction.replace('_', ' ') + '</b> | ' + a.confidence
          ).join('\\n')
        + '\\n\\n<i>ORACLE v2 · Riakoine-Empire</i>'
      : '🔮 <b>ORACLE SUMMARY</b>\\n' + dt + ' EAT\\n\\nNo strong edges detected right now.';
    await sendTelegram(chatId, msg);
    res.json({ ok: true, count, message: 'Summary sent to Telegram' });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// ── ROI tracker (server stores position outcomes)
app.locals.oraclePositions = [];

app.get('/api/oracle/positions', (req, res) => {
  res.json({ ok: true, positions: app.locals.oraclePositions });
});

app.post('/api/oracle/positions', (req, res) => {
  const { question, outcome, odds, stake, myProb, direction } = req.body;
  if (!question || !stake) return res.status(400).json({ error: 'question and stake required' });
  const pos = {
    id: Date.now(),
    question, outcome: outcome || 'YES',
    odds: parseFloat(odds) || 0,
    stake: parseFloat(stake) || 0,
    myProb: parseFloat(myProb) || 0,
    direction: direction || 'BUY_YES',
    status: 'open',
    pnl: 0,
    date: new Date().toISOString()
  };
  app.locals.oraclePositions.unshift(pos);
  res.json({ ok: true, position: pos });
});

app.patch('/api/oracle/positions/:id', (req, res) => {
  const id = parseInt(req.params.id);
  const pos = app.locals.oraclePositions.find(p => p.id === id);
  if (!pos) return res.status(404).json({ error: 'Not found' });
  const { result } = req.body;
  pos.status = 'closed';
  pos.result = result;
  pos.pnl = result === 'WIN'
    ? parseFloat((pos.stake * (1 - pos.odds) / pos.odds).toFixed(2))
    : -pos.stake;
  res.json({ ok: true, position: pos });
});

app.get('/api/oracle/roi', (req, res) => {
  const positions = app.locals.oraclePositions;
  const closed = positions.filter(p => p.status === 'closed');
  const wins = closed.filter(p => p.pnl > 0).length;
  const losses = closed.filter(p => p.pnl < 0).length;
  const totalPnl = closed.reduce((s, p) => s + p.pnl, 0);
  const totalStaked = closed.reduce((s, p) => s + p.stake, 0);
  const roi = totalStaked > 0 ? ((totalPnl / totalStaked) * 100).toFixed(1) : '0.0';
  const winRate = closed.length > 0 ? ((wins / closed.length) * 100).toFixed(1) : '0.0';
  res.json({
    ok: true,
    totalBets: closed.length,
    wins, losses, winRate,
    totalPnl: totalPnl.toFixed(2),
    totalStaked: totalStaked.toFixed(2),
    roi, openBets: positions.filter(p => p.status === 'open').length
  });
});

"""

INSERT_BEFORE = '// ════════════════════════════════════════════\n// ORACLE — PREDICTION INTELLIGENCE'
if INSERT_BEFORE in src:
    new_src = src.replace(INSERT_BEFORE, CRON_CODE + INSERT_BEFORE, 1)
    with open('/var/www/sokoscan/server.js', 'w') as f:
        f.write(new_src)
    print('✅ Daily cron + ROI tracker added')
    print('Lines:', new_src.count('\n'))
    print('daily-summary:', '/api/oracle/daily-summary' in new_src)
    print('roi route:', '/api/oracle/roi' in new_src)
    print('scheduleDailyOracleCron:', 'scheduleDailyOracleCron' in new_src)
else:
    print('❌ Insert point not found')
    import re
    for m in re.finditer(r'// ═+\n// ORACLE', new_src if 'new_src' in dir() else src):
        print(' ', repr(m.group(0)[:60]))
