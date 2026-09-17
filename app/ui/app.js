'use strict';
/* ================= состояние ================= */
const DAYS=[['0','Пн'],['1','Вт'],['2','Ср'],['3','Чт'],['4','Пт'],['5','Сб'],['6','Вс']];
const ST={done_ok:['g','Доставлено'],operator_ok:['g','Оператор'],done_agent:['cy','ИИ-агент · квалиф.'],
  qualified_no:['cy','ИИ-агент · не квалиф.'],machine:['y','Автоответчик'],busy:['y','Занято'],
  no_answer:['y','Нет ответа'],failed:['r','Сбой'],blocked:['r','Блок антиспама'],no_operator:['r','Оператор не ответил'],
  exhausted:['r','Попытки исчерпаны'],queued:['','В очереди'],dialing:['y','Дозвон'],agent:['cy','ИИ-агент'],
  wait_operator:['b','Ждёт оператора'],talk:['b','Разговор'],blocked_no_consent:['r','Нет согласия'],
  blacklisted:['r','Чёрный список'],canceled:['','Отменено'],new:['','Новый'],operator_connected:['b','На операторе']};
const RCOL={done_ok:'#34d399',operator_ok:'#34d399',done_agent:'#53c8ef',qualified_no:'#53c8ef',machine:'#ffc857',
  busy:'#ffc857',no_answer:'#ffc857',failed:'#ff6476',blocked:'#ff6476',no_operator:'#ff6476',exhausted:'#ff6476',
  blocked_no_consent:'#ff6476',blacklisted:'#ff6476',wait_operator:'#6aa6ff',dialing:'#ffc857',agent:'#53c8ef'};
const REP_LABEL={done_ok:'Сообщение доставлено',operator_ok:'Оператор',done_agent:'ИИ-агент (кв.)',qualified_no:'Агент (не кв.)',
  machine:'Автоответчик',busy:'Занято',no_answer:'Нет ответа',failed:'Сбой',blocked:'Блок',no_operator:'Нет оператора',
  timeout:'Таймаут',exhausted:'Исчерпано',done:'Завершён',blocked_no_consent:'Нет согласия',blacklisted:'Чёрный список'};
const NAV=[['dash','Обзор','dash'],['reports','Отчёты','reports'],['campaigns','Кампании','campaigns'],
  ['contacts','Контакты','users'],['numbers','Пул номеров','phone'],['acd','Операторы / ACD','headset'],
  ['journal','Журнал звонков','list'],['blacklist','Чёрный список','shield'],['templates','Шаблоны','file'],['settings','Настройки','gear']];
const TITLE={dash:'Обзор',reports:'Отчёты',campaigns:'Кампании',contacts:'Контакты',numbers:'Пул номеров',
  acd:'Операторы и ACD',journal:'Журнал звонков',blacklist:'Чёрный список',templates:'Шаблоны',settings:'Настройки'};
let state={token:localStorage.getItem('ats_token')||'',role:'',settings:null,contacts:[],campaigns:[],numbers:[],templates:[],databases:[],
  activeTab:'dash',jN:50,jAll:false,providersim:false,curCall:null,repFrom:'',repTo:'',acdSeen:{}};
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nFmt=n=>((n===null||n===undefined)?0:n).toLocaleString('ru-RU');
const isAdmin=()=>state.role==='admin';
const dTxt=ts=>{if(!ts)return'';const m=/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(ts);return m?`${m[3]}.${m[2]} ${m[4]}:${m[5]}`:String(ts).slice(0,16)};

/* ================= иконки ================= */
const IC={
dash:'<rect x="3" y="3" width="7" height="7" rx="1.7"/><rect x="14" y="3" width="7" height="7" rx="1.7"/><rect x="3" y="14" width="7" height="7" rx="1.7"/><rect x="14" y="14" width="7" height="7" rx="1.7"/>',
reports:'<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
campaigns:'<path d="M3 11v3a1 1 0 0 0 1 1h2l6 4.5v-14L6 10H4a1 1 0 0 0-1 1z"/><path d="M15 8.5a4.5 4.5 0 0 1 0 7"/>',
users:'<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
phone:'<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
headset:'<path d="M3 13.5v-1.5a9 9 0 0 1 18 0v1.5"/><rect x="2.5" y="12" width="5.4" height="8" rx="2.2"/><rect x="16.1" y="12" width="5.4" height="8" rx="2.2"/><line x1="21" y1="19" x2="21.5" y2="21"/>',
list:'<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
shield:'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
clock:'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
search:'<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
refresh:'<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
plus:'<path d="M12 5v14M5 12h14"/>',
check:'<polyline points="20 6 9 17 4 12"/>',
x:'<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
trash:'<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
edit:'<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/>',
play:'<polygon points="7 4 20 12 7 20 7 4" fill="currentColor" stroke="none"/>',
pause:'<rect x="5" y="4" width="5" height="16" rx="1.5" fill="currentColor" stroke="none"/><rect x="14" y="4" width="5" height="16" rx="1.5" fill="currentColor" stroke="none"/>',
stop:'<rect x="5" y="5" width="14" height="14" rx="2.5" fill="currentColor" stroke="none"/>',
up:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 5 17 10"/><line x1="12" y1="15" x2="12" y2="5"/>',
down:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
mic:'<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>',
alert:'<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
info:'<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
inbox:'<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
bolt:'<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
};
const ico=(n,s)=>`<svg class="ic" style="${s||''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${IC[n]||''}</svg>`;
const AVH=s=>{let h=0;for(const c of String(s||'?'))h=(h*31+c.codePointAt(0))%360;return h;};
const av=(nm,lg)=>`<span class="av${lg?' lg':''}" style="--h:${AVH(nm)}">${esc(initials(nm))}</span>`;
const initials=n=>{const p=String(n||'?').trim().split(/\s+/).slice(0,2);return p.map(w=>w[0]).join('').toUpperCase()||'?';};

/* ================= http / утилиты ================= */
async function api(path,opts={}){
  const method=opts.method||(opts.body!==undefined?'POST':'GET');
  const headers={'Content-Type':'application/json'};
  if(state.token)headers['X-Ats-Token']=state.token;
  let r;
  try{r=await fetch('/api/v2'+path,{method,headers,body:opts.body!==undefined?JSON.stringify(opts.body):undefined});}
  catch(e){throw new Error('network');}
  if(r.status===401&&path!=='/auth/login'){logout(true);throw new Error('auth');}
  try{return await r.json();}catch(e){return {ok:false,error:'bad_response'};}
}
async function safe(fn){try{return await fn();}catch(e){return null;}}
const isActive=t=>state.activeTab===t;
function toast(txt,type){
  type=type||'ok';
  const el=document.createElement('div');
  el.className='toast '+type;
  const icn=type==='ok'?'check':(type==='err'?'x':(type==='warn'?'alert':'info'));
  el.innerHTML=ico(icn)+'<div>'+esc(txt)+'</div>';
  $('toasts').appendChild(el);
  setTimeout(()=>{el.classList.add('out');setTimeout(()=>el.remove(),260);},3600);
}
/* ================= звук и уведомления о звонках ================= */
let _actx=null;
function notifOn(){return localStorage.getItem('ats_notif')==='1';}
function setNotifUI(){
  const b=$('notifBtn');
  if(!b)return;
  b.classList.toggle('on',notifOn());
  b.title=notifOn()?'Звук и уведомления включены (щёлкните, чтобы выключить)':'Включить звук и уведомления о новых звонках';
}
function ensureAudio(){
  if(!_actx){try{_actx=new (window.AudioContext||window.webkitAudioContext)();}catch(e){_actx=null;}}
  if(_actx&&_actx.state==='suspended'){try{_actx.resume();}catch(e){}}
}
function toggleNotif(){
  const willOn=!notifOn();
  const apply=()=>{localStorage.setItem('ats_notif',willOn?'1':'0');setNotifUI();toast(willOn?'Уведомления и звук включены':'Уведомления выключены','info');};
  if(willOn&&('Notification' in window)&&Notification.permission==='default'){
    Notification.requestPermission().then(p=>{
      if(p==='granted')apply();
      else{localStorage.setItem('ats_notif','0');setNotifUI();toast('Разрешение не выдано — включите уведомления для этого сайта в браузере','warn');}
    });
  }else apply();
}
function ringSound(){
  if(!notifOn()||!_actx)return;
  try{
    if(_actx.state==='suspended')_actx.resume();
    const t0=_actx.currentTime;
    [880,1174.7].forEach((f,i)=>{
      const o=_actx.createOscillator(),g=_actx.createGain();
      o.type='sine';o.frequency.value=f;
      g.gain.setValueAtTime(0.0001,t0+i*0.3);
      g.gain.exponentialRampToValueAtTime(0.22,t0+i*0.3+0.04);
      g.gain.exponentialRampToValueAtTime(0.0001,t0+i*0.3+0.5);
      o.connect(g);g.connect(_actx.destination);o.start(t0+i*0.3);o.stop(t0+i*0.3+0.55);
    });
  }catch(e){}
}
function notifyNewAcd(p){
  if(!notifOn()||!p)return;
  if(p.id){if(state.acdSeen[p.id])return;state.acdSeen[p.id]=1;
    const ks=Object.keys(state.acdSeen);if(ks.length>250){ks.slice(0,100).forEach(k=>delete state.acdSeen[k]);}}
  ringSound();
  const nm=(p.name||'Абонент')+' · '+(p.phone||'');
  toast('Новый звонок в очереди: '+nm,'info');
  try{
    if('Notification' in window&&Notification.permission==='granted'){
      const n=new Notification('ATS v2 — новый звонок',{body:nm+' — ожидает оператора',tag:'acd'+p.id});
      n.onclick=()=>{try{window.focus();}catch(e){}openTab('acd');n.close();};
      setTimeout(()=>{try{n.close();}catch(e){}},20000);
    }
  }catch(e){}
}
document.addEventListener('pointerdown',()=>ensureAudio(),{capture:true});
document.addEventListener('keydown',()=>ensureAudio());

function ask(title,txt,okLabel,danger){
  return new Promise(res=>{
    modal(`<h2>${ico(danger?'alert':'info')}${esc(title)}</h2>
      <div class="hint" style="font-size:13px;color:#d7e3f3;line-height:1.55;margin-bottom:4px">${esc(txt)}</div>
      <div class="form-actions"><button class="btn ${danger?'d':''}" id="askOk">${esc(okLabel||'Удалить')}</button>
      <button class="btn x" id="askNo">Отмена</button></div>`);
    const done=v=>{closeModal();res(v);};
    $('askOk').onclick=()=>done(true);
    $('askNo').onclick=()=>done(false);
    const box=$('modalBox');
    const hk=e=>{if(e.key==='Escape'){document.removeEventListener('keydown',hk);done(false);}};
    document.addEventListener('keydown',hk);
    setTimeout(()=>{$('askOk').focus();},30);
  });
}
function badge(st){const x=ST[st]||['',''];return `<span class="badge ${x[0]}">${esc(x[1]||st)}</span>`;}
function empty(icn,title,txt){return `<div class="empty">${ico(icn)}<b>${esc(title)}</b>${txt?`<span>${esc(txt)}</span>`:''}</div>`;}
function pbar(a,b,cls){a=a||0;b=b||0;const p=b>0?Math.min(100,Math.round(100*a/b)):0;
  return `<div style="display:flex;align-items:center;gap:8px"><div class="pbar" style="width:76px"><i class="${cls||''}" style="width:${p}%"></i></div><span class="usage">${nFmt(a)} / ${nFmt(b)}</span></div>`;}

/* ================= навигация ================= */
function buildNav(){
  $('nav').innerHTML=NAV.map(([tab,name,icn])=>`<button class="navb" data-tab="${tab}" onclick="openTab('${tab}')">
    ${ico(icn)}<span>${name}</span><span class="grow"></span>
    ${tab==='acd'?`<span id="acdBadge" class="badge r hidden">0</span>`:''}
    ${tab==='campaigns'?'<span id="runBadge" class="badge g hidden"></span>':''}
  </button>`).join('');
}
function openTab(t){
  state.activeTab=t;
  document.querySelectorAll('.section').forEach(x=>x.classList.remove('active'));
  const sec=$(TAB2SEC(t));if(sec)sec.classList.add('active');
  document.querySelectorAll('.navb').forEach(x=>x.classList.toggle('active',x.dataset.tab===t));
  $('pageTitle').textContent=TITLE[t]||t;
  document.title=(TITLE[t]||t)+' — ATS v2';
  closeSide();
  hideBulk();
  safe(()=>refresh(t));
  if(t==='campaigns')$('campDetail').innerHTML='';
}
const TAB2SEC=t=>({dash:'dash',reports:'reports',campaigns:'campaigns',contacts:'contacts',numbers:'numbers',acd:'acd',
  journal:'journal',blacklist:'blacklist',templates:'templates',settings:'settings'}[t]);
async function refresh(tab){
  const F={dash:loadDash,reports:loadReports,campaigns:loadCamps,contacts:fetchContacts,numbers:loadNumbers,
    acd:loadAcd,journal:loadJournal,blacklist:loadBlacklist,templates:loadTemplates,settings:loadSettings};
  if(F[tab])return F[tab]();
}
function doRefresh(){safe(()=>refresh(state.activeTab));toast('Данные обновлены','info');}
const _deb={};
function debRefresh(tab,ms){
  if(!isActive(tab))return;
  clearTimeout(_deb[tab]);
  _deb[tab]=setTimeout(()=>{delete _deb[tab];safe(()=>refresh(tab));},ms);
}

/* ================= авторизация / роль ================= */
function applyRoleUI(){
  document.body.classList.toggle('is-admin',isAdmin());
  document.body.classList.toggle('is-op',!isAdmin());
  $('opQuick').classList.toggle('hidden',isAdmin());
  $('roleChipTxt').textContent=isAdmin()?'администратор':'оператор';
  $('whoRole').textContent=isAdmin()?'админ':'оператор';
  $('whoNm').textContent=state.login||(isAdmin()?'admin':'operator');
  $('myAv').style.setProperty('--h',AVH(state.login||(isAdmin()?'admin':'operator')));
  $('myAv').textContent=initials(state.login||(isAdmin()?'admin':'operator'));
}
function showApp(){
  $('gate').classList.remove('on');
  $('gate').style.display='none';
  const ab=$('appBox');ab.style.display='grid';ab.classList.add('on');
}
function showGate(){
  $('gate').classList.add('on');
  $('gate').style.display='flex';
  const ab=$('appBox');ab.style.display='none';ab.classList.remove('on');
}
async function doLogin(){
  const btn=$('gateBtn');btn.disabled=true;$('gateErr').textContent='';
  const j=await safe(()=>api('/auth/login',{body:{login:$('login').value.trim().toLowerCase(),password:$('password').value}}));
  btn.disabled=false;
  if(j&&j.ok){state.token=j.token;state.role=j.role;state.login=j.login||$('login').value.trim().toLowerCase();
    localStorage.setItem('ats_token',j.token);enterApp();}
  else if(j&&j.retry_after_sec){$('gateErr').textContent='Слишком много попыток. Подождите '+j.retry_after_sec+' с.';}
  else $('gateErr').textContent='Неверный логин или пароль';
}
function logout(silent){
  if(state.token&&!silent){safe(()=>api('/auth/logout',{body:{}}));}
  state.token='';state.role='';state.login='';localStorage.removeItem('ats_token');
  if(_es)try{_es.close();}catch(e){}
  _es=null;
  if(!silent){showGate();$('password').value='';}
}
function enterApp(){
  state.curCall=null;
  showApp();applyRoleUI();buildNav();openTab('dash');connectSSE();
}

/* ================= обзор ================= */
async function loadDash(){
  const j=await api('/dashboard');
  if(!j||j.error)return;
  const it=j.items||{},okT=j.ok_today||0,allT=j.calls_today||0;
  const runN=j.campaigns_running||0;
  $('runBadge').classList.toggle('hidden',!runN);
  if(runN)$('runBadge').textContent='● '+runN;
  $('acdBadge').textContent=j.acd_queued||0;
  $('acdBadge').classList.toggle('hidden',!(j.acd_queued>0));
  $('acdCount2').textContent=j.acd_queued||0;
  $('dashCards').innerHTML=
   `<div class="metric acc"><span class="ico">${ico('campaigns')}</span><b>${nFmt(runN)}</b><span class="l">кампаний в работе</span></div>
    <div class="metric"><span class="ico">${ico('bolt')}</span><b>${j.active_channels||0}</b><span class="l">активных каналов</span></div>
    <div class="metric"><span class="ico">${ico('phone')}</span><b>${nFmt(allT)}</b><span class="l">звонков сегодня</span></div>
    <div class="metric"><span class="ico" style="color:var(--ok);background:rgba(52,211,153,.13)">${ico('check')}</span><b class="okc">${nFmt(okT)}</b><span class="l">успешных сегодня</span></div>
    <div class="metric"><span class="ico">${ico('clock')}</span><b>${nFmt(it.queued||0)}</b><span class="l">в очереди дозвона</span></div>
    <div class="metric"><span class="ico" style="color:var(--warn);background:rgba(255,200,87,.13)">${ico('headset')}</span><b>${nFmt(it.wait_operator||0)}</b><span class="l">ждут оператора</span></div>
    <div class="metric"><span class="ico" style="color:var(--cy);background:rgba(83,200,239,.13)">${ico('users')}</span><b>${nFmt(j.acd_queued||0)}</b><span class="l">в ACD-очереди</span></div>`;
  state.providersim=!j.numbers||!j.numbers.length||j.numbers.every(n=>n.provider==='sim');
  $('simPanel').classList.toggle('hidden',!state.providersim);
  const pc=(j.numbers||[]);
  $('poolDash').innerHTML=pc.length?pc.map(n=>numCard(n)).join(''):empty('phone','Номеров пока нет','Добавьте номера в разделе «Пул номеров» — они понадобятся как Caller ID');
  wire($('poolDash'));
  const camps=await api('/campaigns');
  if(camps&&camps.campaigns!==undefined)state.campaigns=camps.campaigns;
  $('dcCamps').textContent='· '+(state.campaigns||[]).length;
  const rows=(state.campaigns||[]).slice(0,7);
  $('dashCamps').innerHTML=rows.length?'<tr><th>Кампания</th><th>Очередь</th><th>Успешно / звонков</th><th>Статус</th></tr>'+
   rows.map(c=>`<tr><td><a class="tlink" data-a="showcamp" data-id="${c.id}" href="#">${esc(c.name)}</a>
     <div class="dim" style="font-size:11px">${esc(c.flow==='message'?'озвучка':c.flow==='operator'?'на оператора':'ИИ-агент')}</div></td>
     <td>${pbar(c.items_done||0,c.items_total||0)}</td><td>${nFmt(c.calls_ok||0)} / ${nFmt(c.calls||0)}</td>
     <td>${statusPill(c.status)}</td></tr>`).join(''):'';
  wire($('dashCamps'));
  const feed=await api('/calls',{body:{limit:9}});
  const fr=(feed&&feed.calls)||[];
  $('actFeed').innerHTML=fr.length?fr.map(x=>`<div class="frow">${av(x.contact_name)}<div style="min-width:0"><div class="calln">${esc(x.contact_name||x.contact_phone)}</div>
    <div class="dim" style="font-size:11px">${esc(x.caller_id||'')}${x.campaign_id?' · кампания #'+x.campaign_id:''}</div></div>
    <span class="res">${badge(x.result||x.status)}</span><span class="tm">${dTxt(x.started_at)}</span></div>`).join('')
    :empty('inbox','Звонков ещё не было','Запустите кампанию — в режиме sim исход звонка задаётся на панели теста');
}
function statusPill(st){return st==='running'?'<span class="badge g"><span class="dot"></span>В работе</span>'
  :st==='paused'?'<span class="badge y">Пауза</span>':'<span class="badge">Остановлена</span>';}
function numCard(n){
  const p=n.daily_limit?Math.min(100,Math.round(100*n.daily_count/n.daily_limit)):0;
  const barCls=p>=100?'bad':(p>=75?'warn':'ok');
  return `<div class="numcard"><div class="top">
    <span class="av" style="--h:${AVH(n.number)}">${ico('phone')}</span>
    <div style="min-width:0"><div class="num">${esc(n.number)}</div><div class="lbl">${esc(n.label||'без метки')}</div></div>
    <span class="acts">${isAdmin()?`<button class="iconbtn sm" style="width:30px;height:30px" data-a="editn" data-id="${n.id}" title="Изменить">${ico('edit')}</button>`:''}</span></div>
    <div class="meta"><span class="badge ${n.active?'g':'b'}">${n.active?'активен':'выключен'}</span>
      <span class="badge b">${esc(n.provider)}</span>${n.quarantined?'<span class="badge r">карантин</span>':''}${n.cooling?'<span class="badge y">остывает</span>':''}${n.enabled_outgoing===false?'<span class="badge r" title="ВАТС запретила исходящие с номера (caller-ids)">исходящие запрещены</span>':''}</div>
    <div>${pbar(n.daily_count,n.daily_limit,barCls)}</div>
    ${isAdmin()?`<div class="toolbar" style="margin:2px 0 0"><button class="btn ghost sm" data-a="quar" data-id="${n.id}" data-on="${n.quarantined?0:1}">${n.quarantined?'Снять карантин':'Карантин'}</button>
      <button class="btn ghost sm" data-a="togglen" data-id="${n.id}" data-on="${n.active?0:1}">${n.active?'Выключить':'Включить'}</button></div>`:''}
  </div>`;
}
async function saveSim(){
  const j=await safe(()=>api('/sim/script',{body:{phone:$('simPhone').value.trim(),outcome:$('simOutcome').value,answer:$('simAnswer').value.trim()}}));
  if(j&&j.ok)toast('Сценарий симуляции задан для '+$('simPhone').value.trim());
  else toast('Не удалось задать сценарий','err');
}

/* ================= контакты ================= */
async function fetchContacts(){
  const j=await api('/contacts');
  if(!j||j.error)return;
  state.contacts=j.contacts;
  await safe(fetchDatabases);
  fillContactFilters();
  $('cCnt2').textContent='· '+nFmt(j.contacts.length);
  renderContacts();
}
async function fetchDatabases(){
  const j=await api('/databases');
  if(j&&!j.error)state.databases=j.databases||[];
}
function fillContactFilters(){
  const groups=[...new Set((state.contacts||[]).map(c=>c.grp).filter(Boolean))].sort();
  const cur=$('contactGroup').value;
  $('contactGroup').innerHTML='<option value="">Все группы</option>'+groups.map(g=>`<option value="${esc(g)}" ${g===cur?'selected':''}>${esc(g)}</option>`).join('');
  const dbs=state.databases||[];
  const curD=$('contactDb').value;
  $('contactDb').innerHTML='<option value="">Все базы</option>'
    +dbs.map(d=>`<option value="${d.id}" ${String(d.id)===curD?'selected':''}>${esc(d.name)} (${nFmt(d.contacts_count||0)})</option>`).join('')
    +`<option value="0" ${curD==='0'?'selected':''}>— без базы —</option>`;
}
function filteredContacts(){
  const q=($('contactSearch').value||'').toLowerCase().trim();
  const grp=$('contactGroup').value;
  const dbf=$('contactDb').value;
  return (state.contacts||[]).filter(c=>{
    if(grp&&c.grp!==grp)return false;
    if(dbf!==''&&String(c.database_id||0)!==dbf)return false;
    if(q&&(c.name+' '+c.phone+' '+(c.note||'')+' '+(c.tags||'')).toLowerCase().indexOf(q)<0)return false;
    return true;
  });
}
function tagChips(tags){
  return String(tags||'').split(',').map(t=>t.trim()).filter(Boolean)
    .map(t=>`<span class="tag">${esc(t)}</span>`).join('');
}
function renderContacts(){
  const rows=filteredContacts();
  $('contactsTable').innerHTML='<tr><th style="width:34px"><input type="checkbox" class="ck" id="ckAll" title="Выбрать всё"></th><th>Контакт</th><th>Телефон</th><th>База</th><th>Теги</th><th>Согласие</th><th>Статус</th><th class="num">Действия</th></tr>'+
   rows.map(c=>`<tr data-cid="${c.id}"><td><input type="checkbox" class="ck" data-id="${c.id}"></td>
    <td style="min-width:0"><span class="cell-av">${av(c.name)}<span style="min-width:0"><button class="linklike" data-a="cardc" data-id="${c.id}" title="Открыть карточку номера">${esc(c.name||'(без имени)')}</button>${c.note?`<span class="ph">${esc(String(c.note).slice(0,40))}</span>`:''}</span></span></td>
    <td class="nowrap"><b>${esc(c.phone)}</b></td>
    <td>${esc(c.db_name||'')}</td>
    <td style="max-width:180px">${tagChips(c.tags)}</td>
    <td>${c.consent?'<span class="badge g">есть</span>':'<span class="badge">нет</span>'}</td>
    <td>${c.blacklisted?'<span class="badge r">ЧС</span>':''}${c.complaints?`<span class="badge y">жалоб: ${c.complaints}</span>`:''}</td>
    <td class="num"><span class="btn-row">
      <button class="btn ghost sm" data-a="editc" data-id="${c.id}" title="Редактировать">${ico('edit')}Ред.</button>
      ${isAdmin()?`<button class="btn ghost sm" data-a="compl" data-phone="${esc(c.phone)}" title="Зафиксировать жалобу">Жалоба</button>
      <button class="btn d sm" data-a="delc" data-id="${c.id}" title="Удалить">${ico('trash')}</button>`:''}
    </span></td></tr>`).join('')
   ||`<tr><td colspan="8">${empty('users',rows.length?'Ничего не найдено':'Контактов пока нет','Добавьте вручную или загрузите базу Excel/CSV (для администратора)')}</td></tr>`;
  wire($('contactsTable'));
  syncBulk();
}
function toggleImport(){$('importBox').classList.toggle('hidden');}
async function importCsv(){
  const rows=[];
  $('csvText').value.split(/\r?\n/).forEach(l=>{
    const p=l.split(';');
    if(p[1])rows.push({name:p[0]||'',phone:p[1].trim()||'',group:p[2]||'',note:p[3]||'',
      consent:['1','да','yes','true','+'].includes(String(p[4]||'').toLowerCase().trim())});
  });
  if(!rows.length)return toast('Нет строк для импорта','warn');
  const j=await safe(()=>api('/contacts/import',{body:{rows}}));
  const errBox=$('importErr');
  if(j&&j.ok){
    let txt='✓ Добавлено: '+j.added+', обновлено: '+(j.updated||0);
    if(j.skipped)txt+=', пропущено: '+j.skipped;
    $('importMsg').textContent=txt;$('importMsg').classList.add('ok');$('importMsg').classList.remove('err');
    const errs=j.errors||[];
    if(errBox)errBox.innerHTML=errs.length
      ?`<div class="errbox"><b>Не импортировано (${errs.length}):</b>${errs.map(e=>`<div>строка ${(e.row||0)+1} — ${esc(e.reason)}${e.phone?` (${esc(e.phone)})`:''}</div>`).join('')}${(j.skipped||0)>errs.length?'<div class="dim">… и другие</div>':''}</div>`
      :'';
    else if(errs.length)toast('Пропущено строк: '+errs.length+' — см. список ошибок ниже','warn');
    await fetchContacts();
  }else{
    $('importMsg').textContent='Ошибка импорта';$('importMsg').classList.remove('ok');$('importMsg').classList.add('err');
    if(errBox)errBox.innerHTML='';
  }
}
function dbOptions(sel){
  const dbs=state.databases||[];
  return '<option value="0">— без базы —</option>'+dbs.map(d=>`<option value="${d.id}" ${String(d.id)===String(sel||0)?'selected':''}>${esc(d.name)}</option>`).join('');
}
function editContact(id){
  const c=state.contacts.find(x=>x.id===id)||{id:0,name:'',phone:'',grp:'',note:'',tags:'',database_id:0,consent:true};
  modal(`<h2>${ico('users')}${id?'Контакт #'+id:'Новый контакт'}</h2>
    <div class="formgrid">
      <div class="fld"><span class="lbl">Имя</span><input id="cName" class="input ctl" value="${esc(c.name)}" placeholder="Иван Петров"></div>
      <div class="fld"><span class="lbl">Телефон</span><input id="cPhone" class="input ctl" value="${esc(c.phone)}" placeholder="+79000000000"></div>
      <div class="fld"><span class="lbl">Группа</span><input id="cGrp" class="input ctl" value="${esc(c.grp)}" placeholder="клиенты, база 2026…"></div>
      <div class="fld"><span class="lbl">База данных</span><select id="cDb" class="select ctl">${dbOptions(c.database_id)}</select></div>
      <div class="fld span2"><span class="lbl">Теги (через запятую)</span><input id="cTags" class="input ctl" value="${esc(c.tags||'')}" placeholder="vip, москва, повторный"></div>
      <div class="fld" style="justify-content:flex-end"><label class="sw" style="padding-bottom:10px"><input id="cConsent" type="checkbox" ${c.consent?'checked':''} ${isAdmin()?'':'disabled'}><span class="trk"></span>есть согласие на обзвон${isAdmin()?'':' <span class="hint">(только админ)</span>'}</label></div>
      <div class="fld span2"><span class="lbl">Примечание</span><textarea id="cNote" class="textarea ctl">${esc(c.note)}</textarea></div>
    </div>
    <div class="form-actions"><button class="btn" onclick="saveContact(${id})">${ico('check')}Сохранить</button><button class="btn x" onclick="closeModal()">Отмена</button></div>`);
}
async function saveContact(id){
  const j=await safe(()=>api('/contacts/save',{body:{id:id,name:$('cName').value,phone:$('cPhone').value,group:$('cGrp').value,note:$('cNote').value,consent:$('cConsent').checked,tags:$('cTags').value,database_id:parseInt($('cDb').value||'0')}}));
  if(j&&j.ok){closeModal();toast('Контакт сохранён');await fetchContacts();}
  else toast((j&&j.error)||'Ошибка сохранения','err');
}
/* ================= базы данных: загрузка Excel/CSV ================= */
function fileToB64(file){
  return new Promise((res,rej)=>{
    const r=new FileReader();
    r.onload=()=>{try{
      const u=new Uint8Array(r.result);let s='';
      for(let i=0;i<u.length;i+=0x8000){s+=String.fromCharCode.apply(null,u.subarray(i,i+0x8000));}
      res(btoa(s));
    }catch(e){rej(e);}};
    r.onerror=rej;r.readAsArrayBuffer(file);
  });
}
function uploadDbModal(){
  const dbs=state.databases||[];
  modal(`<h2>${ico('file')}Загрузить базу контактов</h2>
    <p class="hint" style="margin:0 0 10px">Файл <b>.xlsx</b> или <b>.csv</b> (до ~48 МБ). Колонки распознаются автоматически:
    «Телефон / Phone», «Имя / Name», «Группа», «Теги», «Примечание», «Согласие». Номера приводятся к виду 7XXXXXXXXXX.
    Подойдёт и простой список номеров без заголовков.</p>
    <div class="formgrid">
      <div class="fld span2"><span class="lbl">Файл базы</span><input id="dbFile" type="file" class="input ctl" accept=".xlsx,.csv,.txt"></div>
      <div class="fld"><span class="lbl">В существующую базу</span><select id="dbExist" class="select ctl"><option value="0">— новая база —</option>${dbs.map(d=>`<option value="${d.id}">${esc(d.name)} (${nFmt(d.contacts_count||0)})</option>`).join('')}</select></div>
      <div class="fld"><span class="lbl">Название новой базы</span><input id="dbName" class="input ctl" placeholder="Например: МСК — сентябрь (из файла)"></div>
      <div class="fld span2" style="justify-content:flex-end"><label class="sw"><input id="dbConsent" type="checkbox"><span class="trk"></span>считать, что согласие на обзвон есть у всех (если в файле нет колонки «Согласие»)</label></div>
    </div>
    <div class="form-actions"><button class="btn" id="dbUpBtn" onclick="uploadDbFile()">${ico('check')}Загрузить</button><button class="btn x" onclick="closeModal()">Отмена</button><span class="pillmsg" id="dbUpMsg"></span></div>
    <div id="dbUpRes"></div>`);
  $('dbFile').addEventListener('change',()=>{
    const f=$('dbFile').files[0];
    if(f&&!$('dbName').value)$('dbName').value=f.name.replace(/\.[^.]+$/,'');
  });
}
async function uploadDbFile(){
  const inp=$('dbFile');
  const f=inp&&inp.files[0];
  if(!f)return toast('Выберите файл .xlsx или .csv','warn');
  const btn=$('dbUpBtn'),msg=$('dbUpMsg'),box=$('dbUpRes');
  btn.disabled=true;msg.textContent='Чтение и загрузка…';msg.className='pillmsg';box.innerHTML='';
  let b64='';
  try{b64=await fileToB64(f);}catch(e){btn.disabled=false;msg.textContent='Не удалось прочитать файл';msg.classList.add('err');return;}
  const j=await safe(()=>api('/contacts/import-file',{body:{filename:f.name,content_b64:b64,
    database_id:parseInt($('dbExist').value||'0'),database_name:$('dbName').value,consent_default:$('dbConsent').checked}}));
  btn.disabled=false;
  if(!j||!j.ok){
    msg.textContent='Ошибка: '+((j&&(j.detail||j.error))||'сеть');msg.classList.add('err');return;
  }
  msg.textContent=`✓ Строк: ${j.total_rows} · добавлено: ${j.added} · обновлено: ${j.updated} · пропущено: ${j.skipped}`;
  msg.classList.add('ok');
  const mp=Object.entries(j.mapping||{}).map(([k,v])=>`<span class="badge b">${esc(k)} ← ${esc(v)}</span>`).join(' ');
  const errs=j.errors||[];
  box.innerHTML=`<div class="hint" style="margin:8px 0">База: <b>${esc((j.database||{}).name||'')}</b> · распознано колонок: ${mp||'—'}</div>`
    +(errs.length?`<div class="errbox"><b>Не импортировано (${j.errors_total||errs.length}):</b>${errs.map(e=>`<div>строка ${e.row} — ${esc(e.reason)}${e.phone?` (${esc(e.phone)})`:''}</div>`).join('')}${(j.errors_total||0)>errs.length?'<div class="dim">… показаны первые 100</div>':''}</div>`
    :'<div class="hint">Ошибок нет — все строки загружены.</div>');
  await fetchContacts();
}
async function manageDbModal(){
  await safe(fetchDatabases);
  const dbs=state.databases||[];
  modal(`<h2>${ico('file')}Базы данных</h2>
    <p class="hint" style="margin:0 0 10px">Каждая загрузка Excel/CSV создаёт базу. Удаление базы открепляет контакты (они остаются в общем списке) — либо удаляет их вместе с базой.</p>
    <div class="tblwrap"><table style="width:100%">
    <tr><th>Название</th><th>Файл</th><th class="num">Контактов</th><th class="num">С согласием</th><th>Теги</th><th class="num">Действия</th></tr>
    ${dbs.map(d=>`<tr><td><b>${esc(d.name)}</b><div class="dim" style="font-size:11px">${esc(d.created||'')}</div></td>
      <td>${esc(d.filename||'—')}</td><td class="num">${nFmt(d.contacts_count||0)}</td><td class="num">${nFmt(d.consent_count||0)}</td>
      <td style="max-width:200px">${(d.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</td>
      <td class="num"><span class="btn-row">
        <button class="btn ghost sm" onclick="delDb(${d.id},false)" title="Удалить базу, контакты останутся">Открепить</button>
        <button class="btn d sm" onclick="delDb(${d.id},true)" title="Удалить базу вместе с контактами">${ico('trash')}</button>
      </span></td></tr>`).join('')||'<tr><td colspan="6"><div class="dim" style="padding:8px">Баз пока нет — загрузите Excel/CSV кнопкой «Загрузить базу».</div></td></tr>'}
    </table></div>
    <div class="form-actions"><button class="btn x" onclick="closeModal()">Закрыть</button></div>`,true);
}
async function delDb(id,withContacts){
  const d=(state.databases||[]).find(x=>x.id===id);
  const nm=d?d.name:'#'+id;
  if(!(await ask('Удалить базу',withContacts?`База «${nm}» и все её контакты будут удалены. Звонки и история сохранятся.`:`База «${nm}» будет удалена, контакты останутся в общем списке без привязки к базе.`,'Удалить')))return;
  const j=await safe(()=>api('/databases/delete',{body:{ids:[id],delete_contacts:withContacts}}));
  if(j&&j.ok){toast('База удалена');await manageDbModal();await fetchContacts();}
  else toast('Ошибка удаления','err');
}
/* ================= карточка номера: динамика ================= */
function callPill(st){
  st=String(st||'');
  if(/done_ok|operator_ok|qualified_yes|done_agent/.test(st))return '<span class="badge g">'+esc(st)+'</span>';
  if(/busy|no_answer|machine|wait|ring|dial|talk|agent/.test(st))return '<span class="badge y">'+esc(st)+'</span>';
  if(!st)return '<span class="badge">—</span>';
  return '<span class="badge r">'+esc(st)+'</span>';
}
function recLink(x){
  // Запись ВАТС (внешняя ссылка из history) приоритетнее локального файла.
  if(x.recording_url)return `<a class="iconbtn sm" style="width:29px;height:29px" href="${esc(x.recording_url)}" target="_blank" title="Слушать запись ВАТС">${ico('mic')}</a>`;
  if(x.recording)return `<a class="iconbtn sm" style="width:29px;height:29px" href="/api/v2/calls/${x.id}/recording?token=${encodeURIComponent(state.token)}" target="_blank" title="Слушать запись">${ico('mic')}</a>`;
  return '';
}
function extStatus(x){
  return x.external_status?` <span class="badge b" title="Статус history ВАТС (ground truth)">ВАТС: ${esc(x.external_status)}</span>`:'';
}
async function contactCard(id){
  const j=await safe(()=>api('/contacts/history?id='+id));
  if(!j||!j.contact)return toast('Карточка недоступна','err');
  const c=j.contact,st=j.stats||{},calls=j.calls||[],items=j.items||[];
  const ev=calls.map(x=>({t:x.started_at||'',h:`<b>Звонок #${x.id}</b> ${x.camp_name?`· ${esc(x.camp_name)}`:''} · с ${esc(x.caller_id||'—')} <span class="btn-row">${recLink(x)}</span>`,
    b:`${callPill(x.result||x.status)}${extStatus(x)} <span class="dim">${esc(x.started_at||'')} → ${esc(x.ended_at||'…')}${x.duration_sec?` · ${x.duration_sec} с`:''}${x.detail?` · ${esc(x.detail)}`:''}</span>`,
    cls:/done_ok|operator_ok|qualified_yes/.test(x.result||'')?'ok':(/busy|no_answer|machine/.test(x.result||'')?'warn':(/failed|blocked|exhausted/.test(x.result||'')?'err':''))}));
  const it=items.map(x=>({t:x.updated||'',h:`<b>В кампании</b> ${x.camp_name?`«${esc(x.camp_name)}»`:''}`,
    b:`${callPill(x.last_result?x.status+': '+x.last_result:x.status)} <span class="dim">попыток: ${x.attempts||0}${x.next_attempt_at?` · след.: ${esc(x.next_attempt_at)}`:''}</span>`,cls:''}));
  const all=ev.concat(it).sort((a,b)=>String(b.t).localeCompare(String(a.t)));
  modal(`<h2>${ico('phone')}Карточка номера</h2>
    <dl class="kv" style="margin:0 0 8px">
      <dt>Контакт</dt><dd><b>${esc(c.name||'(без имени)')}</b></dd>
      <dt>Телефон</dt><dd><b>${esc(c.phone)}</b></dd>
      <dt>База</dt><dd>${esc(c.db_name||'— без базы —')}</dd>
      <dt>Группа</dt><dd>${esc(c.grp||'—')}</dd>
      <dt>Теги</dt><dd>${tagChips(c.tags)||'—'}</dd>
      <dt>Согласие</dt><dd>${c.consent?'<span class="badge g">есть</span>':'<span class="badge">нет</span>'}${c.blacklisted?' <span class="badge r">ЧС</span>':''}${c.complaints?` <span class="badge y">жалоб: ${c.complaints}</span>`:''}</dd>
      <dt>Звонков</dt><dd>всего ${st.calls_total||0} · завершено ${st.calls_done||0}${st.last_result?` · последний: ${callPill(st.last_result)} <span class="dim">${esc(st.last_at||'')}</span>`:''}</dd>
      ${c.note?`<dt>Примечание</dt><dd>${esc(c.note)}</dd>`:''}
    </dl>
    <h3 style="margin:10px 0 4px;font-size:14px">Динамика работы с номером</h3>
    <div class="tl">${all.map(e=>`<div class="tl-item ${e.cls}"><div>${e.h}</div><div>${e.b}</div></div>`).join('')||'<div class="dim">Звонков пока не было — номер ещё не обзванивали.</div>'}</div>
    <div class="form-actions"><button class="btn ghost" onclick="editContact(${c.id})">${ico('edit')}Редактировать</button><button class="btn x" onclick="closeModal()">Закрыть</button></div>`,true);
}
async function delContact(id){
  if(!(await ask('Удалить контакт','Контакт будет удалён из базы без возможности восстановления.','Удалить')))return;
  const j=await safe(()=>api('/contacts/delete',{body:{ids:[id]}}));
  if(j&&j.ok){toast('Контакт удалён');await fetchContacts();}
}
async function complaint(phone){
  if(!(await ask('Зафиксировать жалобу','Абонент пожалуйста на звонки с номера? Контакт попадёт в чёрный список, номер — под наблюдение.','Жалоба',false)))return;
  const j=await safe(()=>api('/complaint',{body:{phone,source:'operator'}}));
  if(j&&j.ok){toast('Жалоба зафиксирована: контакт в ЧС, номер под наблюдением');await fetchContacts();}
  else toast('Ошибка фиксации жалобы','err');
}
function selIds(){return [...document.querySelectorAll('#contactsTable .ck:checked')].filter(x=>x.id!=='ckAll').map(x=>parseInt(x.dataset.id));}
function syncBulk(){
  if(!isAdmin()){hideBulk();return;}
  const n=selIds().length;
  $('bulkBar').classList.toggle('hidden',!n||state.activeTab!=='contacts');
  if(n)$('bulkCnt').textContent='Выбрано: '+n;
  const all=$('contactsTable').querySelectorAll('tbody .ck').length||$('contactsTable').querySelectorAll('tr[data-cid] .ck').length;
  const ckAll=$('ckAll');
  if(ckAll)ckAll.checked=!!all&&n===all;
}
function hideBulk(){const b=$('bulkBar');if(b&&!b.classList.contains('hidden')){b.classList.add('hidden');}}
async function bulkToCamp(){
  const ids=selIds();
  if(!ids.length)return toast('Сначала выберите контакты','warn');
  if(!state.campaigns.length)await safe(loadCamps);
  const camps=state.campaigns||[];
  if(!camps.length)return toast('Сначала создайте кампанию','warn');
  modal(`<h2>${ico('campaigns')}Добавить ${ids.length} конт. в кампанию</h2>
    <div class="picklist">${camps.map(c=>`<label><input type="radio" name="pkc" value="${c.id}" ${c.id===camps[0].id?'checked':''}>
      <span><b>${esc(c.name)}</b></span><span class="hint"> #${c.id} · ${statusPill(c.status)}</span></label>`).join('')}</div>
    <div class="form-actions"><button class="btn" id="pkcOk">Добавить</button><button class="btn x" onclick="closeModal()">Отмена</button></div>`);
  $('pkcOk').onclick=async()=>{
    const cid=parseInt(document.querySelector('input[name=pkc]:checked').value);
    const j=await safe(()=>api(`/campaigns/${cid}/add-contacts`,{body:{contact_ids:ids}}));
    closeModal();
    if(j&&j.ok){toast('Добавлено в кампанию: '+j.added);await fetchContacts();}
    else toast('Ошибка: '+((j&&j.error)||'?'),'err');
  };
}
async function bulkDelete(){
  const ids=selIds();
  if(!ids.length)return;
  if(!(await ask('Удалить выбранных',`Будет удалено контактов: ${ids.length}. Действие необратимо.`,'Удалить')))return;
  const j=await safe(()=>api('/contacts/delete',{body:{ids}}));
  if(j&&j.ok){toast('Удалено: '+ids.length);await fetchContacts();}
}
async function exportCsv(){
  const r=await fetch('/api/v2/export/contacts.csv',{headers:{'X-Ats-Token':state.token}});
  if(!r.ok)return toast('Экспорт доступен только администратору','err');
  const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='contacts.csv';a.click();
}

/* ================= кампании ================= */
async function loadCamps(){
  const j=await api('/campaigns');
  if(!j||j.error)return;
  state.campaigns=j.campaigns;
  $('cCnt').textContent='· '+nFmt(j.campaigns.length);
  $('campsTable').innerHTML=`<tr><th style="width:52px">ID</th><th>Кампания</th><th>Сценарий</th><th>Прогресс</th><th>Статус</th><th class="num">Успешно / звонков</th>${isAdmin()?'<th class="num">Действия</th>':''}</tr>`+
   j.campaigns.map(c=>`<tr data-cid="${c.id}">
    <td class="dim">#${c.id}</td>
    <td style="min-width:0"><a class="tlink" data-a="showcamp" data-id="${c.id}" href="#">${esc(c.name)}</a>
      ${c.retry_map&&Object.keys(c.retry_map).length?`<div class="dim" style="font-size:11px">умные повторы</div>`:''}</td>
    <td><span class="badge cy">${c.flow==='message'?'озвучка':c.flow==='operator'?'на оператора':'ИИ-агент'}</span>
      ${c.template_name?`<div class="dim" style="font-size:11px;margin-top:3px">${esc(c.template_name)}</div>`:''}</td>
    <td>${pbar(c.items_done||0,c.items_total||0)}</td>
    <td>${statusPill(c.status)}</td>
    <td class="num">${nFmt(c.calls_ok||0)} / ${nFmt(c.calls||0)}</td>
    ${isAdmin()?`<td class="num"><span class="btn-row">
      <button class="iconbtn sm" style="width:29px;height:29px;color:#7fe7c0" data-a="cstart" data-id="${c.id}" title="Запустить">${ico('play')}</button>
      <button class="iconbtn sm" style="width:29px;height:29px" data-a="cpause" data-id="${c.id}" title="Пауза">${ico('pause')}</button>
      <button class="iconbtn sm" style="width:29px;height:29px;color:#ffa7b4" data-a="cstop" data-id="${c.id}" title="Остановить">${ico('stop')}</button>
    </span></td>`:''}</tr>`).join('')
   ||`<tr><td colspan="7">${empty('campaigns','Кампаний пока нет','Создайте первую кампанию и добавьте контакты')}</td></tr>`;
  wire($('campsTable'));
}
async function campAction(id,act){
  const j=await safe(()=>api(`/campaigns/${id}/${act}`,{body:{}}));
  if(j&&j.ok){toast(act==='retry-exhausted'?'Исчерпанные возвращены в очередь: '+(j.requeued||0):({start:'Кампания запущена',pause:'Кампания на паузе',stop:'Кампания остановлена'}[act]));
    await loadCamps();if(state.activeTab==='campaigns')showCamp(id);safe(loadDash);}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
function editCampaign(c){
  if(!isAdmin())return;
  c=c||{};
  safe(()=>loadTemplates(true)).then(()=>{
    const tpls=state.templates||[];
    const rmText=c.retry_map?JSON.stringify(c.retry_map):'';
    modal(`<h2>${ico('campaigns')}${c.id?'Кампания #'+c.id:'Новая кампания'}</h2>
      <div class="formgrid">
        <div class="fld span2"><span class="lbl">Название</span><input id="kName" class="input ctl" value="${esc(c.name||'')}" placeholder="Осень-2026, сбор заявок…"></div>
        <div class="fld"><span class="lbl">Сценарий</span><select id="kFlow" class="select ctl">
          <option value="message" ${c.flow==='message'?'selected':''}>Сообщение (озвучка)</option>
          <option value="agent" ${!c.flow||c.flow==='agent'?'selected':''}>ИИ-агент (квалификация)</option>
          <option value="operator" ${c.flow==='operator'?'selected':''}>Сразу на оператора</option></select></div>
        <div class="fld"><span classect id="kTemplate" class="select ctl"><option value="0">— без шаблона —</option>${tpls.map(t=>`<option value="${t.id}" ${t.id==c.template_id?'selected':''}>${esc(t.name)}</option>`).join('')}</select></div>
        <div class="fld"><span class="lbl">Окно звонков: с</span><input id="kWs" type="time" class="input ctl" value="${esc((c.schedule&&c.schedule.start)||'08:00')}"></div>
        <div class="fld"><span class="lbl">Окно звонков: до</span><input id="kWe" type="time" class="input ctl" value="${esc((c.schedule&&c.schedule.end)||'20:00')}"></div>
        <div class="fld"><span class="lbl">Попыток на контакт</span><input id="kRetry" type="number" min="1" class="input ctl" value="${c.retry_max>=0?c.retry_max:''}" placeholder="по умолчанию"></div>
        <div class="fld"><span class="lbl">Максимум параллельно</span><input id="kCh" type="number" min="1" class="input ctl" value="${c.max_channels||''}" placeholder="по умолчанию"></div>
        <div class="fld span2"><span class="lbl">Интервалы повторов по причинам, мин (JSON, необязательно)</span>
          <input id="kRetryMap" class="input ctl" style="font-family:ui-monospace,Consolas,monospace;font-size:12.5px" value="${esc(rmText)}" placeholder='{"busy": 5, "no_answer": 20, "machine": 30}'>
          <span class="hint">перекрывает базовый интервал: например занято → 5 мин, нет ответа → 20 мин</span></div>
        <div class="span2"><label class="sw"><input id="kConnect" type="checkbox" ${c.connect_on_qualify===0?'':'checked'}><span class="trk"></span>квалифицированных ИИ-агентом — переводить на оператора</label></div>
      </div>
      <div class="form-actions"><button class="btn" onclick="saveCampaign(${c.id||0})">${ico('check')}Сохранить</button><button class="btn x" onclick="closeModal()">Отмена</button></div>`);
  });
}
async function saveCampaign(id){
  let rm={};
  const raw=$('kRetryMap').value;
  if(raw.trim()){try{rm=JSON.parse(raw);}catch(e){return toast('Интервалы повторов: невалидный JSON','err');}}
  const body={id:id,name:$('kName').value,flow:$('kFlow').value,template_id:parseInt($('kTemplate').value||0),
    schedule:{start:$('kWs').value||'08:00',end:$('kWe').value||'20:00',days:[0,1,2,3,4,5,6]},
    retry_max:parseInt($('kRetry').value||-1),max_channels:parseInt($('kCh').value||0),
    retry_map:rm,connect_on_qualify:$('kConnect').checked};
  const j=await safe(()=>api('/campaigns/save',{body}));
  if(j&&j.ok){closeModal();toast('Кампания сохранена');await loadCamps();showCamp(j.id||id);}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
let campView='items';
async function showCamp(id){
  const j=await api(`/campaigns/${id}`);
  if(!j||!j.campaign)return;
  const c=j.campaign||{},items=j.items||[],calls=j.calls||[];
  $('campDetail').innerHTML=`<div class="panel">
    <div class="toolbar">
      <h2 style="margin:0" class="grow">Кампания #${c.id}: ${esc(c.name)}</h2>
      ${isAdmin()?`<button class="btn g sm" data-a="cstart" data-id="${c.id}">${ico('play')}Запустить</button>
      <button class="btn sm" style="background:#35557f" data-a="cpause" data-id="${c.id}">${ico('pause')}Пауза</button>
      <button class="btn d sm" data-a="cstop" data-id="${c.id}">${ico('stop')}Остановить</button>
      <button class="btn sm" style="background:#6a5acd" data-a="cretry" data-id="${c.id}" title="Вернуть в очередь контакты, исчерпавшие лимит попыток">${ico('refresh')}Дозвонить исчерпанные</button>`:''}
    </div>
    <div class="toolbar" style="margin-bottom:14px">
      ${statusPill(c.status)}
      <span class="badge b">${c.flow==='message'?'озвучка':c.flow==='operator'?'на оператора':'ИИ-агент'}</span>
      <span class="hint">шаблон: ${esc(c.template_name||'—')} · попыток: ${c.retry_max>=0?c.retry_max:'по умолч.'} · окно ${esc((c.schedule&&c.schedule.start)||'-')}–${esc((c.schedule&&c.schedule.end)||'-')}</span>
      <span class="sp grow" style="flex:1"></span>
      ${isAdmin()?`<button class="btn ghost sm" data-a="addc" data-id="${c.id}">${ico('plus')}Контакты</button>
      <button class="btn ghost sm" data-a="clearc" data-id="${c.id}">Очистить</button>
      <button class="btn ghost sm" data-a="editc2" data-id="${c.id}">${ico('edit')}Настройки</button>`:''}
    </div>
    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:14px">
      <div class="metric"><b>${nFmt(items.length)}</b><span class="l">контактов в списке</span></div>
      <div class="metric"><b class="okc">${nFmt(c.calls_ok||0)}</b><span class="l">успешных из ${nFmt(c.calls||0)} звонков</span></div>
      <div class="metric"><b>${nFmt(items.filter(i=>['done_ok','done_agent','operator_ok'].includes(i.status)).length)}</b><span class="l">обработано до конца</span></div>
      <div class="metric"><b>${nFmt(calls.filter(x=>x.duration_sec).reduce((a,x)=>a+(x.duration_sec||0),0))} с</b><span class="l">общая длительность</span></div>
    </div>
    <div class="toolbar" style="margin-bottom:8px">
      <div class="seg"><button id="segI" class="${campView==='items'?'on':''}" onclick="campSeg('items')">Контакты <span class="cnt" style="font-size:11px;color:var(--dim)">${items.length}</span></button>
      <button id="segC" class="${campView==='calls'?'on':''}" onclick="campSeg('calls')">Звонки <span class="cnt" style="font-size:11px;color:var(--dim)">${calls.length}</span></button></div>
    </div>
    <div id="campTab"></div>
  </div>`;
  $('campDetail').dataset.cid=String(id);
  wire($('campDetail'));
  campSeg(campView);
  $('campDetail').scrollIntoView({behavior:'smooth',block:'start'});
}
function campSeg(v){
  campView=v;
  const segI=$('segI'),segC=$('segC');
  if(segI)segI.className=v==='items'?'on':'';if(segC)segC.className=v==='calls'?'on':'';
  const cid=parseInt($('campDetail').dataset.cid||'0');
  if(!cid)return;
  if(v==='calls'){safe(()=>renderCampCalls(cid));}
  else{safe(()=>renderCampItems(cid));}
}
async function renderCampItems(id){
  const j=await api(`/campaigns/${id}`);
  const items=(j&&j.items)||[];
  $('campTab').innerHTML=`<div class="tblwrap thin" style="max-height:340px"><table><tr><th>Контакт</th><th>Телефон</th><th class="num">Попытки</th><th>Статус</th><th>Результат</th><th class="num">Перезвон</th></tr>
    ${items.slice().reverse().map(i=>`<tr><td>${av(i.contact_name)}<span style="width:4px"></span>${esc(i.contact_name)}</td>
      <td class="nowrap"><b>${esc(i.contact_phone)}</b></td><td class="num">${i.attempts}</td><td>${badge(i.status)}</td>
      <td class="dim">${esc(i.last_result||'')}</td><td class="num">${i.next_attempt_at?dTxt(i.next_attempt_at):''}</td></tr>`).join('')
      ||`<tr><td colspan="6">${empty('inbox','Список пуст','Добавьте контакты в кампанию')}</td></tr>`}</table></div>`;
}
async function renderCampCalls(id){
  const j=await api(`/campaigns/${id}`);
  const calls=(j&&j.calls)||[];
  $('campTab').innerHTML=`<div class="tblwrap thin" style="max-height:340px"><table><tr><th class="num">#</th><th>Время</th><th>Контакт</th><th>С номера</th><th>Длит.</th><th>Результат</th><th>Детали агента</th><th class="num">Запись</th></tr>
    ${calls.slice().reverse().map(x=>`<tr><td class="dim">${x.id}</td><td class="nowrap">${dTxt(x.started_at)}</td><td>${av(x.contact_name)}<span style="width:4px"></span>${esc(x.contact_name)}</td>
      <td class="nowrap">${esc(x.caller_id)}</td><td class="num">${x.duration_sec?Math.round(x.duration_sec)+' с':''}</td>
      <td>${badge(x.result||x.status)}</td><td class="dim">${esc(shortAgent(x.agent_result))}</td>
      <td class="num"><span class="btn-row">
      ${recLink(x)}
      ${isAdmin()?`<button class="iconbtn sm" style="width:29px;height:29px" data-a="uprec" data-id="${x.id}" title="Загрузить запись">${ico('up')}</button>`:''}
      </span></td></tr>`).join('')
      ||`<tr><td colspan="8">${empty('phone','Звонков ещё нет','Запустите кампанию — звонки появятся здесь в реальном времени')}</td></tr>`}</table></div>`;
  wire($('campTab'));
}
function shortAgent(s){if(!s)return'';try{const o=JSON.parse(s);return(o.qualified?'✓ квалифицирован · ':'')+(o.summary||o.engine||o.answer||'').slice(0,90);}catch(e){return'';}}
function showCampFromLink(el){const id=parseInt(el.dataset.id);if(id)showCamp(id);}
async function addContactsToCamp(id){
  if(!state.contacts.length)await safe(fetchContacts);
  if(!state.databases.length)await safe(fetchDatabases);
  const cons=state.contacts||[];
  const dbs=state.databases||[];
  const rows=cons.map(c=>`<label data-ck="${c.consent?1:0}"><input type="checkbox" class="ac" value="${c.id}" ${c.consent?'checked':''}>
    <span>${esc(c.name)} — <b class="nowrap">${esc(c.phone)}</b></span>${c.consent?'':'<span class="badge y">нет согласия</span>'}</label>`).join('');
  modal(`<h2>${ico('plus')}Добавить контакты · кампания #${id}</h2>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <label class="sw"><input id="consentOnly" type="checkbox" checked><span class="trk"></span>только с согласием</label>
      <div style="position:relative;flex:1;min-width:150px">${ico('search','position:absolute;left:10px;top:50%;transform:translateY(-50%);width:13px;height:13px;color:#5f7798;z-index:1')}
      <input id="pickQ" class="input" style="padding-left:30px" placeholder="Фильтр списка…"></div>
    </div>
    <div class="picklist" id="pickList">${rows||'<div class="dim" style="padding:10px">База контактов пуста</div>'}</div>
    <div class="toolbar" style="margin:0">
      <button class="btn" onclick="addSelContacts(${id})">Добавить выбранные</button>
      <button class="btn ghost" onclick="addAllContacts(${id})">Все с согласием</button>
      <button class="btn x" onclick="closeModal()">Закрыть</button>
      <span class="pillmsg" id="pickMsg"></span>
    </div>
    <div class="toolbar" style="margin:8px 0 0;border-top:1px solid #1d3353;padding-top:10px">
      <select id="pickDb" class="select" style="flex:1;min-width:180px">${dbs.map(d=>`<option value="${d.id}">${esc(d.name)} (${nFmt(d.contacts_count||0)})</option>`).join('')||'<option value="0">— баз нет —</option>'}</select>
      <button class="btn ghost" onclick="addDbToCamp(${id})" ${dbs.length?'':'disabled'}>Добавить базу целиком</button>
    </div>`);
  const applyFilter=()=>{
    const only=$('consentOnly').checked,q=($('pickQ').value||'').toLowerCase();
    document.querySelectorAll('#pickList label').forEach(row=>{
      const noC=row.dataset.ck==='0';
      const nm=row.textContent.toLowerCase();
      row.style.display=(only&&noC)||(q&&!nm.includes(q))?'none':'';
    });
    const vis=[...document.querySelectorAll('#pickList label')].filter(r=>r.style.display!=='none');
    const nCk=vis.filter(r=>r.querySelector('input').checked).length;
    $('pickMsg').textContent='Видно: '+vis.length+' · выбрано: '+nCk;
  };
  $('consentOnly').onchange=()=>{document.querySelectorAll('#pickList input.ac').forEach(ch=>{ch.checked=$('consentOnly').checked?ch.closest('label').dataset.ck==='1':ch.checked;});applyFilter();};
  $('pickQ').oninput=applyFilter;
  applyFilter();
}
async function addSelContacts(id){
  const ids=[...document.querySelectorAll('#pickList .ac:checked')].map(x=>parseInt(x.value));
  if(!ids.length)return toast('Ничего не выбрано','warn');
  const j=await safe(()=>api(`/campaigns/${id}/add-contacts`,{body:{contact_ids:ids}}));
  if(j&&j.ok){closeModal();toast('Добавлено: '+j.added);await loadCamps();showCamp(id);}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
async function addAllContacts(id){
  const j=await safe(()=>api(`/campaigns/${id}/add-contacts`,{body:{consent_only:true}}));
  if(j&&j.ok){closeModal();toast('Добавлено: '+j.added);await loadCamps();showCamp(id);}
}
async function addDbToCamp(id){
  const dbId=parseInt(($('pickDb')||{}).value||'0');
  if(!dbId)return toast('Выберите базу','warn');
  const only=($('consentOnly')||{}).checked!==false;
  const j=await safe(()=>api(`/campaigns/${id}/add-contacts`,{body:{database_id:dbId,consent_only:only}}));
  if(j&&j.ok){closeModal();toast('Добавлено из базы: '+j.added);await loadCamps();showCamp(id);}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
async function clearCamp(id){
  if(!(await ask('Очистить список кампании','Все контакты будут убраны из списка кампании. Звонки и история сохранятся.','Очистить')))return;
  const j=await safe(()=>api(`/campaigns/${id}/clear`,{body:{}}));
  if(j&&j.ok){toast('Список очищен');showCamp(id);}
  else toast('Очистить нельзя ('+((j&&j.error)||'?')+'): сначала остановите кампанию и дождитесь конца звонков','err');
}

/* ================= пул номеров ================= */
async function loadNumbers(){
  const j=await api('/numbers');
  if(!j||j.error)return;
  state.numbers=j.numbers;
  $('nCnt').textContent='· '+nFmt(j.numbers.length);
  const grid=$('poolGrid');
  grid.innerHTML=j.numbers.length?j.numbers.map(n=>numCard(n)).join('')
    :empty('phone','Номеров пока нет','Добавьте первый номер Caller ID — без него звонки не пойдут');
  wire(grid);
}
async function addNumber(){
  const j=await safe(()=>api('/numbers/save',{body:{number:$('nNumber').value.trim(),label:$('nLabel').value.trim(),daily_limit:parseInt($('nLimit').value||100)}}));
  const m=$('numMsg');
  if(j&&j.ok){m.textContent='✓ Добавлено';m.classList.add('ok');m.classList.remove('err');
    $('nNumber').value='';$('nLabel').value='';await loadNumbers();safe(loadDash);}
  else{m.textContent='Ошибка: '+((j&&j.error)||'?');m.classList.add('err');m.classList.remove('ok');}
}
function editNum(id){
  const n=state.numbers.find(x=>x.id===id)||{};
  modal(`<h2>${ico('phone')}Номер #${n.id}</h2>
    <div class="formgrid">
      <div class="fld span2"><span class="lbl">Номер (E.164)</span><input id="eNum" class="input ctl" value="${esc(n.number)}"></div>
      <div class="fld"><span class="lbl">Метка</span><input id="eLabel" class="input ctl" value="${esc(n.label)}"></div>
      <div class="fld"><span class="lbl">Суточный лимит звонков</span><input id="eLimit" type="number" min="1" class="input ctl" value="${n.daily_limit}"></div>
      <div class="fld"><span class="lbl">Вес (приоритет)</span><input id="eWeight" type="number" min="1" class="input ctl" value="${n.weight||1}"></div>
      <div class="fld"><span class="lbl">Провайдер</span><select id="eProv" class="select ctl">${['sim','uis','ami','megafon_vats'].map(p=>`<option value="${p}" ${n.provider===p?'selected':''}>${p}</option>`).join('')}</select></div>
    </div>
    <div class="form-actions"><button class="btn" onclick="saveNum(${id})">${ico('check')}Сохранить</button><button class="btn x" onclick="closeModal()">Отмена</button></div>`);
}
async function saveNum(id){
  const j=await safe(()=>api('/numbers/save',{body:{id,number:$('eNum').value.trim(),label:$('eLabel').value,daily_limit:parseInt($('eLimit').value),weight:parseInt($('eWeight').value||1),provider:$('eProv').value}}));
  if(j&&j.ok){closeModal();toast('Номер обновлён');await loadNumbers();safe(loadDash);}
  else toast('Ошибка','err');
}
async function setQuar(id,on){await safe(()=>api('/numbers/quarantine',{body:{id,on}}));await loadNumbers();safe(loadDash);}
async function toggleNum(id,active){await safe(()=>api('/numbers/save',{body:{id,active}}));await loadNumbers();safe(loadDash);}
async function resetPool(){
  if(!(await ask('Сбросить лимиты и карантин','Суточные счётчики всех номеров будут обнулены, карантин снят.','Сбросить',false)))return;
  const j=await safe(()=>api('/numbers/reset',{body:{}}));
  if(j&&j.ok){toast('Лимиты и карантин сброшены');await loadNumbers();safe(loadDash);}
}

/* ================= ACD ================= */
async function loadAcd(){
  const j=await api('/acd');
  if(!j||j.error)return;
  const ops=j.operators||[],acd=j.acd||[];
  $('opCnt').textContent='· '+nFmt(ops.length);
  $('opList').innerHTML=ops.length?ops.map(o=>{
    const st=o.status==='free'?'<span class="badge g"><span class="dot"></span>свободен</span>'
      :o.status==='busy'?'<span class="badge r">в разговоре</span>'
      :o.status==='break'?'<span class="badge y">перерыв</span>':'<span class="badge">офлайн</span>';
    return `<div class="opcard">${av(o.name)}<div><div class="onm">${esc(o.name)}</div><div class="oext">${esc(o.ext||'внутр. —')}</div></div>${st}</div>`;}).join('')
    :empty('users','Операторов нет','Операторы появятся при подключении UIS/CRM-аккаунтов или через ACD');
  $('opMsg').textContent='';
  const q=$('acdList');
  if(acd.length){
    q.innerHTML=acd.map(a=>`<div class="acdcard">
      ${av(a.contact_name,true)}
      <div class="who" style="min-width:0"><div class="wn">${esc(a.contact_name)}</div>
        <div class="wd">кампания #${a.campaign_id} · ожидает оператора</div></div>
      <span class="wa nowrap">${esc(a.contact_phone)}</span>
      <button class="btn g" data-a="accept" data-id="${a.id}" data-call="${a.call_id||0}">${ico('phone')}Принять</button>
    </div>`).join('');
  }else q.innerHTML=empty('check','Очередь пуста','Все квалифицированные ИИ-агентом контакты уже обработаны');
  wire(q);
  if(state.curCall){showCallBar();}else hideCallBar();
}
async function acceptAcd(id,callId){
  const j=await safe(()=>api('/acd/accept',{body:{id}}));
  if(j&&j.ok){state.curCall={call_id:callId||0,acd_id:id,op_id:j.operator_id||0};
    toast('Звонок принят — абонент соединён с вами');
    await loadAcd();showCallBar();}
  else toast('Не удалось принять: '+((j&&j.error)||'?'),'err');
}
function showCallBar(){
  const b=$('callPanel');b.style.display='';
  $('callWho').textContent='Абонент на линии';
  $('callSince').textContent='— завершите разговор после того, как договоритесь';
  const seg=$('opQuick');if(seg){[...seg.children].forEach(x=>x.className=x.dataset.st==='busy'?'on':'');}
}
function hideCallBar(){$('callPanel').style.display='none';}
async function finishCall(){
  if(!state.curCall)return;
  const j=await safe(()=>api('/calls/complete',{body:{call_id:state.curCall.call_id,operator_id:state.curCall.op_id||0}}));
  state.curCall=null;hideCallBar();
  if(j&&j.ok)toast('Разговор завершён, результат записан');
  else toast('Разговор завершён','info');
  await loadAcd();safe(loadDash);
}
async function setMyStatus(s){
  const j=await safe(()=>api('/operators/status',{body:{status:s}}));
  const seg=$('opQuick');if(seg){[...seg.children].forEach(x=>x.className=x.dataset.st===s?'on':'');}
  if(j&&j.ok){toast(s==='free'?'Статус: свободен':'Статус: перерыв','info');await loadAcd();}
  else{$('opMsg').textContent='Не удалось сменить статус';$('opMsg').classList.add('err');}
}

/* ================= журнал ================= */
const jResOpts=[['','Все результаты'],['done_ok','Доставлено'],['operator_ok','Оператор'],['done_agent','ИИ-агент · квалиф.'],
  ['qualified_no','ИИ-агент · не квалиф.'],['machine','Автоответчик'],['busy','Занято'],['no_answer','Нет ответа'],
  ['failed','Сбой'],['blocked','Блок антиспама'],['no_operator','Оператор не ответил'],['exhausted','Попытки исчерпаны'],['blocked_no_consent','Нет согласия']];
function resetJournal(){state.jN=parseInt($('jLimit').value||50)||50;state.jAll=false;}
async function loadJournal(){
  const st=$('jStatus').value;
  const j=await api('/calls',{body:{status:st||undefined,limit:state.jN}});
  if(!j||j.error)return;
  const rows=j.calls||[];
  $('jCnt').textContent='· '+nFmt(rows.length);
  $('journalTable').innerHTML='<tr><th class="num">#</th><th>Время</th><th>Контакт</th><th>С номера</th><th class="num">Кампания</th><th>Длит.</th><th>Результат</th><th>Детали</th></tr>'+
   rows.map(x=>`<tr><td class="dim"><a href="#" onclick="event.preventDefault();showTimeline(${x.id})" title="События ВАТС по звонку">${x.id}</a></td><td class="nowrap">${dTxt(x.started_at)}</td>
    <td style="min-width:0">${av(x.contact_name)}<span style="width:4px"></span>${esc(x.contact_name)}<div class="dim" style="font-size:11px">${esc(x.contact_phone)}</div></td>
    <td class="nowrap">${esc(x.caller_id)}</td><td class="num">#${x.campaign_id||''}</td><td class="num">${x.duration_sec?Math.round(x.duration_sec)+' с':''}</td>
    <td>${badge(x.result||x.status)}</td>
    <td class="dim" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.detail||'')} ${shortAgent(x.agent_result)}</td></tr>`).join('')
   ||`<tr><td colspan="8">${empty('inbox','Звонков нет','Выберите другой фильтр результата или подождите — звонки идут в реальном времени')}</td></tr>`;
  $('jMoreBtn').classList.toggle('hidden',rows.length<state.jN||state.jAll);
}
function moreJournal(){
  if(state.jAll)return;
  state.jN=Math.min(state.jN+100,2000);
  state.jAll=state.jN>=2000;
  safe(loadJournal);
}
async function upRecording(callId,campId){
  const inp=document.createElement('input');inp.type='file';inp.accept='audio/*';
  inp.onchange=async()=>{
    const f=inp.files[0];if(!f)return;
    const b64=await new Promise(res=>{const rd=new FileReader();rd.onload=()=>res(rd.result.split(',')[1]);rd.readAsDataURL(f);});
    const j=await safe(()=>api('/calls/recording',{body:{call_id:callId,filename:f.name,data_b64:b64}}));
    if(j&&j.ok){toast('Запись сохранена: '+j.recording);
      if(isActive('campaigns')&&campId)safe(()=>showCamp(campId));
      else if(isActive('journal'))debRefresh('journal',200);
    }
    else toast('Ошибка: '+((j&&j.error)||'?'),'err');
  };
  inp.click();
}
async function exportCalls(){
  const r=await fetch('/api/v2/export/calls.csv',{headers:{'X-Ats-Token':state.token}});
  if(!r.ok)return toast('Экспорт доступен только администратору','err');
  const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='calls.csv';a.click();
}

/* ================= отчёты ================= */
function repToday(off){const d=new Date();d.setDate(d.getDate()-(off||0));
  const p=n=>String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}
function repPreset(days){
  if(days===0){state.repFrom='';state.repTo='';}
  else{state.repFrom=repToday(days-1);state.repTo=repToday(0);}
  document.querySelectorAll('#repSeg button').forEach(b=>b.classList.toggle('on',parseInt(b.dataset.days)===days));
  safe(loadReports);
}
function applyRepDates(){
  const f=$('rFrom').value,t=$('rTo').value;
  if(!f||!t)return toast('Укажите обе даты периода','warn');
  if(f>t)return toast('Дата «с» позже даты «по»','warn');
  const diff=Math.round((new Date(t)-new Date(f))/86400000);
  if(diff>366)return toast('Максимальный период — 366 дней','warn');
  state.repFrom=f;state.repTo=t;
  document.querySelectorAll('#repSeg button').forEach(b=>b.classList.remove('on'));
  safe(loadReports);
}
function syncRepUI(){
  $('rFrom').value=state.repFrom||'';
  $('rTo').value=state.repTo||'';
  if(!state.repFrom&&!state.repTo)document.querySelectorAll('#repSeg button').forEach(b=>b.classList.toggle('on',parseInt(b.dataset.days)===0));
}
async function loadReports(){
  syncRepUI();
  let qs='';
  if(state.repFrom&&state.repTo)qs='?from='+state.repFrom+'&to='+state.repTo;
  const j=await api('/reports'+qs);
  if(!j||j.error){if(j&&j.error==='bad_range')toast('Некорректный период отчёта','err');return;}
  const t=j.today||{};
  const per=j.period||{};
  const custom=!!per.custom;
  const span=custom?'за '+((per.from||'').slice(8,10)+'.'+(per.from||'').slice(5,7))+' — '+((per.to||'').slice(8,10)+'.'+(per.to||'').slice(5,7))+' ':'';
  $('repScope').textContent=per.label||(custom?'период':'за сегодня');
  $('repCards').innerHTML=
   `<div class="metric acc"><span class="ico">${ico('phone')}</span><b>${nFmt(t.n||0)}</b><span class="l">звонков ${span||'сегодня'}</span></div>
    <div class="metric"><span class="ico" style="color:var(--ok);background:rgba(52,211,153,.13)">${ico('check')}</span><b class="okc">${nFmt(t.ok||0)}</b><span class="l">успешных</span></div>
    <div class="metric"><span class="ico">${ico('bolt')}</span><b>${t.ok_rate||0}%</b><span class="l">конверсия</span></div>
    <div class="metric"><span class="ico">${ico('clock')}</span><b>${t.avg_dur||0} с</b><span class="l">ср. длительность</span></div>`;
  const bres=(j.by_result||[]).slice().sort((a,b)=>b.c-a.c);
  const bresMax=Math.max(1,...bres.map(x=>x.c));
  $('repResults').innerHTML=bres.length?`<div class="hbars">`+bres.map(r=>`<div class="hbar"><span class="hn">${esc(REP_LABEL[r.result]||r.result)}</span>
    <span class="ht"><i style="width:${Math.round(100*r.c/bresMax)}%;background:${RCOL[r.result]||'#3d86ff'}"></i></span><span class="hv">${nFmt(r.c)}</span></div>`).join('')+`</div>`
    :empty('reports','Пока нет звонков','Запустите кампанию в рабочем окне');
  $('repCamps').innerHTML=(j.by_campaign&&j.by_campaign.length)?`<div class="hbars">`+
   j.by_campaign.map(r=>{const ok=r.ok||0,bad=r.bad||0;const maxA=Math.max(1,...j.by_campaign.map(x=>x.n));return `<div class="hbar"><span class="hn">#${r.campaign_id} ${esc(r.name)}</span>
    <span class="ht"><i style="width:${Math.round(100*r.n/maxA)}%"></i></span><span class="hv">${nFmt(r.n)}</span></div>
    <div class="dim" style="grid-column:2;font-size:11px;margin-top:-4px">успешных ${ok} · плохих ${bad}</div>`;}).join('')+`</div>`
    :empty('reports','Пока нет звонков','Появятся после запуска кампаний');
  const byN=(j.by_number||[]).slice().sort((a,b)=>b.n-a.n);
  $('repNumbers').innerHTML=byN.length?`<div class="hbars">`+
   byN.map(r=>`<div class="hbar"><span class="hn">${esc(r.caller_id)}</span><span class="ht"><i style="width:${Math.round(100*r.n/Math.max(1,...byN.map(x=>x.n)))}%;background:#53c8ef"></i></span><span class="hv">${nFmt(r.n)}</span></div>
    <div class="dim" style="grid-column:2;font-size:11px;margin-top:-4px">успешных ${r.ok||0}</div>`).join('')+`</div>`
    :empty('reports','Пока нет звонков','Появятся после первых звонков');
  const tr=(j.trend||[]);
  const step=Math.max(1,Math.ceil(tr.length/24));
  const many=tr.length>16;
  const trMax=Math.max(1,...tr.map(r=>r.n));
  $('repTrend').innerHTML=tr.length?`<div class="trend">`+tr.map((r,i)=>{
    const h=Math.round(100*r.n/trMax);
    const lbl=(i%step===0||i===tr.length-1)?(r.label||(r.date||'').slice(8,10)+'.'+(r.date||'').slice(5,7)):'';
    return `<div class="tcol"><div class="tb" style="height:${Math.max(4,h)}%" title="${esc(r.label||r.date)} — звонков: ${r.n}, успех: ${r.ok}">
      ${h>16&&!many?`<em>${r.n}</em>`:''}</div><div class="td">${lbl?esc(lbl):''}</div></div>`;}).join('')+`</div>`
    :empty('reports','Нет данных за период','Звонки появятся в динамике после запуска кампаний');
}

/* ================= чёрный список ================= */
async function loadBlacklist(){
  const j=await api('/blacklist');
  if(!j||j.error)return;
  const b=j.blacklist||[];
  $('blCnt').textContent='· '+nFmt(b.length);
  $('blTable').innerHTML=`<tr><th>Телефон</th><th>Причина</th><th>Источник</th><th>Добавлен</th>${isAdmin()?'<th class="num"></th>':''}</tr>`+
   b.map(x=>`<tr><td class="nowrap"><b>${esc(x.phone)}</b></td><td>${esc(x.reason)}</td>
     <td>${x.source==='auto'?'<span class="badge r">авто</span>':'<span class="badge b">оператор</span>'}</td>
     <td class="dim nowrap">${dTxt(x.created)}</td>
     ${isAdmin()?`<td class="num"><button class="iconbtn sm" style="width:29px;height:29px;color:#ffa7b4" data-a="delbl" data-id="${x.id}" title="Убрать из списка">${ico('trash')}</button></td>`:''}</tr>`).join('')
   ||`<tr><td colspan="5">${empty('shield','Список пуст','Никто не жаловался на звонки — отлично')}</td></tr>`;
  wire($('blTable'));
}
async function addBlacklist(){
  const ph=$('blPhone').value.trim();
  if(!ph)return toast('Укажите телефон','warn');
  const j=await safe(()=>api('/blacklist/add',{body:{phone:ph,reason:$('blReason').value||'manual'}}));
  if(j&&j.ok){$('blPhone').value='';$('blReason').value='';toast('Номер добавлен в ЧС');await loadBlacklist();}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
async function delBlack(id){
  const j=await safe(()=>api('/blacklist/delete',{body:{id}}));
  if(j&&j.ok){toast('Запись удалена из ЧС');await loadBlacklist();}
}

/* ================= шаблоны ================= */
async function loadTemplates(silent){
  const j=await api('/templates');
  if(!j||j.error)return;
  state.templates=j.templates;
  if(silent)return;
  $('tCnt').textContent='· '+nFmt(j.templates.length);
  $('tplTable').innerHTML=`<tr><th>Название</th><th>Текст / сценарий</th><th style="width:80px">Вкл.</th>${isAdmin()?'<th class="num">Действия</th>':''}</tr>`+
   j.templates.map(t=>`<tr><td style="min-width:0"><b>${esc(t.name)}</b></td>
     <td class="dim" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((t.text||'').slice(0,160))}</td>
     <td>${t.active?'<span class="badge g">да</span>':'<span class="badge">нет</span>'}</td>
     ${isAdmin()?`<td class="num"><span class="btn-row"><button class="btn ghost sm" data-a="editt" data-id="${t.id}">${ico('edit')}Ред.</button>
     <button class="btn d sm" data-a="delt" data-id="${t.id}">${ico('trash')}</button></span></td>`:''}</tr>`).join('')
   ||`<tr><td colspan="4">${empty('file','Шаблонов нет','Создайте шаблон озвучки или сценарий ИИ-агента')}</td></tr>`;
  wire($('tplTable'));
}
function editTemplate(id){
  const t=state.templates.find(x=>x.id===id)||{id:0,name:'',text:'',active:true,scenario:{}};
  const scStr=typeof t.scenario==='string'?t.scenario:JSON.stringify(t.scenario||{},null,1);
  modal(`<h2>${ico('file')}${id?'Шаблон #'+id:'Новый шаблон'}</h2>
    <div class="formgrid">
      <div class="fld span2"><span class="lbl">Название</span><input id="tName" class="input ctl" value="${esc(t.name)}"></div>
      <div class="fld span2"><span class="lbl">Текст озвучки — подстановки: {name} {phone} {group} {note}</span>
        <textarea id="tText" class="textarea ctl">${esc(t.text)}</textarea></div>
      <div class="span2"><label class="sw"><input id="tActive" type="checkbox" ${t.active?'checked':''}><span class="trk"></span>шаблон активен</label></div>
      <div class="fld span2"><span class="lbl">Сценарий ИИ-агента (JSON: вопросы / варианты, ответы 1/2)</span>
        <textarea id="tSc" class="textarea ctl" style="min-height:230px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px">${esc(scStr)}</textarea></div>
    </div>
    <div class="form-actions"><button class="btn" onclick="saveTemplate(${id||0})">${ico('check')}Сохранить</button><button class="btn x" onclick="closeModal()">Отмена</button></div>`);
}
async function saveTemplate(id){
  let sc={};
  try{sc=JSON.parse($('tSc').value||'{}');}catch(e){return toast('Сценарий не JSON: '+e.message,'err');}
  const j=await safe(()=>api('/templates/save',{body:{id:id,name:$('tName').value,text:$('tText').value,scenario:sc,active:$('tActive').checked}}));
  if(j&&j.ok){closeModal();toast('Шаблон сохранён');await loadTemplates();}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
async function delTemplate(id){
  if(!(await ask('Удалить шаблон','Шаблон будет удалён. Кампании, которые его используют, останутся без него.','Удалить')))return;
  const j=await safe(()=>api('/templates/delete',{body:{id}}));
  if(j&&j.ok){toast('Шаблон удалён');await loadTemplates();}
}

/* ================= пользователи и операторы (админ) ================= */
async function loadUsers(){
  const j=await api('/users');
  if(!j||!j.users)return;
  state.users=j.users;
  $('uCnt').textContent='· '+nFmt(j.users.length);
  const me=state.login;
  $('usersTable').innerHTML=`<tr><th>Пользователь</th><th>Роль</th><th class="num">Внутр.</th><th>На линии</th><th style="width:74px">Активен</th><th class="num">Действия</th></tr>`+
   j.users.map(u=>{
     const isMe=u.login===me;
     const roleTxt=u.role==='admin'?'<span class="badge b">админ</span>':'<span class="badge cy">оператор</span>';
     const st=(u.op_status==='free')?'<span class="badge g"><span class="dot"></span>свободен</span>'
       :(u.op_status==='busy')?'<span class="badge r">в разговоре</span>'
       :(u.op_status==='break')?'<span class="badge y">перерыв</span>'
       :'<span class="badge">офлайн</span>';
     const nm=u.op_name||u.login;
     return `<tr data-uid="${u.id}"><td style="min-width:0"><span class="cell-av">${av(nm)}<span style="min-width:0">
        <span class="nm">${esc(nm)}${isMe?' <span class="badge y" style="font-size:10px">вы</span>':''}</span>
        <span class="ph">@${esc(u.login)}</span></span></span></td>
      <td>${roleTxt}</td><td class="num">${esc(u.ext||'—')}</td><td>${st}</td>
      <td><label class="sw" style="display:inline-flex" title="${isMe?'Это вы':(u.active?'Выключить доступ':'Включить доступ')}">
        <input type="checkbox" data-uact="${u.id}" ${u.active?'checked':''} ${isMe?'disabled':''}><span class="trk"></span></label></td>
      <td class="num"><span class="btn-row">
        <button class="btn ghost sm" data-a="edituser" data-id="${u.id}">${ico('edit')}Ред.</button>
        ${isMe?'':`<button class="btn d sm" data-a="deluser" data-id="${u.id}" title="Удалить">${ico('trash')}</button>`}
      </span></td></tr>`;
   }).join('')
   ||`<tr><td colspan="6">${empty('users','Пользователей нет','Создайте первого оператора')}</td></tr>`;
  wire($('usersTable'));
  document.querySelectorAll('#usersTable input[data-uact]').forEach(ch=>{
    ch.onchange=()=>togUser(parseInt(ch.dataset.uact),ch.checked?1:0);
  });
}
function editUser(id){
  const u=(state.users||[]).find(x=>x.id===id)||{id:0,login:'',role:'operator',active:1,ext:'',op_name:''};
  const isMe=u.login===state.login;
  modal(`<h2>${ico('users')}${id?'Пользователь @'+esc(u.login):'Новый пользователь'}</h2>
    <div class="formgrid">
      <div class="fld"><span class="lbl">Логин (для входа)</span><input id="uLogin" class="input ctl" value="${esc(u.login)}" placeholder="operator2" spellcheck="false"></div>
      <div class="fld"><span class="lbl">Имя оператора</span><input id="uName" class="input ctl" value="${esc(u.op_name||'')}" placeholder="Иван Оператор"></div>
      <div class="fld"><span class="lbl">Роль</span>
        <select id="uRole" class="select ctl" ${isMe?'disabled':''}>
          <option value="operator" ${u.role==='operator'?'selected':''}>оператор (принимает звонки)</option>
          <option value="admin" ${u.role==='admin'?'selected':''}>администратор (всё)</option>
        </select></div>
      <div class="fld"><span class="lbl">Внутренний номер</span><input id="uExt" class="input ctl" value="${esc(u.ext||'')}" placeholder="101"></div>
      <div class="fld span2"><span class="lbl">Пароль${id?' (пусто — оставить прежний)':' (мин. 6 символов)'}</span>
        <input id="uPass" type="password" class="input ctl" placeholder="••••••" autocomplete="new-password"></div>
      <div class="span2"><label class="sw"><input id="uActive" type="checkbox" ${u.active===0?'':'checked'} ${isMe?'disabled':''}><span class="trk"></span>доступ разрешён</label></div>
    </div>
    <div class="form-actions">
      <button class="btn" onclick="saveUser(${id||0})">${ico('check')}Сохранить</button>
      <button class="btn x" onclick="closeModal()">Отмена</button>
    </div>`);
}
async function saveUser(id){
  const login=$('uLogin').value.trim().toLowerCase();
  const name=$('uName').value.trim();
  const ext=$('uExt').value.trim();
  const role=$('uRole').value;
  const pass=$('uPass').value;
  if(!login)return toast('Укажите логин','warn');
  if(pass&&pass.length<6)return toast('Пароль — минимум 6 символов','warn');
  if(!id&&!pass)return toast('Новому пользователю нужен пароль','warn');
  const j=await safe(()=>api('/users/save',{body:{id:id,login:login,name:name,ext:ext,role:role,password:pass,active:$('uActive').checked}}));
  if(j&&j.ok){closeModal();toast('Пользователь сохранён');await loadUsers();}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
async function togUser(id,active){
  const j=await safe(()=>api('/users/save',{body:{id:id,active:active===1}}));
  if(j&&j.ok){toast(active?'Доступ включён':'Доступ отключён — пользователь выйдет из системы','info');await loadUsers();}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}
async function delUser(id){
  const u=(state.users||[]).find(x=>x.id===id)||{};
  if(!(await ask('Удалить пользователя','Будет удалён пользователь @'+(u.login||'')+'. Оператор больше не сможет войти.','Удалить')))return;
  const j=await safe(()=>api('/users/delete',{body:{id:id}}));
  if(j&&j.ok){toast('Пользователь удалён');await loadUsers();}
  else toast('Ошибка: '+((j&&j.error)||'?'),'err');
}

/* ================= настройки ================= */
function renderDayChips(days){
  const sel=new Set((days||[0,1,2,3,4,5,6]).map(x=>String(x)));
  $('sDays').innerHTML=DAYS.map(([v,l])=>`<label class="daychip ${sel.has(v)?'on':''}"><input type="checkbox" value="${v}" ${sel.has(v)?'checked':''} onchange="dayChip(this)">${l}</label>`).join('');
}
function dayChip(ch){ch.closest('.daychip').classList.toggle('on',ch.checked);}
function selectedDays(){return [...document.querySelectorAll('#sDays input:checked')].map(x=>parseInt(x.value));}
async function loadSettings(){
  const j=await api('/settings');
  if(!j||!j.settings)return;
  const s=j.settings;state.settings=s;
  $('sProvider').value=s.provider||'';
  $('sChannels').value=s.max_channels;
  $('sRetryMax').value=s.retry_max;
  $('sRetryDelay').value=s.retry_delay_min;
  $('sCooldown').value=s.line_cooldown_sec;
  $('sWatchdog').value=s.watchdog_timeout_min;
  $('sAcdT').value=s.acd_wait_timeout_sec;
  $('sWinStart').value=s.window_start;
  $('sWinEnd').value=s.window_end;
  renderDayChips(s.window_days);
  $('sCrm').value=(s.crm&&s.crm.driver)||'csv';
  $('sQuar').value=s.auto_quarantine_on_complaints;
  $('sSimAns').value=s.sim_answer;
  $('sConsent').checked=!!s.consent_required;
  const ro=!isAdmin();
  ['sProvider','sChannels','sRetryMax','sRetryDelay','sCooldown','sWatchdog','sAcdT','sVatsT','sQuar','sCrm','sSimAns','sWinStart','sWinEnd','sConsent'].forEach(i=>{const el=$(i);if(el)el.disabled=ro;});
  document.querySelectorAll('#sDays input').forEach(x=>{x.disabled=ro;});
  $('rawPanel').classList.toggle('hidden',!isAdmin());
  $('vatsPanel').classList.toggle('hidden',!isAdmin());
  $('passPanel').classList.remove('hidden');
  if(isAdmin())safe(()=>loadUsers());
}
async function saveSettings(){
  const j=await safe(()=>api('/settings/save',{body:{
    provider:$('sProvider').value,max_channels:parseInt($('sChannels').value||3),
    retry_max:parseInt($('sRetryMax').value||2),retry_delay_min:parseInt($('sRetryDelay').value||15),
    line_cooldown_sec:parseInt($('sCooldown').value||5),watchdog_timeout_min:parseInt($('sWatchdog').value||20),
    acd_wait_timeout_sec:parseInt($('sAcdT').value||60),window_start:$('sWinStart').value,window_end:$('sWinEnd').value,
    window_days:selectedDays(),consent_required:$('sConsent').checked,
    auto_quarantine_on_complaints:parseInt($('sQuar').value||3),sim_answer:$('sSimAns').value,
    crm:{driver:$('sCrm').value}}}));
  const msg=$('setMsg');
  if(j&&j.ok){msg.textContent='✓ Сохранено. Смена провайдера — после перезапуска сервера.';msg.classList.add('ok');msg.classList.remove('err');toast('Настройки сохранены');}
  else{msg.textContent='Ошибка сохранения';msg.classList.add('err');msg.classList.remove('ok');}
  setTimeout(()=>{msg.textContent='';},5000);
}
async function loadRawCfg(){
  const j=await safe(()=>api('/settings/raw'));
  if(j&&j.provider_config)$('sRaw').value=JSON.stringify(j.provider_config,null,2);
  else toast('Только для администратора','warn');
}
async function saveRawCfg(){
  try{
    const cfg=JSON.parse($('sRaw').value||'{}');
    const j=await safe(()=>api('/settings/raw',{body:{provider_config:cfg}}));
    const m=$('rawMsg');
    if(j&&j.ok){m.textContent='✓ Сохранено';m.classList.add('ok');m.classList.remove('err');toast('Конфигурация сохранена');}
    else{m.textContent='Ошибка';m.classList.add('err');}
  }catch(e){toast('JSON невалиден: '+e.message,'err');}
}
async function changePass(){
  const v=$('newPass').value;
  if(v.length<6)return toast('Минимум 6 символов','warn');
  const j=await safe(()=>api('/settings/save',{body:{new_password:v}}));
  if(j&&j.ok){toast('Пароль изменён');$('newPass').value='';}
  else toast('Ошибка смены пароля','err');
}

/* ================= timeline звонка (forensics ВАТС) ================= */
async function showTimeline(id){
  const j=await safe(()=>api('/calls/'+id+'/timeline'));
  if(!j||!j.call)return toast('Карточка недоступна','err');
  const c=j.call,evs=j.events||[],acd=j.acd||[];
  modal(`<h2>${ico('phone')}Звонок #${c.id} — события ВАТС</h2>
    <dl class="kv" style="margin:0 0 8px">
      <dt>Направление</dt><dd>${esc(c.direction||'')}${c.external_call_id?` · callid <b>${esc(c.external_call_id)}</b>`:''}</dd>
      <dt>Контакт</dt><dd>${esc(c.contact_name||'')} · ${esc(c.contact_phone||'')}</dd>
      <dt>Статус</dt><dd>${badge(c.result||c.status)}${extStatus(c)}</dd>
      <dt>Время</dt><dd>${esc(c.started_at||'')} → ${esc(c.ended_at||'…')}${c.duration_sec?` · ${c.duration_sec} с`:''}</dd>
      ${c.recording_url?`<dt>Запись</dt><dd><a href="${esc(c.recording_url)}" target="_blank">слушать запись ВАТС</a> <span class="btn-row">${recLink(c)}</span></dd>`:''}
      ${c.provider_user?`<dt>Сотрудник ВАТС</dt><dd>${esc(c.provider_user)}</dd>`:''}
    </dl>
    <h3 style="margin:10px 0 4px;font-size:14px">События (${evs.length})</h3>
    <div class="tblwrap thin" style="max-height:260px"><table>
      <tr><th>Время</th><th>Тип</th><th>Статус обработки</th></tr>
      ${evs.map(e=>`<tr><td class="nowrap">${esc(e.received_at||'')}</td><td><b>${esc(e.event_type||'')}</b> <span class="dim">${esc(e.fingerprint||'').slice(0,12)}</span></td><td>${esc(e.status||'')}</td></tr>`).join('')||'<tr><td colspan="3" class="dim">Событий от ВАТС пока нет</td></tr>'}
    </table></div>
    ${acd.length?`<h3 style="margin:10px 0 4px;font-size:14px">ACD</h3><div class="dim">${acd.map(a=>`#${a.id}: ${esc(a.status)} (оператор ${a.operator_id||'—'})`).join(' · ')}</div>`:''}
    <div class="form-actions"><button class="btn x" onclick="closeModal()">Закрыть</button></div>`,true);
}

/* ================= МегаФон ВАТС: проверки и синки (админ) ================= */
function vatsShow(msg,out){
  const m=$('vatsMsg');m.textContent=msg||'';m.classList.remove('err');m.classList.add('ok');
  const o=$('vatsOut');o.classList.remove('hidden');
  o.textContent=typeof out==='string'?out:JSON.stringify(out,null,2);
}
function vatsErr(msg){
  const m=$('vatsMsg');m.textContent=msg;m.classList.add('err');m.classList.remove('ok');
}
async function vatsCheck(){
  const j=await safe(()=>api('/megafon/check',{body:{}}));
  if(!j)return vatsErr('Нет ответа сервера');
  if(j.ok)vatsShow('✓ Связь в порядке',j.report);
  else vatsErr('Ошибка: '+(j.error||j.detail||'?'));
}
async function vatsPool(dry){
  const j=await safe(()=>api('/megafon/pool-sync',{body:{dry_run:!!dry}}));
  if(!j)return vatsErr('Нет ответа сервера');
  if(j.ok)vatsShow(dry?'✓ План готов — сверьте с панелью ВАТС':'✓ Применено',j.report);
  else vatsErr('Ошибка: '+(j.error||'?'));
}
async function vatsSync(kind){
  const j=await safe(()=>api('/megafon/'+kind+'-sync',{body:{}}));
  if(!j)return vatsErr('Нет ответа сервера');
  if(j.ok)vatsShow('✓ Готово',j.report);
  else vatsErr('Ошибка: '+(j.error||'?'));
}
async function vatsDir(){
  const j=await safe(()=>api('/megafon/directory'));
  if(!j)return vatsErr('Нет ответа сервера');
  vatsShow('Сотрудников: '+((j.users||[]).length)+' · отделов: '+((j.groups||[]).length),j);
}

/* ================= модалки ================= */
function modal(html,wide){
  $('modalBox').className='modalbox'+(wide?' wide':'');
  $('modalBox').innerHTML=html;
  $('modal').classList.remove('hidden');
  $('modal').scrollTop=0;
}
function closeModal(){$('modal').classList.add('hidden');$('modalBox').innerHTML='';}

/* ================= палитра Ctrl+K ================= */
let palItems=[],palIdx=0;
async function openPalette(){
  if(!state.token)return;
  $('palette').classList.remove('hidden');
  $('palInput').value='';
  $('palInput').focus();
  if(!state.campaigns.length)await safe(loadCamps);
  if(!state.contacts.length)await safe(fetchContacts);
  palIdx=0;renderPal();
}
function palData(q){
  q=(q||'').toLowerCase();
  const items=[];
  NAV.forEach(([tab,name,icn])=>{if(!q||name.toLowerCase().includes(q))items.push({ic:icn,name,sub:'раздел',go:()=>openTab(tab)});});
  (state.campaigns||[]).slice(0,15).forEach(c=>{
    const nm='#'+c.id+' '+c.name;
    if(!q||nm.toLowerCase().includes(q))items.push({ic:'campaigns',name:nm,sub:statusPill(c.status).replace(/<[^>]+>/g,''),go:()=>{openTab('campaigns');showCamp(c.id);}});
  });
  (state.contacts||[]).slice(0,20).forEach(c=>{
    const nm=c.name+' · '+c.phone;
    if(!q||nm.toLowerCase().includes(q))items.push({ic:'users',name:nm,sub:c.grp||'контакт',go:()=>{openTab('contacts');$('contactSearch').value=c.phone;renderContacts();}});
  });
  return items;
}
function renderPal(){
  const q=$('palInput').value;
  palItems=palData(q).slice(0,40);
  if(!palItems.length){$('palList').innerHTML='<div class="empty" style="padding:22px">Ничего не найдено</div>';return;}
  $('palList').innerHTML=palItems.map((it,i)=>`<button class="palit ${i===palIdx?'sel':''}" data-i="${i}">${ico(it.ic)}<span>${esc(it.name)}</span><span class="sub">${it.sub}</span></button>`).join('');
  const selEl=$('palList').querySelector('.sel');
  if(selEl)selEl.scrollIntoView({block:'nearest'});
}
function closePalette(){$('palette').classList.add('hidden');}
function palGo(i){
  if(palItems[i]){
    const g=palItems[i].go;closePalette();setTimeout(()=>{try{g();}catch(e){}},10);
  }
}

/* ================= делегирование кликов по таблицам/кнопкам ================= */
const Actions={
  showcamp:el=>{openTab('campaigns');showCamp(parseInt(el.dataset.id));},
  cstart:el=>campAction(parseInt(el.dataset.id),'start'),
  cpause:el=>campAction(parseInt(el.dataset.id),'pause'),
  cstop:el=>campAction(parseInt(el.dataset.id),'stop'),
  cretry:el=>campAction(parseInt(el.dataset.id),'retry-exhausted'),
  editc:el=>editContact(parseInt(el.dataset.id)),
  cardc:el=>contactCard(parseInt(el.dataset.id)),
  delc:el=>delContact(parseInt(el.dataset.id)),
  compl:el=>complaint(el.dataset.phone),
  editn:el=>editNum(parseInt(el.dataset.id)),
  quar:el=>setQuar(parseInt(el.dataset.id),parseInt(el.dataset.on)),
  togglen:el=>toggleNum(parseInt(el.dataset.id),parseInt(el.dataset.on)),
  accept:el=>acceptAcd(parseInt(el.dataset.id),parseInt(el.dataset.call||0)),
  addc:el=>addContactsToCamp(parseInt(el.dataset.id)),
  clearc:el=>clearCamp(parseInt(el.dataset.id)),
  editc2:el=>{const c=state.campaigns.find(x=>x.id===parseInt(el.dataset.id));editCampaign(c);},
  uprec:el=>{const campId=parseInt($('campDetail').dataset.cid||'0');upRecording(parseInt(el.dataset.id),campId);},
  delbl:el=>delBlack(parseInt(el.dataset.id)),
  editt:el=>editTemplate(parseInt(el.dataset.id)),
  delt:el=>delTemplate(parseInt(el.dataset.id)),
  edituser:el=>editUser(parseInt(el.dataset.id)),
  deluser:el=>delUser(parseInt(el.dataset.id)),
};
function wire(root){
  if(!root)return;
  root.querySelectorAll('[data-a]').forEach(el=>{
    el.onclick=ev=>{
      ev.preventDefault();ev.stopPropagation();
      const fn=Actions[el.dataset.a];
      if(fn)fn(el,ev);
    };
  });
}
function bindGlobal(){
  document.body.addEventListener('click',ev=>{
    if(!isAdmin())return;
    // клик по строке контактов = выбор
    const tr=ev.target.closest&&ev.target.closest('#contactsTable tr[data-cid]');
    if(tr&&!ev.target.closest('button,a,input,label')){const ck=tr.querySelector('.ck');if(ck){ck.checked=!ck.checked;ck.dispatchEvent(new Event('change'));}}
  });
  document.body.addEventListener('change',ev=>{
    const t=ev.target;
    if(t.classList&&t.classList.contains('ck')){
      if(t.id==='ckAll'){document.querySelectorAll('#contactsTable tr[data-cid] .ck').forEach(x=>{x.checked=t.checked;x.closest('tr').classList.toggle('sel',t.checked);});}
      if(t.closest('tr[data-cid]')){t.closest('tr').classList.toggle('sel',t.checked);}
      syncBulk();
    }
  });
}

/* ================= SSE ================= */
let _es=null,_esTimer=null;
function setNet(on){
  $('netState').classList.toggle('on',on);
  $('netState').classList.toggle('off',!on);
  $('netTxt').textContent=on?'обновление в реальном времени':'нет связи — повтор…';
}
function connectSSE(){
  if(!state.token)return;
  if(_es)try{_es.close();}catch(e){}
  setNet(false);
  try{_es=new EventSource('/api/v2/events?token='+encodeURIComponent(state.token));}
  catch(e){setNet(false);return;}
  _es.onopen=()=>setNet(true);
  _es.onmessage=e=>{
    try{
      const d=JSON.parse(e.data);
      if(!d||!d.type)return;
      const t=d.type;
      if(['call','item','campaign','acd','agent'].includes(t)){
        if(t==='call'&&isActive('journal'))debRefresh('journal',900);
        if(t==='item'&&isActive('campaigns'))debRefresh('campaigns',900);
        if(t==='acd'){
          if(d.status==='queued'&&d.id)notifyNewAcd(d);
          if(isActive('acd'))debRefresh('acd',300);
        }
        if(isActive('dash'))debRefresh('dash');
      }
    }catch(err){}
  };
  _es.onerror=()=>{
    setNet(false);
    try{_es.close();}catch(e){}
    _es=null;
    clearTimeout(_esTimer);
    _esTimer=setTimeout(()=>{if(state.token)connectSSE();},4000);
  };
}

/* ================= события интерфейса ================= */
function bindUI(){
  $('jStatus').innerHTML=jResOpts.map(([v,l])=>`<option value="${v}">${esc(l)}</option>`).join('');
  $('gateBtn').onclick=()=>doLogin();
  $('password').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
  $('login').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
  $('logoutBtn').onclick=()=>{logout(false);};
  $('refreshBtn').onclick=()=>doRefresh();
  $('palBtn').onclick=()=>openPalette();
  $('notifBtn').onclick=()=>toggleNotif();
  $('repSeg').addEventListener('click',e=>{const b=e.target.closest('button');if(b)repPreset(parseInt(b.dataset.days));});
  ['rFrom','rTo'].forEach(id=>{$(id).addEventListener('change',()=>{if($('rFrom').value&&$('rTo').value)applyRepDates();});});
  $('palInput').addEventListener('input',()=>{palIdx=0;renderPal();});
  $('palInput').addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'){e.preventDefault();palIdx=Math.min(palIdx+1,palItems.length-1);renderPal();}
    else if(e.key==='ArrowUp'){e.preventDefault();palIdx=Math.max(palIdx-1,0);renderPal();}
    else if(e.key==='Enter'){e.preventDefault();palGo(palIdx);}
  });
  $('palList').addEventListener('click',e=>{const b=e.target.closest('.palit');if(b)palGo(parseInt(b.dataset.i));});
  $('palette').addEventListener('click',e=>{if(e.target===$('palette'))closePalette();});
  $('modal').addEventListener('click',e=>{if(e.target===$('modal'))closeModal();});
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.code==='KeyK'){e.preventDefault();$('palette').classList.contains('hidden')?openPalette():closePalette();return;}
    if(e.key==='F5'){e.preventDefault();doRefresh();return;}
    if(e.key==='Escape'){
      if(!$('palette').classList.contains('hidden'))closePalette();
      else if(!$('modal').classList.contains('hidden'))closeModal();
    }
  });
  const seg=$('opQuick');
  seg.addEventListener('click',e=>{const b=e.target.closest('button');if(b)setMyStatus(b.dataset.st);});
  const burger=$('burger'),sideBack=$('sideBack');
  burger.onclick=()=>{$('side').classList.toggle('open');sideBack.classList.toggle('hidden');};
  sideBack.onclick=()=>{$('side').classList.remove('open');sideBack.classList.add('hidden');};
  window.addEventListener('resize',()=>{if(window.innerWidth>1020){$('side').classList.remove('open');sideBack.classList.add('hidden');}});
  bindGlobal();
}
function closeSide(){$('side').classList.remove('open');$('sideBack').classList.add('hidden');}

/* ================= старт ================= */
async function init(){
  bindUI();
  setNotifUI();
  if(state.token){
    const me=await safe(()=>api('/auth/me'));
    if(me&&me.ok){state.role=me.role;state.login=me.login;enterApp();return;}
    state.token='';localStorage.removeItem('ats_token');
  }
  showGate();
  // фоновое обновление активного раздела (8 с), только когда вкладка видима
  setInterval(()=>{
    if(!state.token||document.visibilityState==='hidden')return;
    if(isActive('dash'))debRefresh('dash',300);
    if(isActive('reports'))debRefresh('reports',1200);
    if(isActive('journal'))debRefresh('journal',2000);
  },8000);
}
init();