(() => {
const $=id=>document.getElementById(id), saveKey='drago-arena-v1';
const defaults={name:'Герой Драго',level:1,xp:0,coins:0,wins:0,attack:14,defense:4,maxHp:100,hp:100,potions:3,loot:[]};
let state={...defaults,...JSON.parse(localStorage.getItem(saveKey)||'{}')}, enemy=null;
const foes=[
{name:'Гоблін-крадій',icon:'👺',hp:55,atk:8,def:1,reward:18,xp:18,loot:'🪙 Мішечок монет'},
{name:'Крижаний вовк',icon:'🐺',hp:75,atk:11,def:2,reward:28,xp:25,loot:'❄️ Крижане ікло'},
{name:'Темний лицар',icon:'🗡️',hp:105,atk:15,def:5,reward:48,xp:36,loot:'🛡️ Темний щит'},
{name:'Вогняний дракон',icon:'🐲',hp:150,atk:20,def:7,reward:80,xp:55,loot:'🔥 Луска дракона'}];
function persist(){localStorage.setItem(saveKey,JSON.stringify(state))}
function bar(id,value,max){$(id).style.width=Math.max(0,Math.min(100,value/max*100))+'%'}
function render(){
 $('hero-name').textContent=state.name;$('player-name').textContent=state.name;$('hero-level').textContent='Рівень '+state.level;
 $('coins').textContent=state.coins;$('wins').textContent=state.wins;$('stat-atk').textContent=state.attack;$('stat-def').textContent=state.defense;$('stat-hp').textContent=state.maxHp;
 $('potions').textContent=state.potions;$('xp').textContent=state.xp+' / '+(state.level*100)+' XP';bar('xp-bar',state.xp,state.level*100);
 $('player-hp-text').textContent='❤️ '+state.hp+' / '+state.maxHp;bar('player-hp',state.hp,state.maxHp);
 if(enemy){$('enemy-name').textContent=enemy.name;$('enemy-emoji').textContent=enemy.icon;$('enemy-hp-text').textContent='❤️ '+enemy.hp+' / '+enemy.maxHp;bar('enemy-hp',enemy.hp,enemy.maxHp)}
 $('loot-list').innerHTML=state.loot.length?state.loot.slice(-8).reverse().map(x=>'<div>'+x+'</div>').join(''):'<p>Поки що порожньо. Перемагай ворогів!</p>';
 persist();
}
function log(t){$('battle-log').textContent=t}
function telegram(){const u=window.Telegram?.WebApp?.initDataUnsafe?.user;if(u){state.name=u.first_name||state.name;$('avatar').src=u.photo_url||'https://api.dicebear.com/7.x/adventurer/svg?seed='+encodeURIComponent(u.id);window.Telegram.WebApp.expand()}else $('avatar').src='https://api.dicebear.com/7.x/adventurer/svg?seed=drago'}
function spawn(){const base=foes[Math.min(foes.length-1,Math.floor((state.level-1)/2)+Math.floor(Math.random()*2))];const m=1+(state.level-1)*.16;enemy={...base,maxHp:Math.round(base.hp*m),hp:Math.round(base.hp*m),atk:Math.round(base.atk*m),def:Math.round(base.def*m)};$('attack').disabled=false;$('heal').disabled=false;log(enemy.name+' виходить на арену!');render()}
function gain(xp){state.xp+=xp;while(state.xp>=state.level*100){state.xp-=state.level*100;state.level++;state.maxHp+=18;state.attack+=3;state.defense+=1;state.hp=state.maxHp;state.potions++;log('🎉 Новий рівень! Герой став сильнішим.')}}
function attack(){if(!enemy)return;const hit=Math.max(1,state.attack+Math.floor(Math.random()*7)-enemy.def);enemy.hp-=hit;if(enemy.hp<=0){state.coins+=enemy.reward;state.wins++;state.loot.push(enemy.loot);gain(enemy.xp);log('🏆 Перемога! +'+enemy.reward+' 🪙, +'+enemy.xp+' XP, здобич: '+enemy.loot);enemy=null;$('attack').disabled=true;$('heal').disabled=true;render();return}const back=Math.max(1,enemy.atk+Math.floor(Math.random()*5)-state.defense);state.hp-=back;if(state.hp<=0){state.hp=state.maxHp;enemy=null;$('attack').disabled=true;$('heal').disabled=true;log('💥 Герой відновився. Обери нового противника.')}else log('Ти завдав '+hit+' шкоди. '+enemy.name+' відповідає: -'+back+' HP.');render()}
function heal(){if(!enemy||!state.potions||state.hp===state.maxHp)return;state.potions--;state.hp=Math.min(state.maxHp,state.hp+Math.round(state.maxHp*.35));log('✨ Зілля відновило здоров’я.');render()}
function upgrade(){if(state.coins<50){log('Потрібно 50 🪙 для покращення.');return}state.coins-=50;state.attack+=2;state.defense++;state.maxHp+=10;state.hp=state.maxHp;log('⬆️ Герой покращений!');render()}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('[data-tab],.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$(b.dataset.tab).classList.add('active')});
$('new-enemy').onclick=spawn;$('attack').onclick=attack;$('heal').onclick=heal;$('upgrade').onclick=upgrade;telegram();render();
})();
