const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');

const app = express();
app.use(express.json());
app.use(cors());

// Зчитуємо MONGO_URI зі змінних оточення (Environment Variables) на Render
const MONGO_URI = process.env.MONGO_URI;

// Ваш Telegram ID (Адмін)
const ADMIN_TELEGRAM_ID = 5512316636;

if (!MONGO_URI) {
    console.error('❌ Помилка: Змінну оточення MONGO_URI не задано!');
} else {
    mongoose.connect(MONGO_URI)
        .then(() => console.log('✅ База даних підключена!'))
        .catch(err => console.error('❌ Помилка підключення до БД:', err));
}

// Модель Користувача
const UserSchema = new mongoose.Schema({
    telegramId: { type: Number, required: true, unique: true },
    username: String,
    firstName: String,
    money: { type: Number, default: 0 },
    tapPower: { type: Number, default: 1 },
    energy: { type: Number, default: 1000 },
    passiveIncome: { type: Number, default: 0 },
    totalTaps: { type: Number, default: 0 },
    isBanned: { type: Boolean, default: false },
    isAdmin: { type: Boolean, default: false },
    cards: Array,
    collectionItems: Array,
    lastUpdate: { type: Date, default: Date.now }
});

const User = mongoose.model('User', UserSchema);

// Синхронізація та вхід
app.post('/api/user/sync', async (req, res) => {
    const { telegramId, username, firstName } = req.body;
    if (!telegramId) return res.status(400).json({ error: "Немає Telegram ID" });

    try {
        let user = await User.findOne({ telegramId });

        if (!user) {
            user = new User({
                telegramId,
                username: username || 'Анонім',
                firstName: firstName || 'Гравець',
                isAdmin: telegramId === ADMIN_TELEGRAM_ID
            });
            await user.save();
        } else {
            user.username = username || user.username;
            user.firstName = firstName || user.firstName;
            if (telegramId === ADMIN_TELEGRAM_ID) user.isAdmin = true;
            await user.save();
        }

        if (user.isBanned) {
            return res.status(403).json({ banned: true, message: "Ваш акаунт заблоковано!" });
        }

        res.json(user);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Збереження прогресу
app.post('/api/user/save', async (req, res) => {
    const { telegramId, money, tapPower, energy, passiveIncome, totalTaps, cards, collectionItems } = req.body;

    try {
        const user = await User.findOne({ telegramId });
        if (!user || user.isBanned) return res.status(403).json({ error: "Доступ заборонено" });

        user.money = money;
        user.tapPower = tapPower;
        user.energy = energy;
        user.passiveIncome = passiveIncome;
        user.totalTaps = totalTaps;
        if (cards) user.cards = cards;
        if (collectionItems) user.collectionItems = collectionItems;
        user.lastUpdate = Date.now();

        await user.save();
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// ================= АДМІН ПАНЕЛЬ =================

// Список всіх гравців
app.get('/api/admin/users/:adminId', async (req, res) => {
    const adminId = parseInt(req.params.adminId);
    if (adminId !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: "Ви не адмін!" });

    try {
        const users = await User.find().sort({ money: -1 });
        res.json(users);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Нарахування грошей
app.post('/api/admin/add-money', async (req, res) => {
    const { adminId, targetTelegramId, amount } = req.body;
    if (parseInt(adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: "Ви не адмін!" });

    try {
        const user = await User.findOne({ telegramId: targetTelegramId });
        if (user) {
            user.money += (amount || 100000);
            await user.save();
            res.json({ success: true, newBalance: user.money });
        } else {
            res.status(404).json({ error: "Користувача не знайдено" });
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// Бан / Розбан
app.post('/api/admin/toggle-ban', async (req, res) => {
    const { adminId, targetTelegramId } = req.body;
    if (parseInt(adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: "Ви не адмін!" });

    try {
        const user = await User.findOne({ telegramId: targetTelegramId });
        if (user) {
            user.isBanned = !user.isBanned;
            await user.save();
            res.json({ success: true, isBanned: user.isBanned });
        } else {
            res.status(404).json({ error: "Користувача не знайдено" });
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 Сервер запущено на порту ${PORT}`));
