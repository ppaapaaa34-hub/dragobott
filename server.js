const express = require("express");
const mongoose = require("mongoose");
const cors = require("cors");
const path = require("path");

const app = express();
app.use(express.json());
app.use(cors());
app.use(express.static(path.join(__dirname)));

const MONGO_URI = process.env.MONGO_URI;
const ADMIN_TELEGRAM_ID = 5512316636;

if (!MONGO_URI) {
    console.warn("⚠️ MONGO_URI не задано — сервер працює без БД");
} else {
    mongoose.connect(MONGO_URI)
        .then(() => console.log("✅ База даних підключена"))
        .catch(err => console.error("❌ Помилка БД:", err));
}

const UserSchema = new mongoose.Schema({
    telegramId: { type: Number, required: true, unique: true },
    username: String,
    firstName: String,
    money: { type: Number, default: 0 },
    tapPower: { type: Number, default: 1 },
    energy: { type: Number, default: 1000 },
    maxEnergy: { type: Number, default: 1000 },
    energyDrain: { type: Number, default: 5 },
    energyRegen: { type: Number, default: 3 },
    passiveIncome: { type: Number, default: 0 },
    totalTaps: { type: Number, default: 0 },
    playerLevel: { type: Number, default: 1 },
    playerXP: { type: Number, default: 0 },
    maxCombo: { type: Number, default: 1 },
    loginStreak: { type: Number, default: 0 },
    lastDailyClaim: String,
    isBanned: { type: Boolean, default: false },
    isAdmin: { type: Boolean, default: false },
    cards: Array,
    collectionItems: Array,
    upgrades: Object,
    lastUpdate: { type: Date, default: Date.now }
});

const User = mongoose.model("User", UserSchema);

function isDbReady() {
    return mongoose.connection.readyState === 1;
}

app.post("/api/user/sync", async (req, res) => {
    const { telegramId, username, firstName } = req.body;
    if (!telegramId) return res.status(400).json({ error: "Немає Telegram ID" });
    if (!isDbReady()) return res.json({ telegramId, firstName, money: 0, tapPower: 1, isAdmin: telegramId === ADMIN_TELEGRAM_ID });

    try {
        let user = await User.findOne({ telegramId });
        if (!user) {
            user = new User({
                telegramId,
                username: username || "Анонім",
                firstName: firstName || "Гравець",
                isAdmin: telegramId === ADMIN_TELEGRAM_ID
            });
            await user.save();
        } else {
            user.username = username || user.username;
            user.firstName = firstName || user.firstName;
            if (telegramId === ADMIN_TELEGRAM_ID) user.isAdmin = true;
            await user.save();
        }
        if (user.isBanned) return res.status(403).json({ banned: true });
        res.json(user);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post("/api/user/save", async (req, res) => {
    if (!isDbReady()) return res.json({ success: true, offline: true });

    const { telegramId, ...data } = req.body;
    try {
        const user = await User.findOne({ telegramId });
        if (!user || user.isBanned) return res.status(403).json({ error: "Доступ заборонено" });

        Object.assign(user, {
            money: data.money,
            tapPower: data.tapPower,
            energy: data.energy,
            maxEnergy: data.maxEnergy,
            energyDrain: data.energyDrain,
            energyRegen: data.energyRegen,
            passiveIncome: data.passiveIncome,
            totalTaps: data.totalTaps,
            playerLevel: data.playerLevel,
            playerXP: data.playerXP,
            maxCombo: data.maxCombo,
            loginStreak: data.loginStreak,
            lastDailyClaim: data.lastDailyClaim,
            cards: data.cards,
            collectionItems: data.collectionItems,
            upgrades: data.upgrades,
            lastUpdate: Date.now()
        });
        await user.save();
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get("/api/leaderboard", async (req, res) => {
    if (!isDbReady()) return res.json([]);
    try {
        const users = await User.find({ isBanned: false })
            .sort({ money: -1 })
            .limit(50)
            .select("telegramId firstName username money playerLevel totalTaps");
        res.json(users);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get("/api/admin/users/:adminId", async (req, res) => {
    if (parseInt(req.params.adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: "Не адмін" });
    if (!isDbReady()) return res.json([]);
    try {
        const users = await User.find().sort({ money: -1 });
        res.json(users);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post("/api/admin/add-money", async (req, res) => {
    const { adminId, targetTelegramId, amount } = req.body;
    if (parseInt(adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: "Не адмін" });
    try {
        const user = await User.findOne({ telegramId: targetTelegramId });
        if (!user) return res.status(404).json({ error: "Не знайдено" });
        user.money += amount || 100000;
        await user.save();
        res.json({ success: true, newBalance: user.money });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post("/api/admin/toggle-ban", async (req, res) => {
    const { adminId, targetTelegramId } = req.body;
    if (parseInt(adminId) !== ADMIN_TELEGRAM_ID) return res.status(403).json({ error: "Не адмін" });
    try {
        const user = await User.findOne({ telegramId: targetTelegramId });
        if (!user) return res.status(404).json({ error: "Не знайдено" });
        user.isBanned = !user.isBanned;
        await user.save();
        res.json({ success: true, isBanned: user.isBanned });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get("/", (req, res) => {
    res.sendFile(path.join(__dirname, "index.html"));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🐉 Drago Tap Empire v2 — порт ${PORT}`));
