const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = Number(process.env.PORT || 3000);
const MONGO_URI = process.env.MONGO_URI;
const ADMIN_TELEGRAM_ID = Number(process.env.ADMIN_TELEGRAM_ID || 5512316636);

app.disable('x-powered-by');
app.use(cors({ origin: process.env.CORS_ORIGIN ? process.env.CORS_ORIGIN.split(',') : true }));
app.use(express.json({ limit: '256kb' }));

const userSchema = new mongoose.Schema({
  telegramId: { type: Number, required: true, unique: true, index: true },
  username: { type: String, default: 'Анонім', maxlength: 64 },
  firstName: { type: String, default: 'Гравець', maxlength: 128 },
  money: { type: Number, default: 0, min: 0 },
  tapPower: { type: Number, default: 1, min: 1 },
  energy: { type: Number, default: 1000, min: 0 },
  maxEnergy: { type: Number, default: 1000, min: 1 },
  energyDrain: { type: Number, default: 5, min: 1 },
  energyRegen: { type: Number, default: 3, min: 0 },
  passiveIncome: { type: Number, default: 0, min: 0 },
  totalTaps: { type: Number, default: 0, min: 0 },
  playerLevel: { type: Number, default: 1, min: 1 },
  playerXP: { type: Number, default: 0, min: 0 },
  maxCombo: { type: Number, default: 1, min: 1 },
  loginStreak: { type: Number, default: 0, min: 0 },
  lastDailyClaim: { type: String, default: '' },
  lastSpinDate: { type: String, default: '' },
  spinsUsedToday: { type: Number, default: 0, min: 0 },
  cards: { type: [mongoose.Schema.Types.Mixed], default: [] },
  collectionItems: { type: [mongoose.Schema.Types.Mixed], default: [] },
  heroes: { type: [mongoose.Schema.Types.Mixed], default: [] },
  selectedHero: { type: Number, default: 0 },
  heroSouls: { type: Number, default: 0 },
  upgrades: { type: mongoose.Schema.Types.Mixed, default: {} },
  activeBoosts: { type: mongoose.Schema.Types.Mixed, default: {} },
  rpgInventory: { type: [mongoose.Schema.Types.Mixed], default: [] },
  equipped: { type: mongoose.Schema.Types.Mixed, default: {} },
  trophies: { type: Number, default: 0, min: 0 },
  fightCount: { type: Number, default: 0, min: 0 },
  pendingEnemy: { type: mongoose.Schema.Types.Mixed, default: null },
  isBanned: { type: Boolean, default: false },
  isAdmin: { type: Boolean, default: false },
  lastUpdate: { type: Date, default: Date.now }
}, { versionKey: false });

const User = mongoose.models.User || mongoose.model('User', userSchema);
const memoryUsers = new Map();
const dbReady = () => mongoose.connection.readyState === 1;
const validId = value => Number.isSafeInteger(Number(value)) && Number(value) > 0;

const RARITIES = {
  common: { label: 'Звичайний', color: '#aeb8c8', multiplier: 1, chance: 58 },
  uncommon: { label: 'Незвичайний', color: '#54d47b', multiplier: 1.35, chance: 24 },
  rare: { label: 'Рідкісний', color: '#54a7ff', multiplier: 1.8, chance: 11 },
  epic: { label: 'Епічний', color: '#b875ff', multiplier: 2.5, chance: 5.5 },
  legendary: { label: 'Легендарний', color: '#ffb648', multiplier: 3.7, chance: 1.35 },
  mythic: { label: 'Міфічний', color: '#ff5f8f', multiplier: 5.5, chance: 0.15 }
};
const EQUIP_SLOTS = ['weapon', 'armor', 'accessory'];
const PREFIXES = ['Попелястий', 'Срібний', 'Крижаний', 'Грозовий', 'Тіньовий', 'Сонячний', 'Багряний', 'Зоряний', 'Драконячий', 'Безсмертний', 'Рунічний', 'Небесний'];
const WEAPON_BASES = ['Клинок', 'Меч', 'Сокира', 'Спис', 'Кинджал', 'Лук', 'Арбалет', 'Посох', 'Молот', 'Коса', 'Катана', 'Жезл'];
const ARMOR_BASES = ['Шкіряний обладунок', 'Кольчуга', 'Панцир', 'Шолом', 'Плащ', 'Наручі', 'Черевики', 'Мантія', 'Кіраса', 'Щит', 'Наплічники', 'Рукавиці'];
const ACCESSORY_BASES = ['Амулет', 'Перстень', 'Талісман', 'Медальйон', 'Оберіг', 'Корона', 'Реліквія', 'Печатка', 'Кристал', 'Підвіска'];
const ICONS = { weapon: ['⚔️', '🗡️', '🏹', '🔨', '🪄'], armor: ['🛡️', '🥋', '⛑️', '🧥', '🥾'], accessory: ['💍', '📿', '👑', '🔮', '🧿'] };
// Ціни в магазині спорядження, у трофеях (валюта, що заробляється в боях).
const SHOP_PRICES = { common: 60, uncommon: 180, rare: 500, epic: 1400, legendary: 4000, mythic: 12000 };
function shopPrice(item) { return SHOP_PRICES[item.rarity] || SHOP_PRICES.common; }

function createCatalog() {
  const catalog = [];
  const add = (slot, bases, offset) => PREFIXES.forEach((prefix, pi) => bases.forEach((base, bi) => {
    const tier = Math.floor((pi * bases.length + bi) / 16);
    const rarity = ['common', 'uncommon', 'rare', 'epic', 'legendary', 'mythic'][Math.min(5, tier)];
    const power = Math.round((8 + pi * 5 + bi * 3 + offset) * RARITIES[rarity].multiplier);
    catalog.push({ id: `${slot}-${pi + 1}-${bi + 1}`, slot, name: `${prefix} ${base}`, rarity, icon: ICONS[slot][bi % ICONS[slot].length], attack: slot === 'weapon' ? power : 0, defense: slot === 'armor' ? power : 0, vitality: slot === 'accessory' ? power * 4 : 0 });
  }));
  add('weapon', WEAPON_BASES, 3); add('armor', ARMOR_BASES, 1); add('accessory', ACCESSORY_BASES, 0);
  return catalog;
}
const CATALOG = createCatalog(); // 408 unique pieces of equipment.
const byId = new Map(CATALOG.map(item => [item.id, item]));
const starterInventory = () => ['weapon-1-1', 'armor-1-1', 'accessory-1-1'];

function defaults(telegramId, username, firstName) {
  return { telegramId, username: username || 'Анонім', firstName: firstName || 'Гравець', money: 0, tapPower: 1, energy: 1000, maxEnergy: 1000, energyDrain: 5, energyRegen: 3, passiveIncome: 0, totalTaps: 0, playerLevel: 1, playerXP: 0, maxCombo: 1, loginStreak: 0, lastDailyClaim: '', lastSpinDate: '', spinsUsedToday: 0, cards: [], collectionItems: [], heroes: [], selectedHero: 0, heroSouls: 0, upgrades: {}, activeBoosts: {}, rpgInventory: starterInventory(), equipped: { weapon: 'weapon-1-1', armor: 'armor-1-1', accessory: 'accessory-1-1' }, fightCount: 0, trophies: 0, isBanned: false, isAdmin: telegramId === ADMIN_TELEGRAM_ID };
}
function ensureRpg(user) {

    if (!Array.isArray(user.rpgInventory)) {
        user.rpgInventory = [];
    }

    // залишаємо тільки існуючі предмети
    user.rpgInventory = user.rpgInventory.filter(item => {
        const id = typeof item === "object" ? item.id : item;
        return byId.has(id);
    });

    // стартовий інвентар
    if (user.rpgInventory.length === 0) {
        user.rpgInventory = starterInventory();
    }

    if (!user.equipped || typeof user.equipped !== "object") {
        user.equipped = {};
    }

    for (const slot of EQUIP_SLOTS) {

        const currentId = user.equipped[slot];

        if (currentId && byId.has(currentId)) {

            const item = byId.get(currentId);

            if (item.slot === slot) continue;

        }

        const found = user.rpgInventory.find(entry => {

            const id = typeof entry === "object"
                ? entry.id
                : entry;

            const item = byId.get(id);

            return item && item.slot === slot;

        });

        user.equipped[slot] = found
            ? (typeof found === "object" ? found.id : found)
            : null;
    }

    markEquippedDirty(user);
}
async function findOrCreate(telegramId, username, firstName) {
    if (!dbReady()) {
        const user = memoryUsers.get(telegramId) || defaults(telegramId, username, firstName);
        user.username = username || user.username;
        user.firstName = firstName || user.firstName;
        ensureRpg(user);
        memoryUsers.set(telegramId, user);
        return user;
    }

    const setFields = {};
    if (username) setFields.username = username;
    if (firstName) setFields.firstName = firstName;

    // Mongo забороняє одному й тому ж полю бути одночасно в $set і $setOnInsert
    // ("Updating the path 'username' would create a conflict at 'username'", code 40).
    // Тому все, що йде в $set, прибираємо з дефолтів для $setOnInsert.
    const insertDefaults = defaults(telegramId, username, firstName);
    delete insertDefaults.username;
    delete insertDefaults.firstName;
    if (!username) insertDefaults.username = 'Анонім';
    if (!firstName) insertDefaults.firstName = 'Гравець';

    const update = { $setOnInsert: insertDefaults };

    // Додаємо $set тільки якщо є що оновлювати
    if (Object.keys(setFields).length > 0) {
        update.$set = setFields;
    }

    const user = await User.findOneAndUpdate(
        { telegramId },
        update,
        {
            new: true,
            upsert: true,
            setDefaultsOnInsert: true
        }
    );

    ensureRpg(user);
    return user;
}
async function persist(user) { user.lastUpdate = new Date(); if (dbReady()) await user.save(); else memoryUsers.set(user.telegramId, user); }
// `equipped` — це Schema.Types.Mixed, Mongoose НЕ бачить вкладені зміни (user.equipped[slot] = ...),
// відстежується лише пряме присвоєння всього поля. Без markModified user.save() мовчки не пише
// оновлену екіпіровку в базу — саме тому "екіпіровано" не тримається після перезаходу в сумку.
function markEquippedDirty(user) { if (typeof user.markModified === 'function') user.markModified('equipped'); }
function publicUser(user) { const source = user.toObject ? user.toObject() : user; return { ...source, isAdmin: Number(source.telegramId) === ADMIN_TELEGRAM_ID || Boolean(source.isAdmin) }; }
// Мапимо збережений предмет на дані каталогу: якщо запис старий/неповний (наприклад,
// зіпсований запис без icon зі старої системи випадкового луту), підмішуємо відсутні поля
// з каталогу, щоб іконка й назва завжди були коректними.
function inventoryState(user) {ensureRpg(user);return {items: user.rpgInventory.map(item => {if(typeof item === "object") {const base = byId.get(item.id); return base ? {...base, ...item, icon: item.icon || base.icon} : item;}return byId.get(item) || null;}).filter(Boolean),equipped: user.equipped || {},totalCatalogItems: CATALOG.length};}
function combatStats(user) { ensureRpg(user); const items = EQUIP_SLOTS.map(slot => byId.get(user.equipped[slot])).filter(Boolean); return { attack: 15 + Math.floor(user.playerLevel * 2.4) + items.reduce((sum, item) => sum + item.attack, 0), defense: 4 + Math.floor(user.playerLevel * 1.1) + items.reduce((sum, item) => sum + item.defense, 0), hp: 100 + user.playerLevel * 12 + items.reduce((sum, item) => sum + item.vitality, 0) }; }
const ENEMIES = [
  { name: 'Піщаний гоблін', icon: '👺', factor: 1 },
  { name: 'Дикий кабан', icon: '🐗', factor: 1.15 },
  { name: 'Нічний вовк', icon: '🐺', factor: 1.35 },
  { name: 'Болотний тролль', icon: '🧌', factor: 1.5 },
  { name: 'Крижаний лицар', icon: '🧊', factor: 1.7 },
  { name: 'Кістяний воїн', icon: '💀', factor: 1.9 },
  { name: 'Тіньовий маг', icon: '🧙', factor: 2.2 },
  { name: 'Гірський велетень', icon: '🗿', factor: 2.5 },
  { name: 'Демон безодні', icon: '👹', factor: 2.8 },
  { name: 'Дракон арени', icon: '🐉', factor: 3.1 },
  { name: 'Володар пекла', icon: '😈', factor: 3.6 },
  { name: 'Прадавній Левіафан', icon: '🐲', factor: 4.2 }
];
function requireAdmin(req, res, next) { if (Number(req.params.id || req.body.adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: 'Недостатньо прав' }); next(); }
// Генерує ворога під поточний рівень гравця (без прив'язки до конкретного бою — використовується і для розвідки, і для самого бою).
function rollEnemy(user) { const template = ENEMIES[Math.floor(Math.random() * Math.min(ENEMIES.length, 1 + Math.ceil(user.playerLevel / 6)))]; const scale = template.factor * (1 + Math.max(0, user.playerLevel - 1) * 0.16); return { ...template, hp: Math.round(72 * scale), attack: Math.round(8 * scale), reward: Math.round(120 * scale) }; }
// Один прогін бою (та сама формула, що й раніше, винесена окремо, щоб її можна було
// прогнати кілька разів наперед для оцінки шансів на перемогу).
function simulateBattle(stats, enemy) { let heroHp = stats.hp, enemyHp = enemy.hp, rounds = 0; while (heroHp > 0 && enemyHp > 0 && rounds++ < 40) { enemyHp -= Math.max(1, stats.attack + Math.floor(Math.random() * 8) - 3); if (enemyHp > 0) heroHp -= Math.max(1, enemy.attack - stats.defense + Math.floor(Math.random() * 4)); } return { win: heroHp > 0, heroHp, enemyHp, rounds }; }
// Приблизний шанс на перемогу для екрану розвідки — прогін бою наперед кілька разів (без впливу на реальний бій).
function estimateWinChance(stats, enemy, trials = 30) { let wins = 0; for (let i = 0; i < trials; i++) if (simulateBattle(stats, enemy).win) wins++; return Math.round((wins / trials) * 100); }
const saveableKeys = ['money','tapPower','energy','maxEnergy','energyDrain','energyRegen','passiveIncome','totalTaps','playerLevel','playerXP','maxCombo','loginStreak','lastDailyClaim','lastSpinDate','spinsUsedToday','cards','collectionItems','heroes','selectedHero','heroSouls','upgrades','activeBoosts'];

app.get('/healthz', (_req, res) => res.json({ ok: true, database: dbReady() ? 'connected' : 'memory-fallback', catalogItems: CATALOG.length }));
app.post('/api/user/sync', async (req,res,next)=>{try{const {telegramId,username,firstName}=req.body || {};if(!validId(telegramId))return res.status(400).json({error:"Некоректний Telegram ID"});const user = await findOrCreate(Number(telegramId),username,firstName);ensureRpg(user);await persist(user);if(user.isBanned)return res.status(403).json({banned:true});res.json(publicUser(user));}catch(error){next(error);}});
app.post('/api/user/save', async (req, res, next) => { try { if (!validId(req.body?.telegramId)) return res.status(400).json({ error: 'Некоректний Telegram ID' }); const user = await findOrCreate(Number(req.body.telegramId)); if (user.isBanned) return res.status(403).json({ error: 'Доступ заборонено' }); for (const key of saveableKeys) if (Object.hasOwn(req.body, key)) user[key] = req.body[key]; await persist(user); res.json({ success: true }); } catch (error) { next(error); } });
app.get('/api/leaderboard', async (_req, res, next) => { try { const users = dbReady() ? await User.find({ isBanned: false }).sort({ money: -1 }).limit(50).select('telegramId firstName username money playerLevel totalTaps') : [...memoryUsers.values()].filter(user => !user.isBanned).sort((a,b) => b.money - a.money).slice(0, 50); res.json(users.map(publicUser)); } catch (error) { next(error); } });
app.get('/api/rpg/:telegramId', async(req,res,next)=>{try{const id = Number(req.params.telegramId);if(!validId(id))return res.status(400).json({error:"Некоректний Telegram ID"});const user = await findOrCreate(id);ensureRpg(user);await persist(user);res.json({success:true,...inventoryState(user),stats:combatStats(user),trophies:user.trophies || 0});}catch(error){next(error);}});
app.get('/api/shop/:telegramId', async (req, res, next) => { try { const id = Number(req.params.telegramId); if (!validId(id)) return res.status(400).json({ error: 'Некоректний Telegram ID' }); const user = await findOrCreate(id); ensureRpg(user); const owned = new Set(user.rpgInventory.map(entry => typeof entry === 'object' ? entry.id : entry)); const items = CATALOG.map(item => ({ ...item, price: shopPrice(item), owned: owned.has(item.id) })); res.json({ success: true, items, trophies: user.trophies || 0 }); } catch (error) { next(error); } });
app.post('/api/shop/buy', async (req, res, next) => { try { const { telegramId, itemId } = req.body || {}; if (!validId(telegramId) || !byId.has(itemId)) return res.status(400).json({ error: 'Некоректний предмет' }); const user = await findOrCreate(Number(telegramId)); ensureRpg(user); if (user.isBanned) return res.status(403).json({ error: 'Доступ заборонено' }); const item = byId.get(itemId); const price = shopPrice(item); const alreadyOwned = user.rpgInventory.some(entry => (typeof entry === 'object' ? entry.id : entry) === itemId); if (alreadyOwned) return res.status(409).json({ error: 'Цей предмет вже у вашому арсеналі' }); if ((user.trophies || 0) < price) return res.status(402).json({ error: 'Недостатньо трофеїв' }); user.trophies -= price; user.rpgInventory.push({ ...item }); if (typeof user.markModified === 'function') user.markModified('rpgInventory'); await persist(user); res.json({ success: true, ...inventoryState(user), stats: combatStats(user), trophies: user.trophies || 0 }); } catch (error) { next(error); } });
app.post('/api/rpg/equip', async (req, res, next) => { try { const { telegramId, itemId } = req.body || {}; if (!validId(telegramId) || !byId.has(itemId)) return res.status(400).json({ error: 'Некоректний предмет' }); const user = await findOrCreate(Number(telegramId)); ensureRpg(user); if (!user.rpgInventory.some(item => {if(typeof item==="object")return item.id===itemId;

return item===itemId;})) return res.status(403).json({ error: 'Предмет відсутній в інвентарі' }); user.equipped[byId.get(itemId).slot] = itemId; markEquippedDirty(user); await persist(user); res.json({ success: true, ...inventoryState(user), stats: combatStats(user) }); } catch (error) { next(error); } });
app.get('/api/fight/scout/:telegramId', async (req, res, next) => { try { const id = Number(req.params.telegramId); if (!validId(id)) return res.status(400).json({ error: 'Некоректний Telegram ID' }); const user = await findOrCreate(id); ensureRpg(user); if (user.isBanned) return res.status(403).json({ error: 'Доступ заборонено' }); const stats = combatStats(user); const enemy = rollEnemy(user); user.pendingEnemy = enemy; await persist(user); const winChance = estimateWinChance(stats, enemy); res.json({ success: true, stats, enemy, winChance }); } catch (error) { next(error); } });
app.post('/api/fight', async (req, res, next) => { try { const { telegramId } = req.body || {}; if (!validId(telegramId)) return res.status(400).json({ error: 'Некоректний ID' }); const user = await findOrCreate(Number(telegramId));ensureRpg(user); if (user.isBanned) return res.status(403).json({ error: 'Доступ заборонено' }); const stats = combatStats(user);
  // Якщо гравець щойно "розвідав" суперника (екран перед боєм), б'ємось саме з ним, а не з новим випадковим —
  // інакше показана заздалегідь статистика не відповідала б реальному бою.
  const enemy = (user.pendingEnemy && user.pendingEnemy.name) ? user.pendingEnemy : rollEnemy(user); user.pendingEnemy = null;
  const { win, heroHp, enemyHp, rounds } = simulateBattle(stats, enemy);
  let trophyReward = 0; if (win) { user.money += enemy.reward; user.playerXP += Math.round(enemy.reward * 0.65); while (user.playerXP >= user.playerLevel * 1000) { user.playerXP -= user.playerLevel * 1000; user.playerLevel++; } user.fightCount += 1;
  // Лут більше не випадає з боїв напряму — натомість перемога дає трофеї (валюту),
  // яку можна витратити в магазині спорядження на конкретний бажаний предмет.
  // enemy.hp / 72 відновлює той самий "scale", яким генерувався цей ворог у rollEnemy().
  trophyReward = Math.max(1, Math.round(3 + (enemy.hp / 72) * 2.4)); user.trophies = (user.trophies || 0) + trophyReward; }
  await persist(user);
  res.json({ success: true, win, heroHp: Math.max(0, heroHp), enemyHp: Math.max(0, enemyHp), rounds, reward: win ? enemy.reward : 0, enemy, level: user.playerLevel, xp: user.playerXP, money: user.money, trophyReward, trophies: user.trophies || 0, stats: combatStats(user) });} catch (error) { next(error); } });
app.get('/api/admin/users/:id', requireAdmin, async (_req, res, next) => { try { const users = dbReady() ? await User.find().sort({ money: -1 }) : [...memoryUsers.values()].sort((a,b) => b.money - a.money); res.json(users.map(publicUser)); } catch (error) { next(error); } });
app.post('/api/admin/add-money', requireAdmin, async (req, res, next) => { try { const targetId = Number(req.body.targetTelegramId), amount = Number(req.body.amount || 100000); if (!validId(targetId) || !Number.isFinite(amount) || amount <= 0) return res.status(400).json({ error: 'Некоректні дані' }); const user = await findOrCreate(targetId); user.money += amount; await persist(user); res.json({ success: true, newBalance: user.money }); } catch (error) { next(error); } });
app.use(express.static(path.join(__dirname), { index: 'index.html', dotfiles: 'ignore' }));
app.use('/api', (_req, res) => res.status(404).json({ error: 'API route not found' }));
app.use((error, _req, res, _next) => { console.error(error); res.status(500).json({ error: 'Internal server error' }); });
if (MONGO_URI) mongoose.connect(MONGO_URI, { serverSelectionTimeoutMS: 8000 }).then(() => console.log('MongoDB connected')).catch(error => console.error('MongoDB unavailable; memory fallback enabled:', error.message)); else console.warn('MONGO_URI is not set; memory fallback enabled');
const httpServer = app.listen(PORT, '0.0.0.0', () => console.log(`Web server listening on ${PORT}`));
module.exports = { app, httpServer };
