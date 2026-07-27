// Ініціалізація Telegram WebApp
if (window.Telegram && window.Telegram.WebApp) {
    Telegram.WebApp.expand();
    if (Telegram.WebApp.initDataUnsafe && Telegram.WebApp.initDataUnsafe.user) {
        document.getElementById("username").innerText = Telegram.WebApp.initDataUnsafe.user.first_name;
    }
}

// ==================== ІГРОВІ ЗМІННІ ====================
let money = 0;
let tapPower = 1;
let energy = 1000;
let maxEnergy = 1000;
let energyDrain = 5; // Скільки енергії витрачається за 1 тап
let passiveIncome = 0; // Нарахування за годину
let totalTaps = 0;

// ==================== 8 БІЗНЕСІВ (ПРОКАЧКА) ====================
const cards = [
    { id: 1, name: "Точка з Шаурмою", icon: "🥙", cost: 100, profit: 40, lvl: 0 },
    { id: 2, name: "Кав'ярня", icon: "☕", cost: 500, profit: 220, lvl: 0 },
    { id: 3, name: "Барбершоп", icon: "💈", cost: 2000, profit: 950, lvl: 0 },
    { id: 4, name: "Крипто-Ферма", icon: "⛏️", cost: 8000, profit: 4200, lvl: 0 },
    { id: 5, name: "IT-Холдинг", icon: "💻", cost: 30000, profit: 17000, lvl: 0 },
    { id: 6, name: "Казино Дракона", icon: "🎰", cost: 120000, profit: 75000, lvl: 0 },
    { id: 7, name: "Торговий Центр", icon: "🏢", cost: 500000, profit: 320000, lvl: 0 },
    { id: 8, name: "Нафтова Вишка", icon: "🛢️", cost: 2000000, profit: 1400000, lvl: 0 }
];

// ==================== 8 КОЛЕКЦІЙНИХ АРТЕФАКТІВ ====================
const collectionItems = [
    { id: "item_1", name: "Зуб Дракона", rarity: "common", icon: "🦷", cost: 2500, bonus: "+2 ₴ до тапу", owned: false },
    { id: "item_2", name: "Стародавний Амулет", rarity: "common", icon: "📿", cost: 10000, bonus: "+5 ₴ до тапу", owned: false },
    { id: "item_3", name: "Неоновий Шолом", rarity: "rare", icon: "🪖", cost: 35000, bonus: "+5% до пасиву", owned: false },
    { id: "item_4", name: "Смарагдове Яйце", rarity: "rare", icon: "🥚", cost: 100000, bonus: "+15 ₴ до тапу", owned: false },
    { id: "item_5", name: "Око Драго", rarity: "epic", icon: "👁️", cost: 300000, bonus: "+10% до пасиву", owned: false },
    { id: "item_6", name: "Вогняний Меч", rarity: "epic", icon: "⚔️", cost: 800000, bonus: "+50 ₴ до тапу", owned: false },
    { id: "item_7", name: "Корона Імператора", rarity: "legendary", icon: "👑", cost: 2500000, bonus: "+20% до пасиву", owned: false },
    { id: "item_8", name: "Серце Дракона", rarity: "legendary", icon: "💖", cost: 10000000, bonus: "+150 ₴ до тапу", owned: false }
];

// Елементи DOM
const moneyEl = document.getElementById("money");
const energyEl = document.getElementById("energy");
const energyBar = document.getElementById("energy-bar");
const passiveEl = document.getElementById("passive-income");
const tapBtn = document.getElementById("tap");
const cardsContainer = document.getElementById("cards-container");
const collectionContainer = document.getElementById("collection-container");

// Статистика у профілі
const statTotalTaps = document.getElementById("stat-total-taps");
const statTapPower = document.getElementById("stat-tap-power");
const statEnergyDrain = document.getElementById("stat-energy-drain");
const statArtifactsCount = document.getElementById("stat-artifacts-count");

// ==================== ОНОВЛЕННЯ ІНТЕРФЕЙСУ ====================
function updateUI() {
    moneyEl.innerText = Math.floor(money).toLocaleString() + " ₴";
    energyEl.innerText = energy;
    energyBar.style.width = Math.max(0, (energy / maxEnergy * 100)) + "%";
    passiveEl.innerText = Math.floor(passiveIncome).toLocaleString();

    // Статистика
    if (statTotalTaps) statTotalTaps.innerText = totalTaps.toLocaleString();
    if (statTapPower) statTapPower.innerText = tapPower + " ₴";
    if (statEnergyDrain) statEnergyDrain.innerText = energyDrain + " ⚡";
    if (statArtifactsCount) {
        const ownedCount = collectionItems.filter(i => i.owned).length;
        statArtifactsCount.innerText = `${ownedCount} / ${collectionItems.length}`;
    }

    renderCards();
    renderCollection();
}

// ==================== ТАП МЕХАНІКА ====================
tapBtn.addEventListener("pointerdown", (e) => {
    e.preventDefault();

    // Перевіряємо чи вистачає енергії
    if (energy < energyDrain) return;

    money += tapPower;
    energy -= energyDrain;
    totalTaps++;

    // Анімація вилітаючих цифр
    createFloatingNumber(e.clientX, e.clientY, `+${tapPower}`);

    // Вібрація (Telegram Haptic Feedback)
    if (window.Telegram && Telegram.WebApp && Telegram.WebApp.HapticFeedback) {
        Telegram.WebApp.HapticFeedback.impactOccurred('light');
    }

    updateUI();
});

// Анімовані цифри при тапі
function createFloatingNumber(x, y, text) {
    const el = document.createElement("div");
    el.className = "float-num";
    el.innerText = text;
    el.style.left = `${x - 20}px`;
    el.style.top = `${y - 30}px`;
    document.body.appendChild(el);

    setTimeout(() => { el.remove(); }, 800);
}

// ==================== ВІДНОВЛЕННЯ ЕНЕРГІЇ ТА ПАСИВ ====================
setInterval(() => {
    // Відновлюємо по 3⚡ за секунду
    if (energy < maxEnergy) {
        energy = Math.min(maxEnergy, energy + 3);
    }

    // Нарахування пасивного доходу
    if (passiveIncome > 0) {
        money += passiveIncome / 3600;
    }

    updateUI();
}, 1000);

// ==================== ГЕНЕРАЦІЯ КАРТОЧОК БІЗНЕСУ ====================
function renderCards() {
    if (!cardsContainer) return;
    cardsContainer.innerHTML = "";

    cards.forEach(card => {
        const cardEl = document.createElement("div");
        cardEl.className = "card";
        cardEl.innerHTML = `
            <div>
                <div class="card-icon">${card.icon}</div>
                <div class="card-title">${card.name}</div>
                <div class="card-profit">+${card.profit.toLocaleString()} ₴/год</div>
                <div class="card-level">Рівень: ${card.lvl}</div>
            </div>
            <button class="card-btn" ${money < card.cost ? "disabled" : ""} onclick="buyCard(${card.id})">
                ${card.cost.toLocaleString()} ₴
            </button>
        `;
        cardsContainer.appendChild(cardEl);
    });
}

// Купівля бізнесу
window.buyCard = function(id) {
    const card = cards.find(c => c.id === id);
    if (card && money >= card.cost) {
        money -= card.cost;
        passiveIncome += card.profit;
        card.lvl++;
        card.cost = Math.floor(card.cost * 1.55); // Зростання ціни
        updateUI();
    }
};

// ==================== ГЕНЕРАЦІЯ АРТЕФАКТІВ ====================
function renderCollection() {
    if (!collectionContainer) return;
    collectionContainer.innerHTML = "";

    collectionItems.forEach(item => {
        const itemEl = document.createElement("div");
        itemEl.className = `item-card ${item.rarity}`;

        let badgeClass = `badge-${item.rarity}`;
        let rarityText = item.rarity === 'common' ? 'Звичайний' :
                         item.rarity === 'rare' ? 'Рідкісний' :
                         item.rarity === 'epic' ? 'Епічний' : 'Легендарний';

        itemEl.innerHTML = `
            <span class="item-badge ${badgeClass}">${rarityText}</span>
            <div class="item-icon">${item.icon}</div>
            <div class="item-name">${item.name}</div>
            <div class="item-bonus">${item.bonus}</div>
            <button class="card-btn" ${item.owned || money < item.cost ? "disabled" : ""} onclick="buyCollectionItem('${item.id}')">
                ${item.owned ? "В колекції ✨" : item.cost.toLocaleString() + " ₴"}
            </button>
        `;
        collectionContainer.appendChild(itemEl);
    });
}

// Купівля колекційного артефакту
window.buyCollectionItem = function(id) {
    const item = collectionItems.find(i => i.id === id);
    if (item && !item.owned && money >= item.cost) {
        money -= item.cost;
        item.owned = true;

        // Нарахування конкретних бонусів
        if (id === "item_1") tapPower += 2;
        if (id === "item_2") tapPower += 5;
        if (id === "item_3") passiveIncome = Math.floor(passiveIncome * 1.05);
        if (id === "item_4") tapPower += 15;
        if (id === "item_5") passiveIncome = Math.floor(passiveIncome * 1.10);
        if (id === "item_6") tapPower += 50;
        if (id === "item_7") passiveIncome = Math.floor(passiveIncome * 1.20);
        if (id === "item_8") tapPower += 150;

        updateUI();
    }
};

// ==================== ПЕРЕМИКАННЯ ВКЛАДОК ====================
window.switchTab = function(tabName, element) {
    document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));

    document.getElementById(`tab-${tabName}`).classList.add('active');
    element.classList.add('active');
};

// ==================== ЗБЕРЕЖЕННЯ ТА ЗАВАНТАЖЕННЯ ====================
function saveProgress() {
    const data = {
        money,
        tapPower,
        energy,
        passiveIncome,
        totalTaps,
        cards,
        collectionItems
    };
    localStorage.setItem("drago_tap_save_v2", JSON.stringify(data));
}

function loadProgress() {
    const saved = localStorage.getItem("drago_tap_save_v2");
    if (saved) {
        try {
            const data = JSON.parse(saved);
            money = data.money || 0;
            tapPower = data.tapPower || 1;
            energy = data.energy !== undefined ? data.energy : 1000;
            passiveIncome = data.passiveIncome || 0;
            totalTaps = data.totalTaps || 0;

            if (data.cards) {
                data.cards.forEach((savedCard, i) => {
                    if (cards[i]) {
                        cards[i].lvl = savedCard.lvl;
                        cards[i].cost = savedCard.cost;
                    }
                });
            }

            if (data.collectionItems) {
                data.collectionItems.forEach((savedItem, i) => {
                    if (collectionItems[i]) {
                        collectionItems[i].owned = savedItem.owned;
                    }
                });
            }
        } catch (e) {
            console.error("Помилка завантаження збережень:", e);
        }
    }
}

// Старт гри
loadProgress();
setInterval(saveProgress, 5000); // Автозбереження кожні 5 секунд
updateUI();
