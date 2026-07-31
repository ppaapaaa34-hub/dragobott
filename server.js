const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = Number(process.env.PORT || 3000);
const MONGO_URI = process.env.MONGO_URI;
const ADMIN_TELEGRAM_ID = Number(process.env.ADMIN_TELEGRAM_ID || 5512316636);
const MAX_BODY_SIZE = '256kb';

app.disable('x-powered-by');
app.use(cors({ origin: process.env.CORS_ORIGIN ? process.env.CORS_ORIGIN.split(',') : true }));
app.use(express.json({ limit: MAX_BODY_SIZE }));

const userSchema = new mongoose.Schema({
  telegramId: { type: Number, required: true, unique: true, index: true },
  username: { type: String, default: 'Анонім', maxlength: 64 },
  firstName: { type: String, default: 'Гравець', maxlength: 128 },
  money: { type: Number, default: 0, min: 0 }, tapPower: { type: Number, default: 1, min: 1 },
  energy: { type: Number, default: 1000, min: 0 }, maxEnergy: { type: Number, default: 1000, min: 1 },
  energyDrain: { type: Number, default: 5, min: 1 }, energyRegen: { type: Number, default: 3, min: 0 },
  passiveIncome: { type: Number, default: 0, min: 0 }, totalTaps: { type: Number, default: 0, min: 0 },
  playerLevel: { type: Number, default: 1, min: 1 }, playerXP: { type: Number, default: 0, min: 0 },
  maxCombo: { type: Number, default: 1, min: 1 }, loginStreak: { type: Number, default: 0, min: 0 },
  lastDailyClaim: { type: String, default: '' }, lastSpinDate: { type: String, default: '' }, spinsUsedToday: { type: Number, default: 0, min: 0 },
  isBanned: { type: Boolean, default: false }, isAdmin: { type: Boolean, default: false },
  cards: { type: [mongoose.Schema.Types.Mixed], default: [] }, collectionItems: { type: [mongoose.Schema.Types.Mixed], default: [] },
  upgrades: { type: mongoose.Schema.Types.Mixed, default: {} }, activeBoosts: { type: mongoose.Schema.Types.Mixed, default: {} },
  lastUpdate: { type: Date, default: Date.now }
}, { versionKey: false });
const User = mongoose.models.User || mongoose.model('User', userSchema);

// A short-lived memory store keeps the Mini App usable during a Mongo outage.
const memoryUsers = new Map();
const defaults = (telegramId, username, firstName) => ({ telegramId, username: username || 'Анонім', firstName: firstName || 'Гравець', money: 0, tapPower: 1, energy: 1000, maxEnergy: 1000, energyDrain: 5, energyRegen: 3, passiveIncome: 0, totalTaps: 0, playerLevel: 1, playerXP: 0, maxCombo: 1, loginStreak: 0, lastDailyClaim: '', lastSpinDate: '', spinsUsedToday: 0, cards: [], collectionItems: [], upgrades: {}, activeBoosts: {}, isBanned: false, isAdmin: telegramId === ADMIN_TELEGRAM_ID });
const dbReady = () => mongoose.connection.readyState === 1;
const asId = value => Number(value);
const validId = value => Number.isSafeInteger(asId(value)) && asId(value) > 0;
const publicUser = user => ({ ...((user.toObject && user.toObject()) || user), isAdmin: Number(user.telegramId) === ADMIN_TELEGRAM_ID || Boolean(user.isAdmin) });
const saveableKeys = ['money','tapPower','energy','maxEnergy','energyDrain','energyRegen','passiveIncome','totalTaps','playerLevel','playerXP','maxCombo','loginStreak','lastDailyClaim','lastSpinDate','spinsUsedToday','cards','collectionItems','upgrades','activeBoosts'];

async function findOrCreate(telegramId, username, firstName) {
  if (!dbReady()) {
    const current = memoryUsers.get(telegramId) || defaults(telegramId, username, firstName);
    current.username = username || current.username; 
    current.firstName = firstName || current.firstName;
    memoryUsers.set(telegramId, current); 
    return current;
  }
  
  const setFields = {
    ...(username ? { username } : {}),
    ...(firstName ? { firstName } : {}),
    ...(telegramId === ADMIN_TELEGRAM_ID ? { isAdmin: true } : {})
  };
  
  const insertFields = defaults(telegramId, username, firstName);
  Object.keys(setFields).forEach(key => delete insertFields[key]);
  
  const update = { 
    $set: setFields, 
    $setOnInsert: insertFields 
  };
  
  return User.findOneAndUpdate({ telegramId }, update, { new: true, upsert: true, setDefaultsOnInsert: true });
}

function requireAdmin(req, res, next) { if (asId(req.params.id || req.body.adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: 'Недостатньо прав' }); next(); }

app.get('/healthz', (_req, res) => res.json({ ok: true, database: dbReady() ? 'connected' : 'memory-fallback' }));
app.post('/api/user/sync', async (req, res, next) => { try { const { telegramId, username, firstName } = req.body || {}; if (!validId(telegramId)) return res.status(400).json({ error: 'Некоректний Telegram ID' }); const user = await findOrCreate(asId(telegramId), username, firstName); if (user.isBanned) return res.status(403).json({ banned: true }); res.json(publicUser(user)); } catch (error) { next(error); } });
app.post('/api/user/save', async (req, res, next) => { try { const { telegramId } = req.body || {}; if (!validId(telegramId)) return res.status(400).json({ error: 'Некоректний Telegram ID' }); const user = await findOrCreate(asId(telegramId)); if (user.isBanned) return res.status(403).json({ error: 'Доступ заборонено' }); for (const key of saveableKeys) if (Object.prototype.hasOwnProperty.call(req.body, key)) user[key] = req.body[key]; user.lastUpdate = new Date(); if (dbReady()) await user.save(); else memoryUsers.set(asId(telegramId), user); res.json({ success: true }); } catch (error) { next(error); } });
app.get('/api/leaderboard', async (_req, res, next) => { try { const users = dbReady() ? await User.find({ isBanned: false }).sort({ money: -1 }).limit(50).select('telegramId firstName username money playerLevel totalTaps') : [...memoryUsers.values()].filter(u => !u.isBanned).sort((a,b) => b.money - a.money).slice(0,50); res.json(users.map(publicUser)); } catch (error) { next(error); } });
app.get('/api/admin/users/:id', requireAdmin, async (_req, res, next) => { try { const users = dbReady() ? await User.find().sort({ money: -1 }) : [...memoryUsers.values()].sort((a,b) => b.money - a.money); res.json(users.map(publicUser)); } catch (error) { next(error); } });
app.post('/api/admin/add-money', requireAdmin, async (req, res, next) => { try { const targetId = asId(req.body.targetTelegramId); const amount = Number(req.body.amount || 100000); if (!validId(targetId) || !Number.isFinite(amount) || amount <= 0) return res.status(400).json({ error: 'Некоректні дані' }); const user = await findOrCreate(targetId); user.money += amount; if (dbReady()) await user.save(); else memoryUsers.set(targetId, user); res.json({ success: true, newBalance: user.money }); } catch (error) { next(error); } });
app.post('/api/admin/toggle-ban', requireAdmin, async (req, res, next) => { try { const targetId = asId(req.body.targetTelegramId); if (!validId(targetId)) return res.status(400).json({ error: 'Некоректний Telegram ID' }); const user = await findOrCreate(targetId); user.isBanned = !user.isBanned; if (dbReady()) await user.save(); else memoryUsers.set(targetId, user); res.json({ success: true, isBanned: user.isBanned }); } catch (error) { next(error); } });

app.use(express.static(__dirname, { index: 'index.html', dotfiles: 'ignore' }));
app.use('/api', (_req, res) => res.status(404).json({ error: 'API route not found' }));
app.use((error, _req, res, _next) => { console.error(error); res.status(500).json({ error: 'Internal server error' }); });

if (MONGO_URI) mongoose.connect(MONGO_URI, { serverSelectionTimeoutMS: 8000 }).then(() => console.log('MongoDB connected')).catch(error => console.error('MongoDB unavailable; memory fallback enabled:', error.message));
else console.warn('MONGO_URI is not set; memory fallback enabled');
app.listen(PORT, '0.0.0.0', () => console.log(`Web server listening on ${PORT}`));
