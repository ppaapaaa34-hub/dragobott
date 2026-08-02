// Served by the same Express service; a separate host can set window.DRAGO_API_URL.
const SERVER_URL = window.DRAGO_API_URL || window.location.origin;

// ==================== TELEGRAM ====================
let userTelegramId = 5512316636;
let userUsername = "Admin";
let userFirstName = "Drago Boss";
let isAdmin = false;
let serverOnline = false;

if (window.Telegram?.WebApp) {
    Telegram.WebApp.expand();
    Telegram.WebApp.setHeaderColor("#0a0618");
    Telegram.WebApp.setBackgroundColor("#0a0618");
    const tgUser = Telegram.WebApp.initDataUnsafe?.user;
    if (tgUser) {
        userTelegramId = tgUser.id;
        userUsername = tgUser.username || "анонім";
        userFirstName = tgUser.first_name || "Гравець";
        const avatarEl = document.getElementById("user-avatar");
        if (avatarEl && tgUser.photo_url) avatarEl.src = tgUser.photo_url;
    }
}

// ==================== GAME STATE ====================
let money = 0;
let tapPower = 1;
let energy = 1000;
let maxEnergy = 1000;
let energyDrain = 5;
let energyRegen = 3;
let passiveIncome = 0;
let totalTaps = 0;
let playerLevel = 1;
let playerXP = 0;
let combo = 1;
let maxCombo = 1;
let lastTapTime = 0;
let loginStreak = 0;
let lastDailyClaim = "";
let lastSpinDate = "";
let spinsUsedToday = 0;

const activeBoosts = { tap2x: 0, passive2x: 0, energyFull: 0 };

const upgrades = {
    tapPower: { lvl: 0, baseCost: 500, name: "Сила Тапу", icon: "👊", desc: "+1 ₴ за тап", effect: () => { tapPower += 1; } },
    maxEnergy: { lvl: 0, baseCost: 800, name: "Макс. Енергія", icon: "🔋", desc: "+200 ⚡ максимум", effect: () => { maxEnergy += 200; energy = Math.min(energy + 200, maxEnergy); } },
    energyRegen: { lvl: 0, baseCost: 1200, name: "Регенерація", icon: "⚡", desc: "+1 ⚡/с відновлення", effect: () => { energyRegen += 1; } },
    energyEff: { lvl: 0, baseCost: 1500, name: "Економія", icon: "💨", desc: "-1 ⚡ за тап (мін. 1)", effect: () => { if (energyDrain > 1) energyDrain -= 1; } }
};

const cards = [
    { id: 1, name: "Точка з Шаурмою", icon: "🥙", cost: 100, profit: 40, lvl: 0 },
    { id: 2, name: "Кав'ярня", icon: "☕", cost: 500, profit: 220, lvl: 0 },
    { id: 3, name: "Барбершоп", icon: "💈", cost: 2000, profit: 950, lvl: 0 },
    { id: 4, name: "Крипто-Ферма", icon: "⛏️", cost: 8000, profit: 4200, lvl: 0 },
    { id: 5, name: "IT-Холдинг", icon: "💻", cost: 30000, profit: 17000, lvl: 0 },
    { id: 6, name: "Казино Дракона", icon: "🎰", cost: 120000, profit: 75000, lvl: 0 },
    { id: 7, name: "Торговий Центр", icon: "🏢", cost: 500000, profit: 320000, lvl: 0 },
    { id: 8, name: "Нафтова Вишка", icon: "🛢️", cost: 2000000, profit: 1400000, lvl: 0 },
    { id: 9, name: "Авіакомпанія", icon: "✈️", cost: 8000000, profit: 5500000, lvl: 0 },
    { id: 10, name: "Космопорт", icon: "🚀", cost: 30000000, profit: 22000000, lvl: 0 },
    { id: 11, name: "Медіа-Імперія", icon: "📺", cost: 100000000, profit: 80000000, lvl: 0 },
    { id: 12, name: "Банк Дракона", icon: "🏦", cost: 500000000, profit: 450000000, lvl: 0 }
];

const collectionItems = [
    { id: "item_1", name: "Зуб Дракона", rarity: "common", icon: "🦷", cost: 2500, bonus: "+2 ₴ до тапу", owned: false },
    { id: "item_2", name: "Стародавній Амулет", rarity: "common", icon: "📿", cost: 10000, bonus: "+5 ₴ до тапу", owned: false },
    { id: "item_3", name: "Неоновий Шолом", rarity: "rare", icon: "🪖", cost: 35000, bonus: "+5% до пасиву", owned: false },
    { id: "item_4", name: "Смарагдове Яйце", rarity: "rare", icon: "🥚", cost: 100000, bonus: "+15 ₴ до тапу", owned: false },
    { id: "item_5", name: "Око Драго", rarity: "epic", icon: "👁️", cost: 300000, bonus: "+10% до пасиву", owned: false },
    { id: "item_6", name: "Вогняний Меч", rarity: "epic", icon: "⚔️", cost: 800000, bonus: "+50 ₴ до тапу", owned: false },
    { id: "item_7", name: "Корона Імператора", rarity: "legendary", icon: "👑", cost: 2500000, bonus: "+20% до пасиву", owned: false },
    { id: "item_8", name: "Серце Дракона", rarity: "legendary", icon: "💖", cost: 10000000, bonus: "+150 ₴ до тапу", owned: false },
    { id: "item_9", name: "Кристал Вічності", rarity: "rare", icon: "🔮", cost: 500000, bonus: "+25 ₴ до тапу", owned: false },
    { id: "item_10", name: "Крила Фенікса", rarity: "epic", icon: "🪽", cost: 1500000, bonus: "+15% до пасиву", owned: false },
    { id: "item_11", name: "Скіпетр Влади", rarity: "legendary", icon: "🔱", cost: 5000000, bonus: "+100 ₴ до тапу", owned: false },
    { id: "item_12", name: "Душа Імперії", rarity: "legendary", icon: "✨", cost: 50000000, bonus: "+50% до пасиву", owned: false },
    // --- НОВІ ПРЕДМЕТИ ---
    { id: "item_13", name: "Щит Титана", rarity: "epic", icon: "🛡️", cost: 500000000, bonus: "+30% до пасиву", owned: false },
    { id: "item_14", name: "Око Дракона", rarity: "legendary", icon: "👁️‍🗨️", cost: 1500000000, bonus: "+200 ₴ до тапу", owned: false },
    { id: "item_15", name: "Книга Знань", rarity: "rare", icon: "📖", cost: 100000000, bonus: "+10% до пасиву", owned: false },
    { id: "item_16", name: "Плащ Тіней", rarity: "epic", icon: "🧥", cost: 800000000, bonus: "+40% до пасиву", owned: false },
    { id: "item_17", name: "Амулет Сонця", rarity: "rare", icon: "☀️", cost: 250000000, bonus: "+75 ₴ до тапу", owned: false },
    { id: "item_18", name: "Сльоза Фенікса", rarity: "legendary", icon: "💧", cost: 5000000000, bonus: "+100% до пасиву", owned: false },
    { id: "item_19", name: "Меч Долі", rarity: "legendary", icon: "🗡️", cost: 3000000000, bonus: "+300 ₴ до тапу", owned: false },
    { id: "item_20", name: "Кільце Владарювання", rarity: "legendary", icon: "💍", cost: 10000000000, bonus: "+500 ₴ до тапу", owned: false },
    { id: "item_21", name: "Скіпетр Небес", rarity: "legendary", icon: "⚡", cost: 25000000000, bonus: "+750 ₴ до тапу", owned: false },
    { id: "item_22", name: "Чаша Безсмертя", rarity: "legendary", icon: "🏆", cost: 50000000000, bonus: "+200% до пасиву", owned: false },
    { id: "item_23", name: "Посох Архімага", rarity: "legendary", icon: "🪄", cost: 100000000000, bonus: "+1500 ₴ до тапу", owned: false },
    { id: "item_24", name: "Серце Всесвіту", rarity: "legendary", icon: "🌌", cost: 500000000000, bonus: "+500% до пасиву", owned: false }
];

const LEVEL_NAMES = [
    "Новачок", "Учень", "Торговець", "Бізнесмен", "Магнат",
    "Олігарх", "Імператор", "Легенда", "Бог Тапу", "Драко-Бог",
    "Володар TON", "Крипто-Барон", "Галактичний Магнат", "Верховний Дракон", "Титан Мультивсесвіту",
    "Володар Часу", "Повелитель Енергії", "Квантовий Творець", "Галактичний Владика", "Абсолютний Архітектор",
    "Нескінченна Сутність", "Творець Всесвітів", "Провісник Вічності", "Бог Нескінченності", "Альфа і Омега"
];

const DAILY_REWARDS = [
    500, 1000, 2500, 5000, 10000, 25000, 100000,
    250000, 500000, 1000000, 2500000, 5000000, 10000000, 50000000
];

const SPIN_PRIZES = [
    { label: "1K ₴", type: "money", value: 1000 },
    { label: "5K ₴", type: "money", value: 5000 },
    { label: "25K ₴", type: "money", value: 25000 },
    { label: "x2 Тап 5хв", type: "boost", boost: "tap2x", duration: 300 },
    { label: "200 ⚡", type: "energy", value: 200 },
    { label: "100K ₴", type: "money", value: 100000 },
    { label: "1000 ⚡", type: "energy", value: 1000 },
    { label: "x2 Пасив 5хв", type: "boost", boost: "passive2x", duration: 300 },
    { label: "500K ₴", type: "money", value: 500000 },
    { label: "x5 Тап 3хв", type: "boost", boost: "tap5x", duration: 180 },
    { label: "1M ₴", type: "money", value: 1000000 },
    { label: "Full Енергія", type: "energy", value: 5000 }
];

let missions = [];
let achievements = [];

function initMissions() {
    // Впевнись, що функція todayKey() у тебе десь визначена нижче по коду
    const today = typeof todayKey === 'function' ? todayKey() : new Date().toISOString().split('T')[0];
    missions = [
        { id: "m1", icon: "👆", name: "Зроби 100 тапів", target: 100, progress: 0, reward: 2000, claimed: false, track: "taps" },
        { id: "m2", icon: "💰", name: "Зароби 10,000 ₴", target: 10000, progress: 0, reward: 5000, claimed: false, track: "earned" },
        { id: "m3", icon: "🏢", name: "Купи 1 бізнес", target: 1, progress: 0, reward: 3000, claimed: false, track: "business" },
        { id: "m4", icon: "🔥", name: "Досягни комбо x3", target: 3, progress: 0, reward: 4000, claimed: false, track: "combo" },
        { id: "m5", icon: "⚡", name: "Витрати 500 енергії", target: 500, progress: 0, reward: 2500, claimed: false, track: "energy" }
    ];
    const saved = localStorage.getItem(`drago_missions_${today}`);
    if (saved) {
        try { missions = JSON.parse(saved); } catch (_) {}
    }
}

function initAchievements() {
    achievements = [
        { id: "a1", icon: "👆", name: "Перший тап", desc: "100 тапів", unlocked: false, check: () => totalTaps >= 100 },
        { id: "a2", icon: "💰", name: "Багатій", desc: "100K ₴", unlocked: false, check: () => money >= 100000 },
        { id: "a3", icon: "🏢", name: "Підприємець", desc: "5 бізнесів", unlocked: false, check: () => cards.reduce((s, c) => s + c.lvl, 0) >= 5 },
        { id: "a4", icon: "💎", name: "Колекціонер", desc: "3 артефакти", unlocked: false, check: () => collectionItems.filter(i => i.owned).length >= 3 },
        { id: "a5", icon: "🔥", name: "Комбо-Майстер", desc: "Комбо x5", unlocked: false, check: () => maxCombo >= 5 },
        { id: "a6", icon: "⚡", name: "Енерджайзер", desc: "Рівень 5", unlocked: false, check: () => playerLevel >= 5 },
        { id: "a7", icon: "🎁", name: "Вірний", desc: "7 днів серії", unlocked: false, check: () => loginStreak >= 7 },
        { id: "a8", icon: "👑", name: "Імператор", desc: "1M ₴", unlocked: false, check: () => money >= 1000000 },
        { id: "a9", icon: "🚀", name: "Космонавт", desc: "Космопорт", unlocked: false, check: () => cards.find(c => c.id === 10)?.lvl > 0 },
        { id: "a10", icon: "🐉", name: "Драко-Бог", desc: "Рівень 10", unlocked: false, check: () => playerLevel >= 10 },
        { id: "a11", icon: "🌌", name: "Титан Всесвіту", desc: "Рівень 15", unlocked: false, check: () => playerLevel >= 15 },
        { id: "a12", icon: "⚛️", name: "Альфа і Омега", desc: "Рівень 25", unlocked: false, check: () => playerLevel >= 25 }
    ];
}

function todayKey() {
    return new Date().toISOString().slice(0, 10);
}

function xpForLevel(lvl) {
    return Math.floor(1000 * Math.pow(1.35, lvl - 1));
}

function addXP(amount) {
    playerXP += amount;
    while (playerXP >= xpForLevel(playerLevel) && playerLevel < 10) {
        playerXP -= xpForLevel(playerLevel);
        playerLevel++;
        showToast(`🎉 Рівень ${playerLevel}: ${LEVEL_NAMES[playerLevel - 1] || "Бог"}!`);
        if (Telegram?.WebApp?.HapticFeedback) Telegram.WebApp.HapticFeedback.notificationOccurred("success");
    }
}

// ==================== DOM REFS ====================
const moneyEl = document.getElementById("money");
const energyEl = document.getElementById("energy");
const maxEnergyEl = document.getElementById("max-energy");
const energyBar = document.getElementById("energy-bar");
const passiveEl = document.getElementById("passive-income");
const tapBtn = document.getElementById("tap");
const comboDisplay = document.getElementById("combo-display");

// ==================== SAVE / LOAD & OFFLINE REGEN ====================
function getSaveData() {
    return {
        money, tapPower, energy, maxEnergy, energyDrain, energyRegen, passiveIncome, totalTaps,
        playerLevel, playerXP, maxCombo, loginStreak, lastDailyClaim, lastSpinDate, spinsUsedToday,
        cards, collectionItems, upgrades, activeBoosts, achievements: achievements.map(a => ({ id: a.id, unlocked: a.unlocked }))
    };
}

function saveLocalProgress() {
    localStorage.setItem("drago_local_save", JSON.stringify(getSaveData()));
    localStorage.setItem("drago_last_seen", Date.now().toString());
    localStorage.setItem(`drago_missions_${todayKey()}`, JSON.stringify(missions));
}

function loadAndApplyOfflineProgress() {
    // 1. Спочатку завантажуємо дані стану гри
    const saved = localStorage.getItem("drago_local_save");
    if (saved) {
        try {
            const d = JSON.parse(saved);
            money = d.money || 0;
            tapPower = d.tapPower || 1;
            energy = d.energy ?? 1000;
            maxEnergy = d.maxEnergy || 1000;
            energyDrain = d.energyDrain || 5;
            energyRegen = d.energyRegen || 3;
            passiveIncome = d.passiveIncome || 0;
            totalTaps = d.totalTaps || 0;
            playerLevel = d.playerLevel || 1;
            playerXP = d.playerXP || 0;
            maxCombo = d.maxCombo || 1;
            loginStreak = d.loginStreak || 0;
            lastDailyClaim = d.lastDailyClaim || "";
            lastSpinDate = d.lastSpinDate || "";
            spinsUsedToday = d.spinsUsedToday || 0;

            if (d.cards) d.cards.forEach((c, i) => { if (cards[i]) { cards[i].lvl = c.lvl; cards[i].cost = c.cost; } });
            if (d.collectionItems) d.collectionItems.forEach((c, i) => { if (collectionItems[i]) collectionItems[i].owned = c.owned; });
            if (d.upgrades) Object.keys(upgrades).forEach(k => { if (d.upgrades[k]) upgrades[k].lvl = d.upgrades[k].lvl; });
            if (d.activeBoosts) Object.assign(activeBoosts, d.activeBoosts);
            if (d.achievements) d.achievements.forEach(sa => {
                const a = achievements.find(x => x.id === sa.id);
                if (a) a.unlocked = sa.unlocked;
            });
        } catch (e) {
            console.error("Load error:", e);
        }
    }

    // 2. Тепер вираховуємо офлайн прогрес
    const lastSeenRaw = localStorage.getItem("drago_last_seen");
    if (lastSeenRaw) {
        const lastSeen = parseInt(lastSeenRaw, 10);
        if (!isNaN(lastSeen)) {
            const elapsedSec = Math.floor((Date.now() - lastSeen) / 1000);
            if (elapsedSec > 0) {
                // Обмеження до 12 годин
                const cappedSec = Math.min(elapsedSec, 12 * 3600);

                // Нарахування енергії
                if (energy < maxEnergy) {
                    const regenAmount = energyRegen * cappedSec;
                    const before = energy;
                    energy = Math.min(maxEnergy, energy + regenAmount);
                    const diff = Math.floor(energy - before);
                    if (diff > 0) {
                        setTimeout(() => showToast(`⚡ Офлайн відновлено: +${diff} енергії!`), 1000);
                    }
                }

                // Нарахування пасивного доходу
                const passive = getEffectivePassive();
                if (passive > 0) {
                    const earned = (passive / 3600) * cappedSec;
                    if (earned >= 1) {
                        money += earned;
                        setTimeout(() => showToast(`💰 Офлайн дохід: +${formatNum(earned)} ₴`), 1200);
                    }
                }
            }
        }
    }

    // 3. Зберігаємо оновлений час
    saveLocalProgress();
}

// Події закриття та зміни вкладки
window.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        saveLocalProgress();
    } else {
        loadAndApplyOfflineProgress();
        updateUI();
    }
});
window.addEventListener("pagehide", saveLocalProgress);
window.addEventListener("beforeunload", saveLocalProgress);

// ==================== UI ====================
function formatNum(n) {
    if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e4) return (n / 1e3).toFixed(1) + "K";
    return Math.floor(n).toLocaleString();
}

function updateUI() {
    if (moneyEl) moneyEl.innerText = formatNum(money) + " ₴";
    if (energyEl) energyEl.innerText = Math.floor(energy);
    if (maxEnergyEl) maxEnergyEl.innerText = maxEnergy;
    if (energyBar) energyBar.style.width = Math.max(0, energy / maxEnergy * 100) + "%";
    if (passiveEl) passiveEl.innerText = formatNum(getEffectivePassive());

    const xpNeeded = xpForLevel(playerLevel);
    const levelBar = document.getElementById("level-bar");
    const xpCurrent = document.getElementById("xp-current");
    const xpNeededEl = document.getElementById("xp-needed");
    const levelName = document.getElementById("level-name");
    if (levelBar) levelBar.style.width = (playerXP / xpNeeded * 100) + "%";
    if (xpCurrent) xpCurrent.innerText = formatNum(playerXP);
    if (xpNeededEl) xpNeededEl.innerText = formatNum(xpNeeded);
    if (levelName) levelName.innerText = `Рівень ${playerLevel} · ${LEVEL_NAMES[playerLevel - 1] || "Бог"}`;

    const drainText = document.getElementById("energy-drain-text");
    const regenText = document.getElementById("energy-regen-text");
    if (drainText) drainText.innerText = energyDrain;
    if (regenText) regenText.innerText = energyRegen;

    const statTaps = document.getElementById("stat-total-taps");
    const statTapPower = document.getElementById("stat-tap-power");
    const statCombo = document.getElementById("stat-max-combo");
    const statArt = document.getElementById("stat-artifacts-count");
    const statAch = document.getElementById("stat-achievements");
    const statStreak = document.getElementById("stat-streak");

    if (statTaps) statTaps.innerText = totalTaps.toLocaleString();
    if (statTapPower) statTapPower.innerText = getEffectiveTapPower() + " ₴";
    if (statCombo) statCombo.innerText = "x" + maxCombo;
    if (statArt) statArt.innerText = `${collectionItems.filter(i => i.owned).length} / ${collectionItems.length}`;
    if (statAch) statAch.innerText = `${achievements.filter(a => a.unlocked).length} / ${achievements.length}`;
    if (statStreak) statStreak.innerText = loginStreak + " днів";

    const adminContainer = document.getElementById("admin-btn-container");
    if (adminContainer) adminContainer.style.display = (isAdmin || userTelegramId === 5512316636) ? "block" : "none";

    renderActiveBoosts();
    renderCards();
    renderCollection();
    renderBoosts();
    renderMissions();
    renderAchievements();
    checkAchievements();
}

function getEffectiveTapPower() {
    let p = tapPower;
    if (activeBoosts.tap2x > Date.now()) p *= 2;
    return Math.floor(p * combo);
}

function getEffectivePassive() {
    let p = passiveIncome;
    if (activeBoosts.passive2x > Date.now()) p *= 2;
    return p;
}

function renderActiveBoosts() {
    const el = document.getElementById("active-boosts");
    if (!el) return;
    el.innerHTML = "";
    const now = Date.now();
    if (activeBoosts.tap2x > now) el.innerHTML += `<span class="boost-badge">👊 x2 Тап ${Math.ceil((activeBoosts.tap2x - now) / 1000)}с</span>`;
    if (activeBoosts.passive2x > now) el.innerHTML += `<span class="boost-badge">💰 x2 Пасив ${Math.ceil((activeBoosts.passive2x - now) / 1000)}с</span>`;
}

// ==================== TAP ====================
if (tapBtn) {
    tapBtn.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        if (energy < energyDrain) {
            showToast("⚡ Недостатньо енергії!");
            return;
        }

        const now = Date.now();
        if (now - lastTapTime < 1500) {
            combo = Math.min(5, combo + 0.2);
        } else {
            combo = 1;
        }
        lastTapTime = now;
        maxCombo = Math.max(maxCombo, Math.floor(combo));

        const earned = getEffectiveTapPower();
        money += earned;
        energy -= energyDrain;
        totalTaps++;
        addXP(Math.floor(earned / 2) + 1);

        updateMissionProgress("taps", 1);
        updateMissionProgress("earned", earned);
        updateMissionProgress("energy", energyDrain);
        updateMissionProgress("combo", Math.floor(combo));

        if (comboDisplay) {
            comboDisplay.innerText = `COMBO x${combo.toFixed(1)}`;
            comboDisplay.classList.toggle("active", combo > 1.2);
        }

        createFloatingNumber(e.clientX, e.clientY, `+${formatNum(earned)}`, combo > 1.2);
        createTapParticles(e.clientX, e.clientY);

        if (Telegram?.WebApp?.HapticFeedback) Telegram.WebApp.HapticFeedback.impactOccurred(combo > 2 ? "medium" : "light");

        saveLocalProgress();
        updateUI();
    });
}

function createFloatingNumber(x, y, text, isCombo) {
    const el = document.createElement("div");
    el.className = "float-num" + (isCombo ? " combo" : "");
    el.innerText = text;
    el.style.left = `${x - 20}px`;
    el.style.top = `${y - 30}px`;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 900);
}

function createTapParticles(x, y) {
    const colors = ["#fbbf24", "#a855f7", "#22d3ee", "#f472b6", "#fb923c"];
    for (let i = 0; i < 8; i++) {
        const p = document.createElement("div");
        p.className = "tap-particle";
        p.style.left = x + "px";
        p.style.top = y + "px";
        p.style.background = colors[i % colors.length];
        const angle = (Math.PI * 2 * i) / 8;
        const dist = 40 + Math.random() * 40;
        p.style.setProperty("--tx", Math.cos(angle) * dist + "px");
        p.style.setProperty("--ty", Math.sin(angle) * dist + "px");
        document.body.appendChild(p);
        setTimeout(() => p.remove(), 600);
    }
}

// ==================== GAME LOOP ====================
setInterval(() => {
    if (energy < maxEnergy) energy = Math.min(maxEnergy, energy + energyRegen);
    const passive = getEffectivePassive();
    if (passive > 0) money += passive / 3600;
    if (Date.now() - lastTapTime > 2000 && combo > 1) {
        combo = 1;
        if (comboDisplay) {
            comboDisplay.innerText = "COMBO x1";
            comboDisplay.classList.remove("active");
        }
    }
    // Легке збереження стану без перезапису drago_last_seen
    localStorage.setItem("drago_local_save", JSON.stringify(getSaveData()));
    updateUI();
}, 1000);

// ==================== CARDS ====================
function renderCards() {
    const container = document.getElementById("cards-container");
    if (!container) return;
    container.innerHTML = "";
    cards.forEach(card => {
        const el = document.createElement("div");
        el.className = "card";
        el.innerHTML = `
            <div class="card-icon">${card.icon}</div>
            <div class="card-title">${card.name}</div>
            <div class="card-profit">+${formatNum(card.profit)} ₴/год</div>
            <div class="card-level">Рівень ${card.lvl}</div>
            <button class="card-btn" ${money < card.cost ? "disabled" : ""} onclick="buyCard(${card.id})">${formatNum(card.cost)} ₴</button>`;
        container.appendChild(el);
    });
}

window.buyCard = function(id) {
    const card = cards.find(c => c.id === id);
    if (!card || money < card.cost) return;
    money -= card.cost;
    passiveIncome += card.profit;
    card.lvl++;
    card.cost = Math.floor(card.cost * 1.55);
    updateMissionProgress("business", 1);
    addXP(Math.floor(card.profit / 10));
    showToast(`🏢 ${card.name} — рівень ${card.lvl}!`);
    saveLocalProgress();
    updateUI();
};

// ==================== COLLECTION ====================
const ITEM_BONUSES = {
   item_1: () => { tapPower += 2; },
    item_2: () => { tapPower += 5; },
    item_3: () => { passiveIncome = Math.floor(passiveIncome * 1.05); },
    item_4: () => { tapPower += 15; },
    item_5: () => { passiveIncome = Math.floor(passiveIncome * 1.10); },
    item_6: () => { tapPower += 50; },
    item_7: () => { passiveIncome = Math.floor(passiveIncome * 1.20); },
    item_8: () => { tapPower += 150; },
    item_9: () => { tapPower += 25; },
    item_10: () => { passiveIncome = Math.floor(passiveIncome * 1.15); },
    item_11: () => { tapPower += 100; },
    item_12: () => { passiveIncome = Math.floor(passiveIncome * 1.50); },
    // --- БОНУСИ НОВИХ ПРЕДМЕТІВ ---
    item_13: () => { passiveIncome = Math.floor(passiveIncome * 1.30); },
    item_14: () => { tapPower += 200; },
    item_15: () => { passiveIncome = Math.floor(passiveIncome * 1.10); },
    item_16: () => { passiveIncome = Math.floor(passiveIncome * 1.40); },
    item_17: () => { tapPower += 75; },
    item_18: () => { passiveIncome = Math.floor(passiveIncome * 2.0); },
    item_19: () => { tapPower += 300; },
    item_20: () => { tapPower += 500; },
    item_21: () => { tapPower += 750; },
    item_22: () => { passiveIncome = Math.floor(passiveIncome * 3.0); },
    item_23: () => { tapPower += 1500; },
    item_24: () => { passiveIncome = Math.floor(passiveIncome * 6.0); }
};

function renderCollection() {
    const container = document.getElementById("collection-container");
    if (!container) return;
    container.innerHTML = "";
    const rarityNames = { common: "Звичайний", rare: "Рідкісний", epic: "Епічний", legendary: "Легендарний" };
    collectionItems.forEach(item => {
        const el = document.createElement("div");
        el.className = `item-card ${item.rarity}${item.owned ? " owned" : ""}`;
        el.innerHTML = `
            <span class="item-badge badge-${item.rarity}">${rarityNames[item.rarity]}</span>
            <div class="item-icon">${item.icon}</div>
            <div class="item-name">${item.name}</div>
            <div class="item-bonus">${item.bonus}</div>
            <button class="card-btn" ${item.owned || money < item.cost ? "disabled" : ""} onclick="buyCollectionItem('${item.id}')">
                ${item.owned ? "✨ В колекції" : formatNum(item.cost) + " ₴"}
            </button>`;
        container.appendChild(el);
    });
}

window.buyCollectionItem = function(id) {
    const item = collectionItems.find(i => i.id === id);
    if (!item || item.owned || money < item.cost) return;
    money -= item.cost;
    item.owned = true;
    ITEM_BONUSES[id]?.();
    showToast(`💎 ${item.name} отримано!`);
    saveLocalProgress();
    updateUI();
};

// ==================== BOOSTS ====================
function getUpgradeCost(key) {
    const u = upgrades[key];
    return Math.floor(u.baseCost * Math.pow(1.6, u.lvl));
}

function renderBoosts() {
    const container = document.getElementById("boost-container");
    const shop = document.getElementById("boost-shop");
    if (!container || !shop) return;

    container.innerHTML = "";
    Object.entries(upgrades).forEach(([key, u]) => {
        const cost = getUpgradeCost(key);
        const el = document.createElement("div");
        el.className = "boost-card";
        el.innerHTML = `
            <div class="boost-icon">${u.icon}</div>
            <div class="boost-info"><h3>${u.name} (Lv.${u.lvl})</h3><p>${u.desc}</p></div>
            <button class="boost-buy-btn" ${money < cost ? "disabled" : ""} onclick="buyUpgrade('${key}')">${formatNum(cost)} ₴</button>`;
        container.appendChild(el);
    });

    shop.innerHTML = "";
    const shopItems = [
        { name: "x2 Тап (5 хв)", icon: "👊", cost: 5000, action: () => activateBoost("tap2x", 300) },
        { name: "x2 Пасив (5 хв)", icon: "💰", cost: 8000, action: () => activateBoost("passive2x", 300) },
        { name: "Повна енергія", icon: "🔋", cost: 3000, action: () => { energy = maxEnergy; showToast("⚡ Енергія відновлена!"); } }
    ];
    shopItems.forEach((item, i) => {
        const el = document.createElement("div");
        el.className = "boost-card";
        el.innerHTML = `
            <div class="boost-icon">${item.icon}</div>
            <div class="boost-info"><h3>${item.name}</h3><p>Тимчасовий буст</p></div>
            <button class="boost-buy-btn" ${money < item.cost ? "disabled" : ""} onclick="buyActiveBoost(${i})">${formatNum(item.cost)} ₴</button>`;
        shop.appendChild(el);
    });
    window._shopItems = shopItems;
}

window.buyUpgrade = function(key) {
    const u = upgrades[key];
    const cost = getUpgradeCost(key);
    if (money < cost) return;
    money -= cost;
    u.lvl++;
    u.effect();
    showToast(`🚀 ${u.name} — рівень ${u.lvl}!`);
    saveLocalProgress();
    updateUI();
};

window.buyActiveBoost = function(index) {
    const item = window._shopItems[index];
    if (!item || money < item.cost) return;
    money -= item.cost;
    item.action();
    saveLocalProgress();
    updateUI();
};

function activateBoost(type, seconds) {
    activeBoosts[type] = Date.now() + seconds * 1000;
    showToast(`⚡ Буст активовано на ${seconds / 60} хв!`);
}

// ==================== MISSIONS ====================
function updateMissionProgress(track, amount) {
    missions.forEach(m => {
        if (m.track === track && !m.claimed) {
            m.progress = Math.min(m.target, m.progress + amount);
        }
    });
}

function renderMissions() {
    const container = document.getElementById("missions-container");
    if (!container) return;
    container.innerHTML = "";
    missions.forEach(m => {
        const pct = Math.min(100, m.progress / m.target * 100);
        const el = document.createElement("div");
        el.className = "mission-card";
        el.innerHTML = `
            <div class="mission-icon">${m.icon}</div>
            <div class="mission-info">
                <h3>${m.name}</h3>
                <div class="mission-progress-wrap"><div class="mission-progress" style="width:${pct}%"></div></div>
                <div class="mission-reward">🎁 ${formatNum(m.reward)} ₴ · ${Math.floor(m.progress)}/${m.target}</div>
            </div>
            <button class="mission-claim-btn ${m.claimed ? "claimed" : ""}" ${m.claimed || m.progress < m.target ? "disabled" : ""} onclick="claimMission('${m.id}')">
                ${m.claimed ? "✓" : "Забрати"}
            </button>`;
        container.appendChild(el);
    });

    const resetEl = document.getElementById("missions-reset");
    if (resetEl) {
        const now = new Date();
        const midnight = new Date(now);
        midnight.setHours(24, 0, 0, 0);
        const diff = midnight - now;
        const h = Math.floor(diff / 3600000);
        const min = Math.floor((diff % 3600000) / 60000);
        const s = Math.floor((diff % 60000) / 1000);
        resetEl.innerText = `Оновлення через: ${h}:${String(min).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
}

window.claimMission = function(id) {
    const m = missions.find(x => x.id === id);
    if (!m || m.claimed || m.progress < m.target) return;
    m.claimed = true;
    money += m.reward;
    showToast(`🎯 +${formatNum(m.reward)} ₴ за місію!`);
    saveLocalProgress();
    updateUI();
};

// ==================== ACHIEVEMENTS ====================
function checkAchievements() {
    achievements.forEach(a => {
        if (!a.unlocked && a.check()) {
            a.unlocked = true;
            showToast(`🏆 Досягнення: ${a.name}!`);
        }
    });
}

function renderAchievements() {
    const container = document.getElementById("achievements-container");
    if (!container) return;
    container.innerHTML = "";
    achievements.forEach(a => {
        const el = document.createElement("div");
        el.className = "achievement-card" + (a.unlocked ? " unlocked" : "");
        el.innerHTML = `<div class="ach-icon">${a.icon}</div><div class="ach-name">${a.name}</div><div class="ach-desc">${a.desc}</div>`;
        container.appendChild(el);
    });
}

// ==================== DAILY REWARDS ====================
window.openDailyModal = function() {
    document.getElementById("daily-modal").style.display = "flex";
    renderDailyGrid();
};

window.closeDailyModal = function() {
    document.getElementById("daily-modal").style.display = "none";
};

function renderDailyGrid() {
    const grid = document.getElementById("daily-grid");
    const btn = document.getElementById("claim-daily-btn");
    if (!grid) return;
    grid.innerHTML = "";
    const streakDay = loginStreak % 7;
    const canClaim = lastDailyClaim !== todayKey();

    DAILY_REWARDS.forEach((reward, i) => {
        const el = document.createElement("div");
        el.className = "daily-day";
        if (i < streakDay) el.classList.add("claimed");
        if (i === streakDay && canClaim) el.classList.add("today");
        el.innerHTML = `<div class="day-num">День ${i + 1}</div><div class="day-reward">💰</div><div>${formatNum(reward)} ₴</div>`;
        grid.appendChild(el);
    });

    if (btn) {
        btn.disabled = !canClaim;
        btn.innerText = canClaim ? "Забрати нагороду" : "Вже забрано сьогодні ✓";
    }
}

window.claimDailyReward = function() {
    if (lastDailyClaim === todayKey()) return;
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayKey = yesterday.toISOString().slice(0, 10);

    if (lastDailyClaim === yesterdayKey) loginStreak++;
    else if (lastDailyClaim !== todayKey()) loginStreak = 1;

    const dayIndex = (loginStreak - 1) % 7;
    const reward = DAILY_REWARDS[dayIndex];
    money += reward;
    lastDailyClaim = todayKey();
    showToast(`🎁 Щоденна нагорода: +${formatNum(reward)} ₴!`);
    closeDailyModal();
    saveLocalProgress();
    updateUI();
};

// ==================== SPIN WHEEL ====================
let wheelSpinning = false;

window.openSpinModal = function() {
    document.getElementById("spin-modal").style.display = "flex";
    const status = document.getElementById("spin-status");
    if (lastSpinDate !== todayKey()) spinsUsedToday = 0;
    if (status) status.innerText = spinsUsedToday === 0 ? "1 безкоштовне обертання на день!" : "Наступне обертання: 10,000 ₴";
};

window.closeSpinModal = function() {
    document.getElementById("spin-modal").style.display = "none";
};

window.spinWheel = function() {
    if (wheelSpinning) return;
    const isFree = spinsUsedToday === 0 && lastSpinDate !== todayKey();
    if (!isFree && money < 10000) {
        showToast("💰 Потрібно 10,000 ₴ для обертання!");
        return;
    }
    if (!isFree) money -= 10000;

    wheelSpinning = true;
    const prizeIndex = Math.floor(Math.random() * SPIN_PRIZES.length);
    const wheel = document.getElementById("wheel");
    const segmentAngle = 360 / SPIN_PRIZES.length;
    const spins = 5 + Math.random() * 3;
    const targetAngle = spins * 360 + (360 - prizeIndex * segmentAngle - segmentAngle / 2);

    wheel.style.transform = `rotate(${targetAngle}deg)`;

    setTimeout(() => {
        applySpinPrize(SPIN_PRIZES[prizeIndex]);
        spinsUsedToday++;
        lastSpinDate = todayKey();
        wheelSpinning = false;
        saveLocalProgress();
        updateUI();
    }, 4200);
};

function applySpinPrize(prize) {
    if (prize.type === "money") {
        money += prize.value;
        showToast(`🎡 Виграш: +${formatNum(prize.value)} ₴!`);
    } else if (prize.type === "energy") {
        energy = Math.min(maxEnergy, energy + prize.value);
        showToast(`🎡 +${prize.value} ⚡ енергії!`);
    } else if (prize.type === "boost") {
        activateBoost(prize.boost, prize.duration);
    }
}

// ==================== LEADERBOARD ====================
async function loadLeaderboard() {
    const container = document.getElementById("leaderboard-container");
    if (!container) return;
    try {
        const res = await fetch(`${SERVER_URL}/api/leaderboard`);
        if (!res.ok) throw new Error();
        const users = await res.json();
        if (!Array.isArray(users)) throw new Error();
        container.innerHTML = "";
        if (users.length === 0) {
            container.innerHTML = "<p class='loading-text'>Поки немає гравців</p>";
            return;
        }
        users.slice(0, 20).forEach((u, i) => {
            const row = document.createElement("div");
            row.className = "leader-row" + (u.telegramId === userTelegramId ? " me" : "");
            row.innerHTML = `
                <span class="leader-rank">${i + 1}</span>
                <span class="leader-name">${u.firstName || "Гравець"}</span>
                <span class="leader-score">${formatNum(u.money)} ₴</span>`;
            container.appendChild(row);
        });
    } catch (_) {
        container.innerHTML = "<p class='loading-text'>Рейтинг недоступний (офлайн режим)</p>";
    }
}

// ==================== TABS ====================
window.switchTab = function(tabName, element) {
    document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));
    document.getElementById(`tab-${tabName}`)?.classList.add("active");
    element?.classList.add("active");
    if (tabName === "profile") loadLeaderboard();
};

// ==================== TOAST ====================
function showToast(msg) {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.innerText = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// ==================== SERVER SYNC ====================
async function syncWithServer() {
    try {
        const res = await fetch(`${SERVER_URL}/api/user/sync`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ telegramId: userTelegramId, username: userUsername, firstName: userFirstName })
        });
        if (!res.ok) throw new Error("Server error");
        const data = await res.json();
        serverOnline = true;
        if (data.banned) {
            document.body.innerHTML = `<div style="color:#f87171;text-align:center;font-size:24px;margin-top:50px;font-weight:900;">🚫 ВАС ЗАБЛОКОВАНО</div>`;
            return;
        }
        mergeServerData(data);
        document.getElementById("username").innerText = data.firstName || userFirstName;
        saveLocalProgress();
        updateUI();
    } catch (e) {
        console.error("Offline mode:", e);
        serverOnline = false;
        if (userTelegramId === 5512316636) isAdmin = true;
        document.getElementById("username").innerText = userFirstName;
        updateUI();
    }
}

function mergeServerData(data) {
    money = Math.max(money, data.money || 0);
    tapPower = Math.max(tapPower, data.tapPower || 1);
    energy = data.energy ?? energy;
    maxEnergy = Math.max(maxEnergy, data.maxEnergy || 1000);
    passiveIncome = Math.max(passiveIncome, data.passiveIncome || 0);
    totalTaps = Math.max(totalTaps, data.totalTaps || 0);
    playerLevel = Math.max(playerLevel, data.playerLevel || 1);
    playerXP = Math.max(playerXP, data.playerXP || 0);
    maxCombo = Math.max(maxCombo, data.maxCombo || 1);
    loginStreak = Math.max(loginStreak, data.loginStreak || 0);
    isAdmin = data.isAdmin || userTelegramId === 5512316636;

    if (data.cards?.length) data.cards.forEach((sc, i) => {
        if (cards[i] && sc.lvl > cards[i].lvl) { cards[i].lvl = sc.lvl; cards[i].cost = sc.cost; }
    });
    if (data.collectionItems?.length) data.collectionItems.forEach((si, i) => {
        if (collectionItems[i]) collectionItems[i].owned = si.owned || collectionItems[i].owned;
    });
}

async function saveProgressToServer() {
    if (!serverOnline) return;
    try {
        await fetch(`${SERVER_URL}/api/user/save`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ telegramId: userTelegramId, ...getSaveData() })
        });
    } catch (e) {
        console.error("Save error:", e);
        serverOnline = false;
    }
}

// ==================== ADMIN ====================
async function fetchWithTimeout(url, options = {}, timeout = 15000) {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeout);
    try {
        const res = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(id);
        return res;
    } catch (e) {
        clearTimeout(id);
        throw e;
    }
}

window.openAdminModal = async function() {
    document.getElementById("admin-modal").style.display = "flex";
    const list = document.getElementById("admin-users-list");
    list.innerHTML = "<p class='loading-text'>Завантаження...</p>";

    const renderUsers = (users) => {
        list.innerHTML = "";
        if (users.length === 0) {
            list.innerHTML = "<p class='loading-text'>Немає гравців у базі</p>";
            return;
        }
        users.forEach(u => {
            const item = document.createElement("div");
            item.className = "admin-user-item";
            item.innerHTML = `
                <div class="admin-name">${u.firstName} (@${u.username || "немає"})</div>
                <div class="admin-meta">ID: ${u.telegramId} · ${formatNum(u.money)} ₴ · Lv.${u.playerLevel || 1}</div>
                <div class="admin-meta">Тапів: ${u.totalTaps || 0} · ${u.isBanned ? "🔴 ЗАБАНЕНИЙ" : "🟢 АКТИВНИЙ"}</div>
                <div class="admin-actions">
                    <button class="btn-add" onclick="adminAddMoney(${u.telegramId})">+100K ₴</button>
                    <button class="btn-ban" onclick="adminToggleBan(${u.telegramId})">${u.isBanned ? "Розбанити" : "Забанити"}</button>
                </div>`;
            list.appendChild(item);
        });
    };

    const showError = (msg) => {
        list.innerHTML = `<p class='loading-text' style='color:#f87171'>${msg}</p>`;
    };

    for (let attempt = 1; attempt <= 2; attempt++) {
        try {
            const res = await fetchWithTimeout(`${SERVER_URL}/api/admin/users/${userTelegramId}`, {}, 15000);
            if (!res.ok) {
                if (res.status === 403) {
                    showError("Немає прав адміністратора");
                    return;
                }
                throw new Error(`HTTP ${res.status}`);
            }
            const users = await res.json();
            if (!Array.isArray(users)) throw new Error("Невірний формат відповіді");
            renderUsers(users);
            return;
        } catch (e) {
            if (attempt < 2) {
                list.innerHTML = "<p class='loading-text'>Пробудження сервера... спроба 2/2</p>";
                await new Promise(r => setTimeout(r, 3000));
            } else {
                showError("Помилка завантаження. Сервер недоступний — спробуйте пізніше.");
            }
        }
    }
};

window.closeAdminModal = function() {
    document.getElementById("admin-modal").style.display = "none";
};

window.adminAddMoney = async function(targetId) {
    try {
        await fetch(`${SERVER_URL}/api/admin/add-money`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ adminId: userTelegramId, targetTelegramId: targetId, amount: 100000 })
        });
    } catch (_) {}
    openAdminModal();
};

window.adminToggleBan = async function(targetId) {
    try {
        await fetch(`${SERVER_URL}/api/admin/toggle-ban`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ adminId: userTelegramId, targetTelegramId: targetId })
        });
    } catch (_) {}
    openAdminModal();
};

// ==================== PARTICLE BACKGROUND ====================
(function initParticles() {
    const canvas = document.getElementById("particles-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let particles = [];

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener("resize", resize);

    for (let i = 0; i < 50; i++) {
        particles.push({
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            r: Math.random() * 2 + 0.5,
            dx: (Math.random() - 0.5) * 0.4,
            dy: (Math.random() - 0.5) * 0.4,
            color: ["#a855f7", "#fbbf24", "#22d3ee", "#f472b6"][Math.floor(Math.random() * 4)]
        });
    }

    function animate() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        particles.forEach(p => {
            p.x += p.dx;
            p.y += p.dy;
            if (p.x < 0 || p.x > canvas.width) p.dx *= -1;
            if (p.y < 0 || p.y > canvas.height) p.dy *= -1;
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
            ctx.fillStyle = p.color;
            ctx.globalAlpha = 0.35;
            ctx.fill();
        });
        ctx.globalAlpha = 1;
        requestAnimationFrame(animate);
    }
    animate();
})();

// ==================== START ====================
initAchievements();
initMissions();
// Послідовність важлива: завантажуємо та нараховуємо ОФЛАЙН ЕНЕРГІЮ
loadAndApplyOfflineProgress();
updateUI();
syncWithServer();
setInterval(saveProgressToServer, 5000);
setInterval(renderMissions, 1000);

if (lastDailyClaim !== todayKey()) {
    setTimeout(() => showToast("🎁 Не забудь забрати щоденну нагороду!"), 2000);
}
