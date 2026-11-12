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

// ---- on the phone: Back (the swipe from the left edge, a browser's back) closes what's open
// on top - a menu, the player's settings, the player, a window - one at a time, instead of
// leaving the app. Whatever's open has one entry in the browser's history.

const layerCount = () => (document.getElementById('menu-root')?.children.length || 0)
  + (document.getElementById('modal-root')?.children.length || 0)
  + (document.querySelector('#wp:not(.hidden)') ? 1 : 0)
  + (document.querySelector('#wp:not(.hidden) .wp-sheet:not(.hidden)') ? 1 : 0);
let backEntry = false, backSkip = 0, backTimer = 0;

function closeTopLayer() {
  if (document.getElementById('menu-root')?.children.length) { closeMenu(); return true; }
  const sheet = document.querySelector('#wp:not(.hidden) .wp-sheet:not(.hidden)');
  if (sheet) { sheet.classList.add('hidden'); return true; }
  if (document.querySelector('#wp:not(.hidden)') && typeof wpClose === 'function') { wpClose(); return true; }
  const top = document.getElementById('modal-root')?.lastElementChild;
  if (top) { closeModal(top); return true; }
  return false;
}

function layersChanged() {
  if (!REMOTE) return;
  clearTimeout(backTimer);
  if (layerCount() && !backEntry) {
    history.pushState({ waLayer: true }, '');
    backEntry = true;
  } else if (!layerCount() && backEntry) {
    // (all closed with their own buttons: the entry goes too, a moment later)
    backTimer = setTimeout(() => {
      if (!layerCount() && backEntry) { backEntry = false; backSkip += 1; history.back(); }
    }, 60);
  }
}

if (REMOTE) {
  window.addEventListener('popstate', () => {
    if (backSkip) { backSkip -= 1; return; }
    if (!backEntry) return;
    backEntry = false;
    closeTopLayer();
    if (layerCount()) { history.pushState({ waLayer: true }, ''); backEntry = true; }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const watch = new MutationObserver(() => layersChanged());
    for (const id of ['menu-root', 'modal-root']) {
      const el = document.getElementById(id);
      if (el) watch.observe(el, { childList: true });
    }
    // (the player and its settings sheet open and close by a class)
    const wp = () => document.getElementById('wp');
    const hook = setInterval(() => {
      if (!wp()) return;
      clearInterval(hook);
      watch.observe(wp(), { attributes: true, attributeFilter: ['class'], subtree: true });
    }, 500);
  });
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
    <button type="button" data-tab="saved">${ico('M12 3v10.6l3.3-3.3 1.4 1.4-5.7 5.7-5.7-5.7 1.4-1.4 3.3 3.3V3zM4 18h16v2H4z')}<span>Saved</span></button>
    <button type="button" data-tab="settings">${ico('M19.1 12.9a7.3 7.3 0 0 0 0-1.9l2-1.6-1.9-3.3-2.4 1a7 7 0 0 0-1.6-.9L14.8 3h-3.8l-.4 2.5c-.6.2-1.1.5-1.6.9l-2.4-1-1.9 3.3 2 1.6a7.3 7.3 0 0 0 0 1.9l-2 1.6 1.9 3.3 2.4-1c.5.4 1 .7 1.6.9l.4 2.5h3.8l.4-2.5c.6-.2 1.1-.5 1.6-.9l2.4 1 1.9-3.3zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z')}<span>Settings</span></button>`;
  // A tablet (no bar at the bottom): Saved in the top bar, by Watch together.
  const party = document.getElementById('btn-party');
  if (party && !document.getElementById('btn-saved')) {
    const b = document.createElement('button');
    b.id = 'btn-saved';
    b.className = 'icon-btn';
    b.type = 'button';
    b.title = `Saved on this ${deviceWord()}`;
    b.setAttribute('aria-label', b.title);
    b.innerHTML = ico('M12 3v10.6l3.3-3.3 1.4 1.4-5.7 5.7-5.7-5.7 1.4-1.4 3.3 3.3V3zM4 18h16v2H4z');
    b.addEventListener('click', (e) => { e.stopPropagation(); openSavedSheet(); });
    party.before(b);
  }
  bar.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-tab]')?.dataset.tab;
    if (!tab) return;
    e.stopPropagation();                     // (not also the tap that closes menus)
    if (tab === 'party') document.getElementById('btn-party')?.click();
    else if (tab === 'settings') document.getElementById('btn-settings')?.click();
    else if (tab === 'lists') openListsSheet(e.target.closest('[data-tab]'));
    else if (tab === 'saved') openSavedSheet();
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

// ---- Save to this iPad: a video kept on the phone or tablet itself and played from there (no
// streaming: no stalls, however the Wi-Fi is), taken off once it's been watched. The PC gets it
// ready (the file itself when the phone plays it as it is; else repackaged or converted) and the
// page keeps it in its own storage (IndexedDB), in pieces, fetched one after another - resumed
// where it stopped when the app is opened again. "Saved" lists them, with their sizes.

const SAVE_DB = 'wa-saved';
const SAVE_PIECE = 8 * 1024 * 1024;
const saveJobs = {};                         // id -> a save going on now (its fetching)

function deviceWord() {
  const ua = navigator.userAgent || '';
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return 'iPad';
  if (/iPhone/.test(ua)) return 'iPhone';
  return /Android|Mobile/.test(ua) ? 'phone' : 'device';
}

function saveDb() {
  if (saveDb.p) return saveDb.p;
  saveDb.p = new Promise((resolve, reject) => {
    const req = indexedDB.open(SAVE_DB, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('files')) db.createObjectStore('files', { keyPath: 'id' });
      if (!db.objectStoreNames.contains('pieces')) db.createObjectStore('pieces');
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => { saveDb.p = null; reject(req.error || new Error("this browser can't keep videos")); };
  });
  return saveDb.p;
}

async function saveDo(store, mode, fn) {
  const db = await saveDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, mode);
    let out;
    Promise.resolve(fn(tx.objectStore(store), (v) => { out = v; })).catch(reject);
    tx.oncomplete = () => resolve(out);
    tx.onerror = () => reject(tx.error || new Error('saving failed'));
    tx.onabort = () => reject(tx.error || new Error('there was no room left to save it'));
  });
}
const saveGet = (store, key) => saveDo(store, 'readonly', (os, done) => { const r = os.get(key); r.onsuccess = () => done(r.result); });
const savePut = (store, value, key) => saveDo(store, 'readwrite', (os) => { if (key === undefined) os.put(value); else os.put(value, key); });
const saveAllFiles = () => saveDo('files', 'readonly', (os, done) => { const r = os.getAll(); r.onsuccess = () => done(r.result || []); });

async function savedList() {
  try { return (await saveAllFiles()).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0)); } catch { return []; }
}

// The saved copy of an episode (or film), when it's all there.
async function savedFor(itemId, key) {
  if (!REMOTE || typeof indexedDB === 'undefined') return null;
  return (await savedList()).find((f) => f.item_id === itemId && f.key === key && f.state === 'done') || null;
}

// What the phone's player opens for a saved copy (its picture and sound from here, its subtitles too).
async function savedMedia(file) {
  const parts = [];
  for (let n = 0; n < file.pieces; n += 1) {
    const piece = await saveGet('pieces', `${file.id}:${n}`);
    if (!piece) throw new Error('part of the saved video is missing');
    parts.push(piece);
  }
  const url = URL.createObjectURL(new Blob(parts, { type: 'video/mp4' }));
  return { index: undefined, key: file.key, label: file.label, offset: 0, duration: file.duration || null, how: 'saved',
    type: 'mp4', src: url, savedId: file.id, audio: [],
    subs: (file.subs || []).map((s) => ({ label: s.label, lang: s.lang, absolute: true,
      url: URL.createObjectURL(new Blob([s.text], { type: 'text/vtt' })) })) };
}

async function savedRemove(id) {
  const file = await saveGet('files', id).catch(() => null);
  if (saveJobs[id]) saveJobs[id].stop = true;
  await saveDo('pieces', 'readwrite', (os) => { for (let n = 0; n < Math.max(file?.pieces || 0, file?.expected || 0) + 1; n += 1) os.delete(`${id}:${n}`); });
  await saveDo('files', 'readwrite', (os) => { os.delete(id); });
  api('POST', `/api/web/save/${id}/done`, {}).catch(() => {});
  savedRefresh();
}

// Start saving one of a title's files: {itemId, key, label, path} (an episode's ⋯), or {sid, index}
// (the file the phone's player has open).
async function saveToDevice({ itemId, key, label, path, title, sid, index }) {
  if (typeof indexedDB === 'undefined') { toast("This browser can't keep videos", 'error'); return; }
  try { navigator.storage?.persist?.(); } catch { /* fine: asked */ }
  if (itemId) {
    const mine = (await savedList()).find((f) => f.item_id === itemId && f.key === key);
    if (mine && mine.state === 'done') { toast(`${label || title} is already on this ${deviceWord()}`); return; }
  }
  let r;
  try {
    r = await api('POST', '/api/web/save', sid ? { sid, index, caps: wpCaps() } : { item_id: itemId, key, path, label, caps: wpCaps() });
  } catch (err) { toast(err.message, 'error'); return; }
  const had = (await savedList()).find((f) => f.id === r.id || (f.item_id === r.item_id && f.key === r.key && f.state === 'done'));
  if (had && had.state === 'done') { toast(`${r.label || r.title} is already on this ${deviceWord()}`); return; }
  if (!had) {
    await savePut('files', { id: r.id, item_id: r.item_id, key: r.key, label: r.label, title: r.title, state: 'preparing', progress: 0,
      size: 0, got: 0, pieces: 0, savedAt: Date.now() });
  }
  toast(`Saving ${r.label || r.title} to this ${deviceWord()} - see Saved`);
  savePump(r.id);
}

// One save, step by step: the PC getting it ready, then its pieces fetched and kept.
async function savePump(id) {
  if (saveJobs[id]) return;
  const job = saveJobs[id] = { stop: false };
  const update = async (patch) => {
    const f = await saveGet('files', id);
    if (!f) { job.stop = true; return null; }
    Object.assign(f, patch);
    await savePut('files', f);
    savedRefresh();
    return f;
  };
  try {
    let state;
    for (;;) {
      if (job.stop) return;
      try { state = await api('GET', `/api/web/save/${id}`); } catch (err) {
        if (err.code === 'gone') { await update({ state: 'failed', error: 'the computer let go of it (it was a while ago) - save it again' }); return; }
        await new Promise((res) => setTimeout(res, 4000));
        continue;
      }
      if (state.state === 'failed') { await update({ state: 'failed', error: state.error }); return; }
      if (state.state === 'ready') break;
      await update({ state: 'preparing', progress: state.progress || 0, how: state.how });
      await new Promise((res) => setTimeout(res, 1500));
    }
    let f = await update({ state: 'saving', size: state.size, duration: state.duration, subs: state.subs || [], audio: state.audio || [],
      expected: Math.ceil(state.size / SAVE_PIECE), how: state.how, poster: state.poster || '' });
    if (!f) return;
    for (let n = f.pieces; n * SAVE_PIECE < state.size; n += 1) {
      if (job.stop) return;
      const start = n * SAVE_PIECE, end = Math.min(state.size, start + SAVE_PIECE) - 1;
      let piece = null;
      for (let tries = 0; tries < 5 && !piece; tries += 1) {
        try {
          const res = await fetch(state.url, { headers: { Range: `bytes=${start}-${end}` }, cache: 'no-store' });
          if (res.status !== 206 && res.status !== 200) throw new Error(`the computer answered ${res.status}`);
          piece = await res.blob();
          if (piece.size !== end - start + 1) { piece = null; throw new Error('a piece came short'); }
        } catch (err) {
          if (tries === 4) throw err;
          await new Promise((res) => setTimeout(res, 2000 * (tries + 1)));
        }
      }
      await savePut('pieces', piece, `${id}:${n}`);
      f = await update({ pieces: n + 1, got: end + 1 });
      if (!f) return;
    }
    await update({ state: 'done', got: state.size, savedAt: Date.now() });
    api('POST', `/api/web/save/${id}/done`, {}).catch(() => {});
    toast(`${f.label || f.title} is on this ${deviceWord()} - it plays from here now`);
  } catch (err) {
    await update({ state: 'paused', error: `${err.message || err} - it carries on when you open Saved` }).catch(() => {});
  } finally {
    delete saveJobs[id];
  }
}

// Saves left half-way (the app was closed): carried on.
async function saveResume() {
  for (const f of await savedList()) {
    if (['preparing', 'saving', 'paused'].includes(f.state)) savePump(f.id);
  }
}

const fmtSize = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);

// "Saved": everything kept on this device, how big, what it's doing; play or delete each.
async function openSavedSheet() {
  closeMenu();
  saveResume();
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad saved-modal">
    <h2>Saved on this ${deviceWord()}</h2>
    <p class="sub">They play from here, without streaming - and are taken off by themselves once you've watched them.
      Save more from an episode's ⋯, or from the player's menu.</p>
    <div id="saved-list"></div><p class="muted small" id="saved-space"></p>
    <div class="btns"><button class="btn btn-ghost" data-saved="all">Delete all</button></div></div>`, 'saved-sheet');
  wrap.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-saved]');
    if (!b) return;
    const what = b.dataset.saved, id = b.dataset.id;
    if (what === 'delete') { await savedRemove(id); toast('Deleted from this ' + deviceWord()); }
    else if (what === 'retry') { const f = await saveGet('files', id); if (f) { await savePut('files', { ...f, state: 'saving', error: '' }); savePump(id); } }
    else if (what === 'play') {
      const f = await saveGet('files', id);
      const item = typeof findItem === 'function' ? findItem(f.item_id) : null;
      if (!item) { toast("That title isn't in your list any more", 'error'); return; }
      closeModal(wrap);
      // (played as its file: the phone's player finds it saved here and plays it from here)
      openWatch(item, f.key && f.key.includes(':') ? { season: +f.key.split(':')[0], episode: +f.key.split(':')[1] } : undefined, { how: 'file' });
    } else if (what === 'all') {
      const all = await savedList();
      if (all.length && confirm(`Delete all ${all.length} saved video${all.length === 1 ? '' : 's'} from this ${deviceWord()}?`)) {
        for (const f of all) await savedRemove(f.id);
        toast('Everything saved here is deleted');
      }
    }
  });
  savedRefresh();
}

let savedTimer = 0;
async function savedRefresh() {
  const box = document.getElementById('saved-list');
  clearTimeout(savedTimer);
  if (!box) return;
  const files = await savedList();
  const word = { preparing: 'Getting it ready on the computer', saving: 'Saving', paused: 'Stopped', failed: "Couldn't save it", done: 'Saved' };
  box.innerHTML = files.length ? files.map((f) => {
    const pct = f.state === 'preparing' ? f.progress : f.size ? (f.got || 0) / f.size : 0;
    return `<div class="saved-row" data-state="${f.state}">
      <div class="saved-what"><b>${esc(f.title || '')}</b>${f.label && f.label !== f.title ? ` <span class="muted">${esc(f.label)}</span>` : ''}
        <div class="small ${f.state === 'failed' || f.state === 'paused' ? 'bad' : 'muted'}">${word[f.state] || f.state}${f.state === 'done' ? '' : ` ${Math.round(pct * 100)}%`}
          ${f.size ? ` · ${fmtSize(f.size)}` : ''}${f.how && f.how !== 'as it is' ? ` · ${esc(f.how)}` : ''}${f.error ? ` · ${esc(f.error)}` : ''}</div>
        ${f.state !== 'done' && f.state !== 'failed' ? `<div class="saved-bar"><i style="width:${Math.round(pct * 100)}%"></i></div>` : ''}</div>
      <div class="saved-btns">${f.state === 'done' ? `<button class="btn btn-white btn-sm" data-saved="play" data-id="${f.id}">▶ Play</button>` : ''}
        ${f.state === 'paused' ? `<button class="btn btn-ghost btn-sm" data-saved="retry" data-id="${f.id}">Carry on</button>` : ''}
        <button class="btn btn-ghost btn-sm" data-saved="delete" data-id="${f.id}" aria-label="Delete">🗑</button></div></div>`;
  }).join('') : `<p class="muted">Nothing saved on this ${deviceWord()}.</p>`;
  const used = files.reduce((n, f) => n + (f.got || 0), 0);
  let free = '';
  try { const est = await navigator.storage.estimate(); if (est.quota) free = ` · about ${fmtSize(Math.max(0, est.quota - est.usage))} more can be kept`; } catch { /* fine */ }
  const space = document.getElementById('saved-space');
  if (space) space.textContent = `${fmtSize(used || 0)} used${free}`;
  if (files.some((f) => f.state === 'preparing' || f.state === 'saving')) savedTimer = setTimeout(savedRefresh, 1000);
}

if (REMOTE && typeof window !== 'undefined') window.addEventListener('load', () => setTimeout(saveResume, 3000));

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

// ---- the phone in a watch party: what the host plays opens here (in the phone's own player)

setInterval(async () => {
  if (!REMOTE || typeof S === 'undefined' || !S.status?.party || (typeof WP !== 'undefined' && WP.s)) return;
  try {
    const r = await api('GET', '/api/web/current');
    if (r.launch && !WP.s) openWebPlayer(r.launch);
  } catch { /* not now */ }
}, 2500);

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
        <button type="button" class="${me.play_on === 'pc' ? 'on' : ''}" data-me="pc">The PC's screen</button></div></div>
      <div class="field"><span>Your connection to the PC</span>
        <div class="btns"><button class="btn btn-ghost btn-sm" type="button" data-me="speed">Test my phone connection</button></div>
        <div id="me-speed" class="small" aria-live="polite"></div></div>`;
    box.onchange = async (e) => {
      if (e.target.id !== 'me-name') return;
      try { await api('PUT', `/api/phone/devices/${me.id}`, { name: e.target.value }); toast('Renamed'); } catch (err) { fail(err); }
    };
    box.onclick = async (e) => {
      const where = e.target.closest('[data-me]')?.dataset.me;
      if (!where) return;
      if (where === 'speed') { speedTest($('#me-speed', wrap)); return; }
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

// How good this phone's connection to the PC is: how fast an answer comes (5 tries), and how
// much a few seconds bring (a download from the PC) - and the phone quality that fits it.
async function speedTest(out) {
  out.innerHTML = '<span class="spin-dot"></span> Testing…';
  const times = [];
  for (let i = 0; i < 5; i += 1) {
    const t = performance.now();
    try { await fetch('/api/ping', { cache: 'no-store' }); } catch { out.innerHTML = '<span class="bad">The PC didn\'t answer.</span>'; return; }
    times.push(performance.now() - t);
  }
  const ping = Math.round(times.sort((a, b) => a - b)[2]);
  let bytes = 0, secs = 0;
  for (const mb of [4, 16]) {
    const t = performance.now();
    try {
      const r = await fetch(`/api/phone/speed?mb=${mb}`, { cache: 'no-store' });
      bytes += (await r.arrayBuffer()).byteLength;
    } catch { break; }
    secs += (performance.now() - t) / 1000;
    if (secs > 2) break;
  }
  const mbps = secs ? (bytes * 8) / 1e6 / secs : 0;
  const fits = mbps >= 25 ? ['original', 'Original quality'] : mbps >= 10 ? ['1080', '1080p'] : mbps >= 5 ? ['720', '720p'] : ['480', '480p'];
  out.innerHTML = `<b>${mbps >= 100 ? Math.round(mbps) : mbps.toFixed(1)} Mbit/s</b>, answers in ${ping} ms - good for <b>${fits[1]}</b>.
    ${mbps < 3 ? '<span class="bad">Slow: is the phone far from the Wi-Fi, or on mobile data through Tailscale?</span>' : ''}
    <button class="btn btn-ghost btn-sm" type="button" data-q-set="${fits[0]}">Use ${fits[1]} for the phone</button>`;
  out.onclick = async (e) => {
    const q = e.target.closest('[data-q-set]')?.dataset.qSet;
    if (!q) return;
    try { await api('PUT', '/api/settings', { phone_quality: q }); e.target.textContent = 'Done ✓'; } catch (err) { fail(err); }
  };
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
