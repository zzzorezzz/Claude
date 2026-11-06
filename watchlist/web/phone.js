/* Watch Anything on your phone or tablet, over your own network (remote.py).

   On the phone: connecting it (pairing), and what shows when the PC can't be reached.
   On the PC: the "wants to connect - Allow / Deny" pop-up, and Settings → Use on your phone.
   Loaded before app.js; everything here is called once the app is running. */
'use strict';

// Opened from another device (an address of this PC on your network), not on the PC itself.
const REMOTE = !['127.0.0.1', 'localhost', '[::1]', '::1'].includes(location.hostname);
document.documentElement.classList.toggle('remote', REMOTE);

const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
const standalone = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;

function deviceGuess() {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'iPad';
  if (/iPhone/.test(ua)) return 'iPhone';
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? 'Android phone' : 'Android tablet';
  return 'Browser';
}

function remoteScreen(id, html) {
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.className = 'remote-screen';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    document.body.appendChild(el);
  }
  el.innerHTML = `<div class="rs-box"><img class="rs-logo" src="/logo.png" alt="" width="64" height="64">${html}</div>`;
  return el;
}

const homeScreenTip = () => (isIOS() && !standalone() ? `<p class="rs-tip">Tip: add it to your Home Screen first - press
  <b>Share</b> <span aria-hidden="true">⬆︎</span> then <b>Add to Home Screen</b> - and connect from there. The Home Screen app keeps its own connection.</p>` : '');

// ---- on the phone: links open here (the PC answers with them instead of opening them itself)

function openHere(urls) {
  const url = (urls || [])[0];
  if (!url || !/^https?:/i.test(url)) return;
  const w = window.open(url, '_blank', 'noopener');
  if (w) return;
  // (Safari let it through only from a tap: one more tap)
  const wrap = openModal(`<div class="modal-pad"><h2>Open it here</h2><p class="sub">${esc(new URL(url).host)}</p>
    <div class="btns"><a class="btn btn-red" href="${esc(url)}" target="_blank" rel="noopener">Open</a></div></div>`, 'open-here');
  wrap.addEventListener('click', (e) => { if (e.target.closest('a')) setTimeout(() => closeModal(wrap), 300); });
}

// ---- on the phone: the bottom bar (below 700px wide) - Home, Lists, Find, Party, Settings

const LIST_ROUTES = ['tv', 'anime', 'movie', 'collections', 'collection', 'watched'];
function phoneTabs() {
  const bar = document.getElementById('tabbar');
  if (!bar) return;
  const ico = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  bar.classList.add('tabbar-remote');
  bar.innerHTML = `<a href="#/" data-route="home">${ico('M12 3 3 10.5V21h6v-6h6v6h6V10.5z')}<span>Home</span></a>
    <button type="button" data-tab="lists">${ico('M3 5h2v2H3zm4 0h14v2H7zM3 11h2v2H3zm4 0h14v2H7zm-4 6h2v2H3zm4 0h14v2H7z')}<span>Lists</span></button>
    <a href="#/find" data-route="find">${ico('M10 3a7 7 0 1 0 4.2 12.6l5.1 5.1 1.4-1.4-5.1-5.1A7 7 0 0 0 10 3zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10z')}<span>Find</span></a>
    <button type="button" data-tab="party">${ico('M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm7.5-1a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM9 13c-3.3 0-7 1.6-7 4v3h14v-3c0-2.4-3.7-4-7-4zm7.5-1c-.5 0-1 0-1.6.1 1.3.9 2.1 2.2 2.1 3.9v3h5v-2.6c0-2.3-3.2-4.4-5.5-4.4z')}<span>Party</span></button>
    <button type="button" data-tab="settings">${ico('M19.1 12.9a7.3 7.3 0 0 0 0-1.9l2-1.6-1.9-3.3-2.4 1a7 7 0 0 0-1.6-.9L14.8 3h-3.8l-.4 2.5c-.6.2-1.1.5-1.6.9l-2.4-1-1.9 3.3 2 1.6a7.3 7.3 0 0 0 0 1.9l-2 1.6 1.9 3.3 2.4-1c.5.4 1 .7 1.6.9l.4 2.5h3.8l.4-2.5c.6-.2 1.1-.5 1.6-.9l2.4 1 1.9-3.3zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z')}<span>Settings</span></button>`;
  bar.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-tab]')?.dataset.tab;
    if (!tab) return;
    e.stopPropagation();                     // (not also the tap that closes menus)
    if (tab === 'party') document.getElementById('btn-party')?.click();
    else if (tab === 'settings') document.getElementById('btn-settings')?.click();
    else if (tab === 'lists') openListsSheet(e.target.closest('[data-tab]'));
  });
  const mark = () => {
    const name = (location.hash.replace(/^#\/?/, '').split('/')[0]) || 'home';
    bar.querySelector('[data-tab="lists"]').classList.toggle('active', LIST_ROUTES.includes(name));
  };
  window.addEventListener('hashchange', mark);
  mark();
}

function openListsSheet(anchor) {
  closeMenu();
  const menu = document.createElement('div');
  menu.className = 'menu ctx sheet-menu';
  menu.innerHTML = [['#/tv', 'TV Shows'], ['#/anime', 'Anime'], ['#/movie', 'Movies'], ['#/collections', 'Collections'], ['#/watched', 'Watched']]
    .map(([h, l]) => `<button data-go="${h}">${l}</button>`).join('');
  menu.addEventListener('click', (e) => {
    const go = e.target.closest('[data-go]')?.dataset.go;
    if (!go) return;
    closeMenu();
    location.hash = go;
  });
  placeMenu(menu, anchor);
}

// ---- on the phone: "Add to Home Screen", said once

function homeScreenOnce() {
  if (!REMOTE || !isIOS() || standalone()) return;
  try { if (localStorage.getItem('a2hs-tip')) return; localStorage.setItem('a2hs-tip', '1'); } catch { return; }
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad a2hs">
    <h2>Put it on your Home Screen</h2>
    <p class="sub">It then opens like an app, full screen, with its own icon.</p>
    <ol class="guide-steps"><li>Press <b>Share</b> <span aria-hidden="true">⬆︎</span> at the bottom of Safari.</li>
      <li>Choose <b>Add to Home Screen</b>, then <b>Add</b>.</li>
      <li>Open it from your Home Screen and press <b>Ask to connect</b> once more (the Home Screen app keeps its own connection).</li></ol>
    <div class="btns"><button class="btn btn-red" data-a2hs="ok">Got it</button></div></div>`, 'a2hs-modal');
  wrap.addEventListener('click', (e) => { if (e.target.closest('[data-a2hs]')) closeModal(wrap); });
}

// ---- the app's own files kept on the phone (only where browsers allow it: https)

if (REMOTE && 'serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
if (REMOTE) {
  document.addEventListener('DOMContentLoaded', () => {
    phoneTabs();
    setTimeout(() => { if (!document.getElementById('pair-screen') && !document.getElementById('away-screen')) homeScreenOnce(); }, 4000);
  });
}

// ---- on the phone: connecting it

function showPairing() {
  if (!REMOTE || document.getElementById('pair-screen')) return;
  document.getElementById('away-screen')?.remove();
  const el = remoteScreen('pair-screen', `<h1>Connect to Watch Anything</h1>
    <p>This ${esc(deviceGuess())} uses Watch Anything on your PC. Ask to connect, then press <b>Allow</b> on the PC.</p>
    ${homeScreenTip()}
    <label class="field"><span>Name this device</span><input class="input" id="pair-name" maxlength="40" value="${esc(deviceGuess() === 'Browser' ? 'My browser' : `My ${deviceGuess()}`)}"></label>
    <button class="btn btn-red rs-main" id="pair-go" type="button">Ask to connect</button>
    <div id="pair-out" aria-live="polite"></div>`);
  const out = el.querySelector('#pair-out');
  el.querySelector('#pair-go').addEventListener('click', async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    let r;
    try {
      r = await api('POST', '/api/pair/request', { name: el.querySelector('#pair-name').value });
    } catch (err) {
      out.innerHTML = `<p class="warn">${esc(err.message)}</p>`;
      b.disabled = false;
      return;
    }
    b.hidden = true;
    out.innerHTML = `<p>On your PC, check the code is the same, then press <b>Allow</b>:</p>
      <div class="pair-code" aria-label="Code ${r.code.split('').join(' ')}">${r.code.slice(0, 3)}&thinsp;${r.code.slice(3)}</div>
      <p class="muted small"><span class="spin-dot"></span> Waiting for the PC…</p>`;
    const until = Date.now() + 125000;
    const look = async () => {
      let s = { state: 'waiting' };
      try { s = await api('GET', `/api/pair/status?id=${encodeURIComponent(r.id)}`); } catch { /* the PC is busy: look again */ }
      if (s.state === 'allowed') {
        out.innerHTML = '<p class="ok">Connected ✓</p>';
        setTimeout(() => location.reload(), 600);
      } else if (s.state === 'waiting' && Date.now() < until) {
        setTimeout(look, 1500);
      } else {
        out.innerHTML = `<p class="warn">${s.state === 'denied' ? 'The PC said no.' : 'Nobody pressed Allow in time.'}</p>`;
        b.hidden = false;
        b.disabled = false;
        b.textContent = 'Ask again';
      }
    };
    look();
  });
}

// ---- on the phone: the PC isn't there (asleep, the app closed, another network)

let awayTimer = 0;
function showUnreachable() {
  if (!REMOTE || document.getElementById('away-screen') || document.getElementById('pair-screen')) return;
  const el = remoteScreen('away-screen', `<h1>Your PC isn't reachable</h1>
    <p>Watch Anything runs on your PC, and this ${esc(deviceGuess())} can't reach it right now.</p>
    <ul class="rs-list"><li>Is the PC on and awake, with Watch Anything open?</li>
      <li>Is this ${esc(deviceGuess())} on the same Wi-Fi as the PC (or is Tailscale on, on both)?</li>
      <li>Is <b>Let my phone use this app</b> still on, in the PC's Settings?</li></ul>
    <button class="btn btn-red rs-main" id="away-retry" type="button">Retry</button>
    <p class="muted small" id="away-note">Trying again by itself every 10 seconds.</p>`);
  const retry = async () => {
    try {
      const res = await fetch('/api/ping', { cache: 'no-store' });
      if (res.ok) { clearInterval(awayTimer); el.remove(); location.reload(); return; }
    } catch { /* still not there */ }
    el.querySelector('#away-note').textContent = `Not yet (${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}) - trying again every 10 seconds.`;
  };
  el.querySelector('#away-retry').addEventListener('click', retry);
  clearInterval(awayTimer);
  awayTimer = setInterval(retry, 10000);
}

// ---- on the PC: a phone asking to connect

const pairShown = new Map();             // request id -> its pop-up
async function pairPoll() {
  if (REMOTE || typeof S === 'undefined' || !S.status?.phone_access || onPhone()) return;
  let v;
  try { v = await api('GET', '/api/version'); } catch { return; }
  const waiting = new Set((v.pair || []).map((p) => p.id));
  for (const [id, wrap] of pairShown) {
    if (!waiting.has(id) && wrap?.isConnected) closeModal(wrap);       // (answered, asked again, or too late)
  }
  for (const p of v.pair || []) if (!pairShown.has(p.id)) showPairAsk(p);
}
setInterval(pairPoll, 3000);

function showPairAsk(p) {
  const wrap = openModal(`<div class="modal-pad pair-ask">
    <h2>📱 ${esc(p.name)} wants to connect</h2>
    <p>Code</p><div class="pair-code">${esc(p.code.slice(0, 3))}&thinsp;${esc(p.code.slice(3))}</div>
    <p class="muted small">The same code shows on the device. Allow it only if it's yours and the codes match. (From ${esc(p.ip)})</p>
    <div class="btns"><button class="btn btn-red" data-pa="allow">Allow</button><button class="btn btn-ghost" data-pa="deny">Deny</button></div></div>`, 'pair-modal');
  pairShown.set(p.id, wrap);
  sfx?.('add');
  const timer = setTimeout(() => wrap.isConnected && closeModal(wrap), 120000);
  wrap.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-pa]')?.dataset.pa;
    if (!a) return;
    clearTimeout(timer);
    closeModal(wrap);
    try {
      await api('POST', '/api/pair/answer', { id: p.id, allow: a === 'allow' });
      toast(a === 'allow' ? `${p.name} is connected` : `${p.name} wasn't let in`);
    } catch (err) { fail(err); }
  });
}

// ---- Settings → Use on your phone

let qrLoading = null;
function loadQr() {
  if (window.qrcode) return Promise.resolve();
  qrLoading = qrLoading || new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/vendor/qrcode-1.4.4.min.js';
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return qrLoading;
}

function qrSvg(text) {
  if (!window.qrcode) return '';
  const q = window.qrcode(0, 'M');
  q.addData(text);
  q.make();
  return q.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
}

const seenText = (t) => (t ? ago(t) : 'never');

async function drawPhoneCard(wrap) {
  const box = $('#phone-card', wrap);
  if (!box) return;
  if (REMOTE) {
    let me;
    try { me = (await api('GET', '/api/phone/me')).device; } catch (e) { box.innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
    box.innerHTML = `<p class="muted small">This ${esc(deviceGuess())} uses Watch Anything on your PC.</p>
      <label class="field"><span>This device's name</span><input class="input" id="me-name" maxlength="40" value="${esc(me.name)}"></label>
      <div class="field"><span>Play on</span><div class="seg" role="group">
        <button type="button" class="${me.play_on !== 'pc' ? 'on' : ''}" data-me="phone">This ${esc(deviceGuess())}</button>
        <button type="button" class="${me.play_on === 'pc' ? 'on' : ''}" data-me="pc">The PC's screen</button></div></div>`;
    box.onchange = async (e) => {
      if (e.target.id !== 'me-name') return;
      try { await api('PUT', `/api/phone/devices/${me.id}`, { name: e.target.value }); toast('Renamed'); } catch (err) { fail(err); }
    };
    box.onclick = async (e) => {
      const where = e.target.closest('[data-me]')?.dataset.me;
      if (!where) return;
      try {
        me = (await api('PUT', `/api/phone/devices/${me.id}`, { play_on: where })).device;
        box.querySelectorAll('[data-me]').forEach((b) => b.classList.toggle('on', b.dataset.me === where));
      } catch (err) { fail(err); }
    };
    return;
  }
  let st;
  try { st = await api('GET', '/api/phone'); } catch (e) { box.innerHTML = `<p class="warn">${esc(e.message)}</p>`; return; }
  if (st.on) await loadQr().catch(() => {});
  const mac = S.status?.platform === 'darwin';
  const shown = st.addresses.filter((a) => a.kind !== 'virtual');
  const ts = st.tailscale || {};
  box.innerHTML = `<label class="check-row"><input type="checkbox" id="ph-on" ${st.on ? 'checked' : ''}> <b>Let my phone use this app</b></label>
    <p class="muted small">Your iPhone, iPad or any phone or tablet with a browser, on your home Wi-Fi (or anywhere, with Tailscale).
      Each one asks to connect first and you allow it here. Off, the app is only on this ${mac ? 'Mac' : 'PC'}.</p>
    ${!st.on ? '' : `
    ${st.error ? `<p class="warn small">${esc(st.error)}</p>` : ''}
    <div class="ph-addrs">${shown.map((a) => `<div class="ph-addr">
        <div class="ph-qr" aria-hidden="true">${qrSvg(a.url)}</div>
        <div class="ph-what"><b>${esc(a.label)}</b><code>${esc(a.url)}</code>
          <button class="btn btn-ghost btn-sm" type="button" data-ph="copy" data-url="${esc(a.url)}">Copy</button></div></div>`).join('')
      || '<p class="muted small">Finding this computer\'s addresses…</p>'}
      ${(st.names || []).map((u) => `<div class="ph-addr"><div class="ph-qr" aria-hidden="true">${qrSvg(u)}</div>
        <div class="ph-what"><b>Tailscale name</b><code>${esc(u)}</code></div></div>`).join('')}</div>
    <p class="small">On your phone, scan one of these with the camera (or type it into Safari). It asks to connect: press <b>Allow</b> here.</p>
    ${shown.some((a) => a.reachable === false) ? `<div class="warn small ph-blocked"><b>Your ${mac ? 'Mac\'s' : 'PC\'s'} firewall is probably blocking it</b>
      (the app can't reach itself on ${esc(shown.filter((a) => a.reachable === false).map((a) => a.ip).join(', '))}).
      ${mac ? 'Open System Settings → Network → Firewall → Options, find Watch Anything and choose <b>Allow incoming connections</b>.'
        : 'Open <b>Windows Security → Firewall &amp; network protection → Allow an app through firewall → Change settings</b>, find Watch Anything and tick <b>Private</b>, then OK.'}</div>`
    : `<p class="muted small">🛡 ${mac ? 'macOS may ask whether Watch Anything may accept incoming connections - choose Allow.'
      : 'Windows may ask whether Watch Anything may use your network - allow it on Private networks.'}</p>`}
    <div class="ph-row"><label class="field"><span>Port</span><div class="input-row"><input class="input q-num" id="ph-port" type="number" min="1024" max="65535" value="${st.port}">
      <button class="btn btn-ghost btn-sm" type="button" data-ph="port">Change</button></div></label></div>
    <label class="check-row"><input type="checkbox" id="ph-awake" ${st.keep_awake ? 'checked' : ''}> Keep the ${mac ? 'Mac' : 'PC'} awake while my phone uses it
      <span class="muted small">(until 10 minutes after it last did something)</span></label>
    <div class="ph-ts">${ts.running
      ? `<p class="small"><span class="ok">✓</span> Tailscale is running: your phone can use the Tailscale address anywhere it has internet (Tailscale on, on the phone too).</p>`
      : `<p class="small">Away from home? <button class="btn btn-ghost btn-sm" type="button" data-ph="tailscale">Use it anywhere with Tailscale…</button></p>`}</div>
    <h5>Connected devices</h5>
    <div class="ph-devs">${st.devices.length ? st.devices.map((d) => `<div class="ph-dev" data-id="${esc(d.id)}">
        <input class="input" value="${esc(d.name)}" maxlength="40" aria-label="Device name" data-ph="name">
        <span class="muted small">last here ${esc(seenText(d.seen))}${d.ip ? ` · ${esc(d.ip)}` : ''} · plays on ${d.play_on === 'pc' ? 'the PC' : 'itself'}</span>
        <button class="btn btn-ghost btn-sm" type="button" data-ph="remove">Remove</button></div>`).join('')
      : '<p class="muted small">None yet.</p>'}</div>
    ${st.devices.length ? '<button class="btn btn-ghost btn-sm" type="button" data-ph="remove-all">Remove all devices</button>' : ''}`}`;
  const save = async (patch) => {
    try { await api('PUT', '/api/settings', patch); } catch (err) { fail(err); }
    S.status.phone_access = 'phone_access' in patch ? patch.phone_access : S.status.phone_access;
    setTimeout(() => drawPhoneCard(wrap), 'phone_access' in patch || 'phone_port' in patch ? 1500 : 0);
  };
  box.onchange = async (e) => {
    if (e.target.id === 'ph-on') { e.stopPropagation(); await save({ phone_access: e.target.checked }); }
    else if (e.target.id === 'ph-awake') { e.stopPropagation(); await save({ phone_keep_awake: e.target.checked }); }
    else if (e.target.dataset.ph === 'name') {
      e.stopPropagation();
      try { await api('PUT', `/api/phone/devices/${e.target.closest('[data-id]').dataset.id}`, { name: e.target.value }); } catch (err) { fail(err); }
    }
  };
  box.onclick = async (e) => {
    const b = e.target.closest('[data-ph]');
    if (!b || b.dataset.ph === 'name') return;
    e.preventDefault();
    try {
      if (b.dataset.ph === 'copy') {
        try { await navigator.clipboard.writeText(b.dataset.url); b.textContent = 'Copied ✓'; } catch { prompt('Copy this address:', b.dataset.url); }
      } else if (b.dataset.ph === 'port') {
        await save({ phone_port: Number($('#ph-port', wrap).value) });
      } else if (b.dataset.ph === 'remove') {
        await api('DELETE', `/api/phone/devices/${b.closest('[data-id]').dataset.id}`);
        drawPhoneCard(wrap);
      } else if (b.dataset.ph === 'remove-all') {
        if (!confirm('Remove every connected device? Each one has to ask to connect again.')) return;
        await api('POST', '/api/phone/remove-all', {});
        drawPhoneCard(wrap);
      } else if (b.dataset.ph === 'tailscale') openTailscaleGuide(mac);
    } catch (err) { fail(err); }
  };
  // The addresses and devices change (Tailscale coming up, a phone connecting): kept current.
  clearTimeout(wrap.phoneTimer);
  if (st.on) wrap.phoneTimer = setTimeout(() => wrap.isConnected && !box.contains(document.activeElement) && drawPhoneCard(wrap), 15000);
}

function openTailscaleGuide(mac) {
  openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Use it anywhere with Tailscale</h2>
    <p class="sub">Tailscale is a free app that puts your devices on a private network of their own, wherever they are.
      Your phone then reaches this ${mac ? 'Mac' : 'PC'} directly and encrypted - nothing is opened to the internet.</p>
    <ol class="guide-steps">
      <li>On this ${mac ? 'Mac' : 'PC'}: install Tailscale from <b>tailscale.com/download</b> and sign in.</li>
      <li>On your iPhone or iPad: install <b>Tailscale</b> from the App Store and sign in with the same account.</li>
      <li>Turn Tailscale on, on both.</li>
      <li>Come back here: a <b>Tailscale</b> address (100.…, or a name ending in .ts.net) appears with its own code. Open it on your phone - at home or away.</li>
    </ol>
    <p class="muted small">The app sees Tailscale by itself once it's running (it looks every 30 seconds).</p>
    <div class="btns"><button class="btn btn-white" data-open="https://tailscale.com/download">Open tailscale.com/download</button></div></div>`, 'ts-modal')
    .addEventListener('click', async (e) => {
      const url = e.target.closest('[data-open]')?.dataset.open;
      if (url) { try { await api('POST', '/api/open', { url }); } catch (err) { fail(err); } }
    });
}
