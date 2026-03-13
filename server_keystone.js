require('dotenv').config({path:'/var/www/sokoscan/.env'});
process.chdir('/var/www/sokoscan');
const nodeFetch = (...args) => import('node-fetch').then(({default: fetch}) => fetch(...args));
const express = require('express');
const cors    = require('cors');
const helmet  = require('helmet');
const rateLimit = require('express-rate-limit');
const fetch   = (...a) => import('node-fetch').then(({default:f})=>f(...a));
const path    = require('path');
const crypto  = require('crypto');
const fs      = require('fs');
const app  = express();
const PORT = process.env.PORT || 3000;

// ── Security headers
app.use(helmet({ contentSecurityPolicy: false }));
app.use(express.json({ limit: '20kb' }));

// ── CORS
const ALLOWED = (process.env.ALLOWED_ORIGINS || 'http://localhost:3000').split(',');
app.use(cors({
  origin: (origin, cb) => {
    if (!origin || ALLOWED.includes(origin) || (origin && origin.endsWith('.trycloudflare.com'))) return cb(null, true);
    cb(new Error('CORS blocked: ' + origin));
  }
}));

// ── Rate limiters
const globalLimiter = rateLimit({ windowMs: 60_000, max: 120, standardHeaders: true, legacyHeaders: false });
const aiLimiter     = rateLimit({ windowMs: 60_000, max: 20,  message: { error: 'AI rate limit — wait 1 min' } });
const keystoneLimiter = rateLimit({ windowMs: 60_000, max: 10, message: { error: 'Too many login attempts' } });
app.use(globalLimiter);

// ════════════════════════════════════════════
// KEYSTONE — ACCESS CONTROL SYSTEM
// ════════════════════════════════════════════

const KEYSTONE_DB = '/var/www/sokoscan/data/keystone/users.json';
const KEYSTONE_LOG = '/var/www/sokoscan/data/keystone/audit.json';
const KEYSTONE_TOKENS = {}; // in-memory token store: token -> {userId, expires, app}

// ── Ensure keystone data dir exists
function ensureKeystoneDir() {
  const dir = '/var/www/sokoscan/data/keystone';
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

// ── Load users
function loadUsers() {
  ensureKeystoneDir();
  if (!fs.existsSync(KEYSTONE_DB)) {
    // Seed with Zack as admin using env password
    const salt = crypto.randomBytes(16).toString('hex');
    const masterPwd = process.env.KEYSTONE_MASTER || 'SOKOSCAN2025';
    const hash = crypto.pbkdf2Sync(masterPwd, salt, 10000, 32, 'sha256').toString('hex');
    const seed = [{
      id: 'zack',
      name: 'Zack',
      role: 'admin',
      salt,
      hash,
      apps: ['*'],
      active: true,
      created: new Date().toISOString(),
      lastLogin: null
    }];
    fs.writeFileSync(KEYSTONE_DB, JSON.stringify(seed, null, 2));
    console.log('[KEYSTONE] Users DB seeded with admin:Zack');
    return seed;
  }
  return JSON.parse(fs.readFileSync(KEYSTONE_DB, 'utf8'));
}

function saveUsers(users) {
  ensureKeystoneDir();
  fs.writeFileSync(KEYSTONE_DB, JSON.stringify(users, null, 2));
}

// ── Audit log
function auditLog(action, userId, detail) {
  ensureKeystoneDir();
  let log = [];
  try { log = JSON.parse(fs.readFileSync(KEYSTONE_LOG, 'utf8')); } catch(e) {}
  log.unshift({ time: new Date().toISOString(), action, userId, detail });
  if (log.length > 500) log = log.slice(0, 500);
  fs.writeFileSync(KEYSTONE_LOG, JSON.stringify(log, null, 2));
}

// ── Hash password
function hashPassword(pwd, salt) {
  return crypto.pbkdf2Sync(pwd, salt, 10000, 32, 'sha256').toString('hex');
}

// ── Generate session token
function generateToken() {
  return crypto.randomBytes(32).toString('hex');
}

// ── Verify keystone token middleware
function keystoneAuth(req, res, next) {
  const token = req.headers['x-keystone-token'] || req.query._kt;
  if (!token || !KEYSTONE_TOKENS[token]) {
    return res.status(401).json({ error: 'Invalid or expired session' });
  }
  const session = KEYSTONE_TOKENS[token];
  if (Date.now() > session.expires) {
    delete KEYSTONE_TOKENS[token];
    return res.status(401).json({ error: 'Session expired' });
  }
  req.keystoneUser = session;
  next();
}

// ── Admin only middleware
function adminOnly(req, res, next) {
  if (!req.keystoneUser || req.keystoneUser.role !== 'admin') {
    return res.status(403).json({ error: 'Admin access required' });
  }
  next();
}

// ── LOGIN — POST /api/keystone/login
app.post('/api/keystone/login', keystoneLimiter, (req, res) => {
  const { password, app: appId = 'empire' } = req.body;
  if (!password) return res.status(400).json({ error: 'Password required' });

  const users = loadUsers();
  const user = users.find(u => u.active);

  // Find user by password match (try all active users)
  let matched = null;
  for (const u of users) {
    if (!u.active) continue;
    const h = hashPassword(password, u.salt);
    if (h === u.hash) {
      // Check app access
      if (u.apps.includes('*') || u.apps.includes(appId)) {
        matched = u;
        break;
      } else {
        auditLog('ACCESS_DENIED', u.id, `App: ${appId}`);
        return res.status(403).json({ error: 'Access denied for this application' });
      }
    }
  }

  if (!matched) {
    auditLog('LOGIN_FAILED', 'unknown', `App: ${appId}`);
    return res.status(401).json({ error: 'Invalid password' });
  }

  // Issue token — 24hr for members, 7 days for admin
  const ttl = matched.role === 'admin' ? 7 * 24 * 60 * 60 * 1000 : 24 * 60 * 60 * 1000;
  const token = generateToken();
  KEYSTONE_TOKENS[token] = {
    userId: matched.id,
    name: matched.name,
    role: matched.role,
    apps: matched.apps,
    expires: Date.now() + ttl,
    app: appId
  };

  // Update last login
  const users2 = loadUsers();
  const idx = users2.findIndex(u => u.id === matched.id);
  if (idx >= 0) { users2[idx].lastLogin = new Date().toISOString(); saveUsers(users2); }

  auditLog('LOGIN_OK', matched.id, `App: ${appId}`);
  console.log(`[KEYSTONE] Login: ${matched.name} → ${appId}`);

  res.json({
    ok: true,
    token,
    name: matched.name,
    role: matched.role,
    apps: matched.apps,
    expires: Date.now() + ttl
  });
});

// ── VERIFY — POST /api/keystone/verify (apps call this on load)
app.post('/api/keystone/verify', (req, res) => {
  const token = req.headers['x-keystone-token'] || req.body.token;
  if (!token || !KEYSTONE_TOKENS[token]) {
    return res.json({ ok: false, reason: 'invalid' });
  }
  const session = KEYSTONE_TOKENS[token];
  if (Date.now() > session.expires) {
    delete KEYSTONE_TOKENS[token];
    return res.json({ ok: false, reason: 'expired' });
  }
  // Check app access
  const appId = req.body.app || 'empire';
  if (!session.apps.includes('*') && !session.apps.includes(appId)) {
    return res.json({ ok: false, reason: 'no_access' });
  }
  res.json({ ok: true, name: session.name, role: session.role });
});

// ── LOGOUT — POST /api/keystone/logout
app.post('/api/keystone/logout', (req, res) => {
  const token = req.headers['x-keystone-token'] || req.body.token;
  if (token && KEYSTONE_TOKENS[token]) {
    auditLog('LOGOUT', KEYSTONE_TOKENS[token].userId, '');
    delete KEYSTONE_TOKENS[token];
  }
  res.json({ ok: true });
});

// ── ADMIN: Get all users — GET /api/keystone/admin/users
app.get('/api/keystone/admin/users', keystoneAuth, adminOnly, (req, res) => {
  const users = loadUsers().map(u => ({
    id: u.id, name: u.name, role: u.role,
    apps: u.apps, active: u.active,
    created: u.created, lastLogin: u.lastLogin
  }));
  res.json({ ok: true, users });
});

// ── ADMIN: Add user — POST /api/keystone/admin/users
app.post('/api/keystone/admin/users', keystoneAuth, adminOnly, (req, res) => {
  const { name, password, role = 'member', apps = ['babel', 'lumen'] } = req.body;
  if (!name || !password) return res.status(400).json({ error: 'Name and password required' });

  const users = loadUsers();
  const id = name.toLowerCase().replace(/[^a-z0-9]/g, '_') + '_' + Date.now().toString(36);

  if (users.find(u => u.name.toLowerCase() === name.toLowerCase())) {
    return res.status(400).json({ error: 'User already exists' });
  }

  const salt = crypto.randomBytes(16).toString('hex');
  const hash = hashPassword(password, salt);
  const newUser = { id, name, role, salt, hash, apps, active: true, created: new Date().toISOString(), lastLogin: null };
  users.push(newUser);
  saveUsers(users);
  auditLog('USER_CREATED', req.keystoneUser.userId, `Created: ${name} | Apps: ${apps.join(',')}`);
  console.log(`[KEYSTONE] User created: ${name}`);
  res.json({ ok: true, id, name });
});

// ── ADMIN: Update user — PATCH /api/keystone/admin/users/:id
app.patch('/api/keystone/admin/users/:id', keystoneAuth, adminOnly, (req, res) => {
  const { id } = req.params;
  const { name, password, role, apps, active } = req.body;
  const users = loadUsers();
  const idx = users.findIndex(u => u.id === id);
  if (idx < 0) return res.status(404).json({ error: 'User not found' });

  if (name !== undefined) users[idx].name = name;
  if (role !== undefined) users[idx].role = role;
  if (apps !== undefined) users[idx].apps = apps;
  if (active !== undefined) users[idx].active = active;
  if (password) {
    const salt = crypto.randomBytes(16).toString('hex');
    users[idx].salt = salt;
    users[idx].hash = hashPassword(password, salt);
    // Revoke all tokens for this user
    Object.keys(KEYSTONE_TOKENS).forEach(t => {
      if (KEYSTONE_TOKENS[t].userId === id) delete KEYSTONE_TOKENS[t];
    });
  }
  saveUsers(users);
  auditLog('USER_UPDATED', req.keystoneUser.userId, `Updated: ${id}`);
  res.json({ ok: true });
});

// ── ADMIN: Revoke user — DELETE /api/keystone/admin/users/:id
app.delete('/api/keystone/admin/users/:id', keystoneAuth, adminOnly, (req, res) => {
  const { id } = req.params;
  if (id === 'zack') return res.status(400).json({ error: 'Cannot revoke admin' });
  const users = loadUsers();
  const idx = users.findIndex(u => u.id === id);
  if (idx < 0) return res.status(404).json({ error: 'User not found' });
  users[idx].active = false;
  saveUsers(users);
  // Kill all their tokens
  Object.keys(KEYSTONE_TOKENS).forEach(t => {
    if (KEYSTONE_TOKENS[t].userId === id) delete KEYSTONE_TOKENS[t];
  });
  auditLog('USER_REVOKED', req.keystoneUser.userId, `Revoked: ${id}`);
  console.log(`[KEYSTONE] User revoked: ${id}`);
  res.json({ ok: true });
});

// ── ADMIN: Audit log — GET /api/keystone/admin/audit
app.get('/api/keystone/admin/audit', keystoneAuth, adminOnly, (req, res) => {
  ensureKeystoneDir();
  let log = [];
  try { log = JSON.parse(fs.readFileSync(KEYSTONE_LOG, 'utf8')); } catch(e) {}
  res.json({ ok: true, log: log.slice(0, 100) });
});

// ── ADMIN: Active sessions — GET /api/keystone/admin/sessions
app.get('/api/keystone/admin/sessions', keystoneAuth, adminOnly, (req, res) => {
  const sessions = Object.entries(KEYSTONE_TOKENS)
    .filter(([t, s]) => Date.now() < s.expires)
    .map(([t, s]) => ({
      tokenPreview: t.slice(0, 8) + '...',
      userId: s.userId, name: s.name, role: s.role,
      app: s.app, expires: new Date(s.expires).toISOString()
    }));
  res.json({ ok: true, sessions });
});

// ── ADMIN: Change own password — POST /api/keystone/admin/change-password
app.post('/api/keystone/admin/change-password', keystoneAuth, (req, res) => {
  const { oldPassword, newPassword } = req.body;
  if (!oldPassword || !newPassword) return res.status(400).json({ error: 'Both passwords required' });
  if (newPassword.length < 8) return res.status(400).json({ error: 'Password must be 8+ characters' });

  const users = loadUsers();
  const idx = users.findIndex(u => u.id === req.keystoneUser.userId);
  if (idx < 0) return res.status(404).json({ error: 'User not found' });

  const checkHash = hashPassword(oldPassword, users[idx].salt);
  if (checkHash !== users[idx].hash) return res.status(401).json({ error: 'Old password incorrect' });

  const newSalt = crypto.randomBytes(16).toString('hex');
  users[idx].salt = newSalt;
  users[idx].hash = hashPassword(newPassword, newSalt);
  saveUsers(users);
  auditLog('PASSWORD_CHANGED', req.keystoneUser.userId, '');
  res.json({ ok: true });
});

// ── Cleanup expired tokens every 30min
setInterval(() => {
  const now = Date.now();
  let cleaned = 0;
  Object.keys(KEYSTONE_TOKENS).forEach(t => {
    if (now > KEYSTONE_TOKENS[t].expires) { delete KEYSTONE_TOKENS[t]; cleaned++; }
  });
  if (cleaned > 0) console.log(`[KEYSTONE] Cleaned ${cleaned} expired tokens`);
}, 30 * 60 * 1000);

// ── SokoScan legacy auth (kept for SokoScan app compatibility)
function authCheck(req, res, next) {
  const token = req.headers['x-ss-token'] || req.query._t;
  if (token !== process.env.APP_PASSKEY) return res.status(401).json({ error: 'Unauthorised' });
  next();
}
app.use('/api', (req, res, next) => {
  if (req.path === '/tutor' || req.path.startsWith('/glottolog') || req.path.startsWith('/keystone')) return next();
  authCheck(req, res, next);
});

// ════════════════════════════════════════════
// FIREBASE PROXY — keeps DB URL off client
// ════════════════════════════════════════════
const FIREBASE_DB = process.env.FIREBASE_DB_URL || 'https://riakoine-babel-default-rtdb.asia-southeast1.firebasedatabase.app';

app.all('/api/firebase/*', async (req, res) => {
  // Require keystone token for write operations
  if (req.method !== 'GET') {
    const kt = req.headers['x-keystone-token'] || req.query._kt;
    if (!kt || !KEYSTONE_TOKENS[kt] || Date.now() > KEYSTONE_TOKENS[kt].expires) {
      return res.status(401).json({ error: 'Auth required for writes' });
    }
  }
  const fbPath = req.params[0];
  const qs = new URLSearchParams();
  if (req.query.shallow) qs.set('shallow', 'true');
  const url = `${FIREBASE_DB}/${fbPath}.json${qs.toString() ? '?' + qs : ''}`;
  try {
    const options = {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(8000)
    };
    if (req.method !== 'GET' && req.body) options.body = JSON.stringify(req.body);
    const r = await fetch(url, options);
    const data = await r.json();
    res.status(r.status).json(data);
  } catch(e) {
    res.status(502).json({ error: 'Firebase proxy error: ' + e.message });
  }
});

// ════════════════════════════════════════════
// SOKOSCAN PROXY ROUTES
// ════════════════════════════════════════════
app.get('/api/finnhub/*', async (req, res) => {
  const p = req.params[0];
  const qs = new URLSearchParams(req.query);
  qs.set('token', process.env.FINNHUB_KEY);
  try {
    const r = await fetch(`https://finnhub.io/api/v1/${p}?${qs}`, { signal: AbortSignal.timeout(6000) });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ error: 'Finnhub timeout' }); }
});

app.get('/api/alphavantage', async (req, res) => {
  const qs = new URLSearchParams(req.query);
  qs.set('apikey', process.env.ALPHAVANTAGE_KEY);
  try {
    const r = await fetch(`https://www.alphavantage.co/query?${qs}`, { signal: AbortSignal.timeout(10000) });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ error: 'AlphaVantage timeout' }); }
});

app.get('/api/polygon/*', async (req, res) => {
  const p = req.params[0];
  const qs = new URLSearchParams(req.query);
  qs.set('apiKey', process.env.POLYGON_KEY);
  try {
    const r = await fetch(`https://api.polygon.io/${p}?${qs}`, { signal: AbortSignal.timeout(8000) });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ error: 'Polygon timeout' }); }
});

app.post('/api/gemini', aiLimiter, async (req, res) => {
  try {
    const r = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=${process.env.GEMINI_KEY}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req.body), signal: AbortSignal.timeout(20000) }
    );
    const d = await r.json();
    if (!r.ok) return res.status(r.status).json({ error: d?.error?.message || 'Gemini error' });
    res.json(d);
  } catch (e) { res.status(502).json({ error: 'Gemini timeout' }); }
});

app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: Math.floor(process.uptime()) + 's',
    keystone: {
      users: (() => { try { return loadUsers().length; } catch(e) { return 0; } })(),
      activeSessions: Object.values(KEYSTONE_TOKENS).filter(s => Date.now() < s.expires).length
    },
    keys: {
      gemini: !!process.env.GEMINI_KEY,
      finnhub: !!process.env.FINNHUB_KEY,
      alphavantage: !!process.env.ALPHAVANTAGE_KEY,
      polygon: !!process.env.POLYGON_KEY,
      groq: !!process.env.GROQ_API_KEY,
      firebase: !!process.env.FIREBASE_DB_URL
    }
  });
});

// ════════════════════════════════════════════
// SENTRY ROUTES
// ════════════════════════════════════════════
function sentryAuth(req, res, next) {
  const token = req.headers['x-sentry-token'] || req.query._t;
  if (token !== process.env.SENTRY_PASSKEY && token !== process.env.APP_PASSKEY) {
    return res.status(401).json({ error: 'Unauthorised' });
  }
  next();
}

if (!app.locals.sentryPings)    app.locals.sentryPings    = {};
if (!app.locals.sentryJourneys) app.locals.sentryJourneys = {};
if (!app.locals.offlineTimers)  app.locals.offlineTimers  = {};

function haversine(lat1, lng1, lat2, lng2) {
  const R = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) * Math.sin(dLng/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function eatTime(ts) {
  return new Date(ts).toLocaleString('en-GB', { timeZone: 'Africa/Nairobi', hour12: false,
    day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' }) + ' EAT';
}

async function sendTelegram(chatId, msg) {
  if (!chatId || !process.env.TELEGRAM_BOT_TOKEN) return false;
  try {
    const r = await fetch(
      `https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text: msg, parse_mode: 'HTML', disable_web_page_preview: false }),
        signal: AbortSignal.timeout(8000) }
    );
    const d = await r.json();
    return d.ok;
  } catch (e) { return false; }
}

async function sendWhatsApp(phone, msg) {
  const apiKey = process.env.CALLMEBOT_KEY_KEN;
  if (!apiKey || !phone) return false;
  try {
    const encoded = encodeURIComponent(msg);
    const r = await fetch(
      `https://api.callmebot.com/whatsapp.php?phone=${phone}&text=${encoded}&apikey=${apiKey}`,
      { signal: AbortSignal.timeout(10000) }
    );
    return r.ok;
  } catch (e) { return false; }
}

async function alertAllChannels(msg, mapLink) {
  const results = [];
  const tg1 = await sendTelegram(process.env.TELEGRAM_CHAT_ID, msg);
  results.push({ channel: 'telegram_primary', ok: tg1 });
  if (process.env.TELEGRAM_CHAT_ID_KEN) {
    const tg2 = await sendTelegram(process.env.TELEGRAM_CHAT_ID_KEN, msg);
    results.push({ channel: 'telegram_ken', ok: tg2 });
  }
  if (process.env.CALLMEBOT_KEY_KEN && process.env.KEN_PHONE) {
    const wa = await sendWhatsApp(process.env.KEN_PHONE, msg.replace(/<[^>]+>/g, ''));
    results.push({ channel: 'whatsapp_ken', ok: wa });
  }
  return results;
}

app.post('/sentry/sos', sentryAuth, async (req, res) => {
  const {
    callsign = 'UNKNOWN', lat, lng, accuracy,
    blood = 'UNKNOWN', mode = 'SOS',
    message = '', timestamp = new Date().toISOString(),
    battery = null
  } = req.body;
  const mapsLink = (lat && lng) ? `https://maps.google.com/?q=${lat},${lng}` : null;
  const coordStr = (lat && lng) ? `${parseFloat(lat).toFixed(6)}, ${parseFloat(lng).toFixed(6)}` : 'NO GPS FIX';
  const accuracyStr = accuracy ? `+-${Math.round(accuracy)}m` : 'UNKNOWN';
  const battStr = battery != null ? `${Math.round(battery)}%` : 'UNKNOWN';
  const modeEmoji = mode === 'SOS' ? '🚨' : mode === 'TEST' ? '🧪' : '📍';
  const alertMsg =
`${modeEmoji} <b>SENTRY ${mode} ALERT</b>
👤 <b>Operator:</b> ${callsign}
🩸 <b>Blood Type:</b> ${blood}
🔋 <b>Battery:</b> ${battStr}
📍 <b>Coords:</b> <code>${coordStr}</code>
🎯 <b>Accuracy:</b> ${accuracyStr}
⏰  <b>Time:</b> ${eatTime(timestamp)}
${message ? `💬 <b>Note:</b> ${message}\n` : ''}${mapsLink ? `🗺 <a href="${mapsLink}">OPEN IN MAPS</a>` : ''}
<i>SENTRY Safety — Riakoine-Empire</i>`;
  try {
    const results = await alertAllChannels(alertMsg, mapsLink);
    const ok = results.some(r => r.ok);
    console.log(`[SENTRY] ${mode} from ${callsign} at ${coordStr} | channels:`, results);
    res.json({ ok, channels: results, coords: coordStr });
  } catch (e) {
    console.error('[SENTRY] Alert failed:', e.message);
    res.status(502).json({ ok: false, error: e.message });
  }
});

app.post('/sentry/ping', sentryAuth, async (req, res) => {
  const { callsign = 'UNKNOWN', lat, lng, accuracy, mode = 'safe', battery = null } = req.body;
  if (!app.locals.sentryPings) app.locals.sentryPings = {};
  app.locals.sentryPings[callsign] = { lat, lng, accuracy, mode, battery, time: new Date().toISOString() };
  if (app.locals.offlineTimers[callsign]) {
    clearTimeout(app.locals.offlineTimers[callsign]);
    delete app.locals.offlineTimers[callsign];
  }
  app.locals.offlineTimers[callsign] = setTimeout(async () => {
    const last = app.locals.sentryPings[callsign];
    if (!last) return;
    const mapsLink = (last.lat && last.lng) ? `https://maps.google.com/?q=${last.lat},${last.lng}` : null;
    const msg =
`⚠️ <b>SENTRY — SIGNAL LOST</b>
👤 <b>Operator:</b> ${callsign}
📍 <b>Last Known:</b> <code>${last.lat ? parseFloat(last.lat).toFixed(6)+', '+parseFloat(last.lng).toFixed(6) : 'UNKNOWN'}</code>
🔋 <b>Last Battery:</b> ${last.battery != null ? last.battery+'%' : 'UNKNOWN'}
⏰  <b>Last Ping:</b> ${eatTime(last.time)}
${mapsLink ? `🗺 <a href="${mapsLink}">LAST KNOWN LOCATION</a>` : ''}
<i>No signal for 10 minutes — check in!</i>`;
    await alertAllChannels(msg, mapsLink);
    console.log(`[SENTRY] Dead zone alert for ${callsign}`);
  }, 10 * 60 * 1000);
  console.log(`[SENTRY] Ping ${callsign} | ${lat},${lng} | mode:${mode} | battery:${battery}`);
  res.json({ ok: true });
});

app.post('/sentry/journey/start', sentryAuth, async (req, res) => {
  const { callsign = 'UNKNOWN', name = 'Journey' } = req.body;
  if (!app.locals.sentryJourneys) app.locals.sentryJourneys = {};
  app.locals.sentryJourneys[callsign] = {
    name, callsign, startTime: new Date().toISOString(),
    trail: [], waypoints: [], totalDistance: 0, active: true
  };
  console.log(`[SENTRY] Journey started: ${name} by ${callsign}`);
  res.json({ ok: true, name });
});

app.post('/sentry/journey/ping', sentryAuth, async (req, res) => {
  const { callsign = 'UNKNOWN', lat, lng, accuracy, battery = null } = req.body;
  if (!app.locals.sentryJourneys) return res.status(404).json({ error: 'No journey' });
  const j = app.locals.sentryJourneys[callsign];
  if (!j || !j.active) return res.status(404).json({ error: 'No active journey' });
  const point = { lat: parseFloat(lat), lng: parseFloat(lng), accuracy, battery, time: new Date().toISOString() };
  if (j.trail.length > 0) {
    const last = j.trail[j.trail.length - 1];
    j.totalDistance += haversine(last.lat, last.lng, point.lat, point.lng);
  }
  j.trail.push(point);
  j.lastPing = new Date().toISOString();
  if (!app.locals.sentryPings) app.locals.sentryPings = {};
  app.locals.sentryPings[callsign] = { lat, lng, accuracy, battery, mode: 'journey', time: j.lastPing };
  console.log(`[SENTRY] Journey ping ${callsign} | ${lat},${lng} | dist:${(j.totalDistance/1000).toFixed(2)}km`);
  res.json({ ok: true, points: j.trail.length, totalDistance: j.totalDistance, lastPing: j.lastPing });
});

app.post('/sentry/journey/waypoint', sentryAuth, async (req, res) => {
  const { callsign = 'UNKNOWN', lat, lng, name = 'Waypoint' } = req.body;
  if (!app.locals.sentryJourneys) return res.status(404).json({ error: 'No journey' });
  const j = app.locals.sentryJourneys[callsign];
  if (!j || !j.active) return res.status(404).json({ error: 'No active journey' });
  j.waypoints.push({ lat: parseFloat(lat), lng: parseFloat(lng), name, time: new Date().toISOString() });
  console.log(`[SENTRY] Waypoint: ${name} by ${callsign}`);
  res.json({ ok: true, waypoints: j.waypoints.length });
});

app.post('/sentry/journey/end', sentryAuth, async (req, res) => {
  const { callsign = 'UNKNOWN' } = req.body;
  if (!app.locals.sentryJourneys) return res.status(404).json({ error: 'No journey' });
  const j = app.locals.sentryJourneys[callsign];
  if (!j) return res.status(404).json({ error: 'No journey found' });
  j.active = false;
  j.endTime = new Date().toISOString();
  const durationMs = new Date(j.endTime) - new Date(j.startTime);
  const durationMin = Math.round(durationMs / 60000);
  const summary = {
    name: j.name, distance: Math.round(j.totalDistance),
    points: j.trail.length, waypoints: j.waypoints.length,
    duration: durationMin, startTime: j.startTime, endTime: j.endTime
  };
  console.log(`[SENTRY] Journey ended: ${j.name} | ${(j.totalDistance/1000).toFixed(2)}km | ${durationMin}min`);
  res.json({ ok: true, summary });
});

app.get('/sentry/track/:callsign', (req, res) => {
  const callsign = req.params.callsign;
  const j = (app.locals.sentryJourneys || {})[callsign];
  const p = (app.locals.sentryPings || {})[callsign];
  const trail = j ? j.trail : [];
  const waypoints = j ? j.waypoints : [];
  const lastPing = j ? j.lastPing : (p ? p.time : null);
  const distance = j ? (j.totalDistance/1000).toFixed(2) : '0.00';
  const journeyName = j ? j.name : callsign;
  const active = j ? j.active : false;
  const lastBattery = trail.length ? trail[trail.length-1].battery : (p ? p.battery : null);
  const trailJson = JSON.stringify(trail.map(t => [t.lat, t.lng]));
  const wpJson = JSON.stringify(waypoints);
  res.send(`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SENTRY — Tracking ${callsign}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#020a06;color:#c8ffe0;font-family:'Courier New',monospace;}
#header{padding:12px 16px;background:rgba(2,10,6,0.97);border-bottom:1px solid #0d2b1a;display:flex;justify-content:space-between;align-items:center;}
.logo{font-size:16px;font-weight:900;letter-spacing:4px;color:#00ff88;}
.empire{font-size:8px;letter-spacing:2px;color:#c9a84c;margin-top:1px;}
#map{height:60vh;}
#info{padding:12px 14px;display:grid;grid-template-columns:repeat(2,1fr);gap:8px;}
.stat{background:#071510;border:1px solid #0d2b1a;border-radius:4px;padding:10px;}
.stat-label{font-size:7px;color:#4a8060;letter-spacing:1px;margin-bottom:3px;}
.stat-val{font-size:16px;font-weight:700;color:#c8ffe0;}
#status{padding:8px 14px;font-size:9px;color:#4a8060;letter-spacing:1px;border-top:1px solid #0d2b1a;text-align:center;}
</style>
</head>
<body>
<div id="header">
  <div><div class="logo">SENTRY</div><div class="empire">RIAKOINE-EMPIRE</div></div>
  <div style="text-align:right;font-size:9px;color:#4a8060;">TRACKING<br><span style="color:#c8ffe0;font-size:12px;">${callsign}</span></div>
</div>
<div id="map"></div>
<div id="info">
  <div class="stat"><div class="stat-label">DISTANCE</div><div class="stat-val" id="sDist">${distance} km</div></div>
  <div class="stat"><div class="stat-label">WAYPOINTS</div><div class="stat-val">${waypoints.length}</div></div>
  <div class="stat"><div class="stat-label">TRAIL POINTS</div><div class="stat-val">${trail.length}</div></div>
  <div class="stat"><div class="stat-label">BATTERY</div><div class="stat-val" id="sBatt">${lastBattery != null ? lastBattery+'%' : '--'}</div></div>
</div>
<div id="status">${active ? 'LIVE — auto-refreshes every 30s' : 'JOURNEY ENDED'} | Last ping: ${lastPing ? new Date(lastPing).toLocaleTimeString('en-GB',{timeZone:'Africa/Nairobi',hour:'2-digit',minute:'2-digit'})+' EAT' : 'Never'}</div>
<script>
var trail=${trailJson};var waypoints=${wpJson};var callsign='${callsign}';var active=${active};
var map=L.map('map',{attributionControl:false});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
var polyline=L.polyline(trail,{color:'#00ff88',weight:4,opacity:0.8}).addTo(map);
var marker=null;
if(trail.length){var last=trail[trail.length-1];marker=L.circleMarker([last[0],last[1]],{radius:12,fillColor:'#00ff88',fillOpacity:1,color:'#020a06',weight:3}).addTo(map).bindPopup(callsign+' — LIVE').openPopup();map.setView([last[0],last[1]],14);}
else{map.setView([-1.286389,36.817223],10);}
waypoints.forEach(function(w){L.marker([w.lat,w.lng],{icon:L.divIcon({className:'',html:'<div style="background:#ffcc00;color:#020a06;padding:3px 6px;border-radius:3px;font-size:9px;font-weight:700;white-space:nowrap;">'+w.name+'</div>',iconAnchor:[0,0]})}).addTo(map);});
if(active){setInterval(function(){fetch('/sentry/track-data/'+callsign).then(function(r){return r.json();}).then(function(d){if(d.trail&&d.trail.length){var pts=d.trail.map(function(t){return[t.lat,t.lng];});polyline.setLatLngs(pts);var last=pts[pts.length-1];if(marker)marker.setLatLng(last);else marker=L.circleMarker(last,{radius:12,fillColor:'#00ff88',fillOpacity:1,color:'#020a06',weight:3}).addTo(map);map.setView(last,map.getZoom());}if(d.distance!=null)document.getElementById('sDist').textContent=(d.distance/1000).toFixed(2)+' km';if(d.battery!=null)document.getElementById('sBatt').textContent=d.battery+'%';}).catch(function(){});},30000);}
</script>
</body>
</html>`);
});

app.get('/sentry/track-data/:callsign', (req, res) => {
  const callsign = req.params.callsign;
  const j = (app.locals.sentryJourneys || {})[callsign];
  const p = (app.locals.sentryPings || {})[callsign];
  if (!j && !p) return res.status(404).json({ error: 'Not found' });
  res.json({
    trail: j ? j.trail : [],
    waypoints: j ? j.waypoints : [],
    distance: j ? j.totalDistance : 0,
    active: j ? j.active : false,
    lastPing: j ? j.lastPing : (p ? p.time : null),
    battery: j && j.trail.length ? j.trail[j.trail.length-1].battery : (p ? p.battery : null)
  });
});

app.post('/sentry/buddy-ping', sentryAuth, async (req, res) => {
  const { from = 'Buddy', callsign = 'UNKNOWN' } = req.body;
  const msg = `🔔 <b>BUDDY CHECK</b>\n👤 ${from} is asking ${callsign} to check in!\n⏰  ${eatTime(new Date().toISOString())}`;
  const ok = await sendTelegram(process.env.TELEGRAM_CHAT_ID, msg);
  res.json({ ok });
});

app.get('/sentry/health', (req, res) => {
  res.json({
    status: 'ok', sentry: true,
    telegram: !!process.env.TELEGRAM_BOT_TOKEN,
    whatsapp: !!process.env.CALLMEBOT_KEY_KEN,
    pings: Object.keys(app.locals.sentryPings || {}).length,
    journeys: Object.keys(app.locals.sentryJourneys || {}).length
  });
});

// ════════════════════════════════════════════
// GLOTTOLOG
// ════════════════════════════════════════════
let glottologData = null;
function getGlottolog() {
  if (!glottologData) {
    glottologData = JSON.parse(fs.readFileSync('/var/www/sokoscan/data/glottolog/index.json','utf8'));
  }
  return glottologData;
}

app.get('/api/glottolog/search', (req, res) => {
  const q = (req.query.q||'').toLowerCase().trim();
  if(!q||q.length<2) return res.json([]);
  const data = getGlottolog();
  const results = data.filter(l =>
    l.name.toLowerCase().includes(q) ||
    (l.iso&&l.iso.toLowerCase()===q) ||
    (l.glottocode&&l.glottocode.toLowerCase()===q)
  ).slice(0,20);
  res.json(results);
});

app.get('/api/glottolog/stats', (req, res) => {
  const data = getGlottolog();
  const stats = {};
  data.forEach(l => { if(l.macroarea) stats[l.macroarea]=(stats[l.macroarea]||0)+1; });
  res.json({total:data.length, byMacroarea:stats});
});

// ════════════════════════════════════════════
// GROQ AI PROXY
// ════════════════════════════════════════════
app.post('/api/tutor', async (req, res) => {
  try {
    const { model, messages, system, max_tokens } = req.body;
    const groqMessages = system
      ? [{ role: 'system', content: system }, ...messages]
      : messages;
    const response = await nodeFetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + process.env.GROQ_API_KEY
      },
      body: JSON.stringify({
        model: model || 'llama-3.3-70b-versatile',
        max_tokens: max_tokens || 1000,
        messages: groqMessages
      })
    });
    const data = await response.json();
    res.json(data);
  } catch(e) {
    res.status(500).json({error:{message:'Proxy error: '+e.message}});
  }
});

// ════════════════════════════════════════════
// STATIC + CATCHALL
// ════════════════════════════════════════════
app.use(express.static(path.join(__dirname, 'public')));
app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));

app.listen(PORT, () => {
  console.log(`RIAKOINE-EMPIRE running on port ${PORT}`);
  console.log(`[KEYSTONE] Auth system active`);
  loadUsers(); // seed on startup if needed
});
