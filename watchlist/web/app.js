'use strict';

/* ================================================================ basics */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const TYPES = ['tv', 'anime', 'movie'];
const TYPE_PLURAL = { tv: 'TV Shows', anime: 'Anime', movie: 'Movies' };
const TYPE_ONE = { tv: 'TV show', anime: 'anime', movie: 'movie' };
const TYPE_KIND = { tv: 'SERIES', anime: 'ANIME', movie: 'FILM' };
const ROW_SIZE = 6;

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M6 4l15 8-15 8z"/></svg>',
  info: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z"/></svg>',
  more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M9 3h6l1 2h4v2H4V5h4zm-3 6h12l-1 12H7z"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.4-1.4z"/></svg>',
  pc: '<svg viewBox="0 0 24 24"><path d="M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-7v2h3v2H7v-2h3v-2H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm1 2v9h16V6z"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm6.9 6h-3a15.7 15.7 0 0 0-1.4-3.6A8 8 0 0 1 18.9 8zM12 4c.8 1.2 1.5 2.5 1.9 4h-3.8c.4-1.5 1.1-2.8 1.9-4zM4.3 14a8.2 8.2 0 0 1 0-4h3.4a16.5 16.5 0 0 0 0 4zm.8 2h3a15.7 15.7 0 0 0 1.4 3.6A8 8 0 0 1 5.1 16zm3-8h-3a8 8 0 0 1 4.4-3.6C8.9 5.5 8.4 6.7 8.1 8zM12 20c-.8-1.2-1.5-2.5-1.9-4h3.8c-.4 1.5-1.1 2.8-1.9 4zm2.3-6H9.7a14.7 14.7 0 0 1 0-4h4.6a14.7 14.7 0 0 1 0 4zm.2 5.6c.6-1.1 1.1-2.3 1.4-3.6h3a8 8 0 0 1-4.4 3.6zm1.8-5.6a16.5 16.5 0 0 0 0-4h3.4a8.2 8.2 0 0 1 0 4z"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4zm2 2v12h12V6zm3 2l6 4-6 4z"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 5h7l2 2h9v12H3z"/></svg>',
};

const S = {
  items: [], collections: [], status: {},
  sort: 'popular',     // popular | newest | oldest
  genre: '',
  query: '',
  featured: null,
  folderSel: {},       // per section: '' (all), 'none' (not in a folder) or a folder id
};

const sectionFolders = (type) => S.collections.filter((c) => c.type === type);
const globalCollections = () => S.collections.filter((c) => !c.type);

async function api(method, path, body) {
  const opts = { method, headers: { 'X-Watchlist': '1' } };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new Error('The Watchlist window lost its connection - is the app still running?');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
  return data;
}

function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), kind === 'error' ? 6000 : 3500);
}
const fail = (e) => toast(e.message || String(e), 'error');

const regionName = (code) => {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }).of(code) || code; } catch { return code; }
};
const fmtRating = (r) => (r == null ? '' : Number(r).toFixed(1));
const fmtRuntime = (m) => (!m ? '' : m >= 60 ? `${Math.floor(m / 60)}h ${m % 60 ? `${m % 60}m` : ''}`.trim() : `${m}m`);
const fmtMoney = (n) => (n >= 1e9 ? `$${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `$${Math.round(n / 1e6)}M` : `$${n.toLocaleString()}`);
const epLabel = (p) => `S${p.season} E${p.episode}`;
const isSeries = (item) => item.format === 'series';
const isMac = () => S.status.platform === 'darwin';
const pcName = () => (isMac() ? 'Mac' : 'PC');
const browserName = () => (/opera\s*gx/i.test(S.status.browser || '') ? 'Opera GX' : 'your browser');
const findItem = (id) => S.items.find((i) => i.id === id);

function upsert(item) {
  const i = S.items.findIndex((x) => x.id === item.id);
  if (i >= 0) S.items[i] = item; else S.items.push(item);
}

async function reload() {
  const data = await api('GET', '/api/state');
  S.items = data.items;
  S.collections = data.collections;
  S.status = data.status;
  S.version = data.version;
  render();
  refreshOpenTitle();
}

// With sync on, the other computer can change the list at any time: check
// every few seconds (and when the window comes back into focus).
async function checkForChanges() {
  if (document.hidden || S.checking) return;
  S.checking = true;
  try {
    const { version } = await api('GET', '/api/version');
    if (version !== S.version) await reload();
  } catch { /* the app is closing, or busy: try again next time */ } finally { S.checking = false; }
}
setInterval(checkForChanges, 6000);
window.addEventListener('focus', checkForChanges);

// Broken poster images fall back to the title text behind them.
document.addEventListener('error', (e) => {
  if (e.target.tagName === 'IMG') e.target.classList.add('broken');
}, true);

/* ======================================================= sort & filter */

function popularity(item) {
  // Movies: box office first. TV: TMDB popularity. Anime: MyAnimeList members.
  if (item.type === 'movie') return [item.revenue || 0, item.popularity || 0];
  return [item.popularity || 0, 0];
}
function byPopularity(a, b) {
  const pa = popularity(a); const pb = popularity(b);
  return (pb[0] - pa[0]) || (pb[1] - pa[1]) || a.title.localeCompare(b.title);
}
function sortItems(list) {
  const arr = [...list];
  if (S.sort === 'newest') arr.sort((a, b) => (b.year || 0) - (a.year || 0) || byPopularity(a, b));
  else if (S.sort === 'oldest') arr.sort((a, b) => (a.year || 9999) - (b.year || 9999) || byPopularity(a, b));
  else arr.sort(byPopularity);
  return arr;
}
const applyGenre = (list) => (S.genre ? list.filter((i) => i.genres.includes(S.genre)) : list);
const ofType = (type) => sortItems(applyGenre(S.items.filter((i) => i.type === type)));

function matchesQuery(item, q) {
  const hay = [item.title, item.original_title, ...(item.aliases || []), ...(item.genres || []),
    ...(item.cast || []), ...(item.studios || []), String(item.year || '')].join(' \u0001 ').toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
}

/* ============================================================ routing */

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length) return { name: 'home' };
  if (TYPES.includes(parts[0])) return { name: parts[0] };
  if (parts[0] === 'collections') return { name: 'collections', id: parts[1] };
  return { name: 'home' };
}

function render() {
  const r = route();
  $$('.nav-links a').forEach((a) => a.classList.toggle('active', a.dataset.route === r.name && !S.query));
  renderNavButtons();
  const view = $('#view');
  if (S.query.trim()) renderSearch(view);
  else if (r.name === 'home') renderHome(view);
  else if (TYPES.includes(r.name)) renderCategory(view, r.name);
  else if (r.id) renderCollection(view, r.id);
  else renderCollections(view);
}

window.addEventListener('hashchange', () => {
  if (S.query) { S.query = ''; $('#search').value = ''; }
  render();
  window.scrollTo(0, 0);
});
window.addEventListener('scroll', () => $('#nav').classList.toggle('solid', window.scrollY > 30), { passive: true });

/* ============================================================== nav */

function renderNavButtons() {
  const yearBtn = $('#btn-year');
  yearBtn.textContent = S.sort === 'newest' ? 'Year: newest first ↓' : S.sort === 'oldest' ? 'Year: oldest first ↑' : 'Sort by year';
  yearBtn.classList.toggle('on', S.sort !== 'popular');
  const genreBtn = $('#btn-genre');
  genreBtn.innerHTML = `${esc(S.genre || 'Genre')} <span class="caret">▾</span>`;
  genreBtn.classList.toggle('on', !!S.genre);
}

$('#btn-year').addEventListener('click', () => {
  S.sort = { popular: 'newest', newest: 'oldest', oldest: 'popular' }[S.sort];
  toast(S.sort === 'popular' ? 'Sorted by popularity' : `Sorted by year, ${S.sort} first`);
  render();
});

$('#btn-genre').addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = $('#genre-menu');
  if (!menu.classList.contains('hidden')) { menu.classList.add('hidden'); return; }
  const genres = [...new Set(S.items.flatMap((i) => i.genres || []))].sort();
  menu.innerHTML = `<button data-genre="" class="${S.genre ? '' : 'sel'}">All genres</button><span></span><hr>`
    + (genres.length ? genres.map((g) => `<button data-genre="${esc(g)}" class="${g === S.genre ? 'sel' : ''}">${esc(g)}</button>`).join('')
      : '<div class="menu-label">Add some titles first</div>');
  menu.classList.remove('hidden');
});
$('#genre-menu').addEventListener('click', (e) => {
  const b = e.target.closest('[data-genre]');
  if (!b) return;
  S.genre = b.dataset.genre;
  $('#genre-menu').classList.add('hidden');
  render();
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.dropdown')) $('#genre-menu').classList.add('hidden');
  if (!e.target.closest('.ctx')) closeMenu();
});

let searchTimer;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.query = e.target.value; render(); }, 150);
});
$('#search').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { e.target.value = ''; S.query = ''; render(); }
});
$('#btn-settings').addEventListener('click', () => openSettings());

/* ============================================================= cards */

function cardHTML(item) {
  const meta = [];
  if (item.rating) meta.push(`<span class="star">★</span> ${fmtRating(item.rating)}`);
  if (item.year) meta.push(item.year);
  if (isSeries(item)) meta.push(epLabel(item.progress));
  else if (item.runtime) meta.push(fmtRuntime(item.runtime));
  const badge = !item.local_folder ? ''
    : item.local_broken ? '<span class="badge-pc warn">FILE ISSUE</span>' : `<span class="badge-pc">ON ${pcName().toUpperCase()}</span>`;
  return `<div class="card" data-id="${esc(item.id)}" tabindex="0" draggable="true">
    <div class="poster-fallback">${esc(item.title)}</div>
    ${item.poster ? `<img class="poster" src="${esc(item.poster)}" alt="" loading="lazy">` : ''}
    ${badge}
    <div class="card-info">
      <div class="card-buttons">
        <button class="cb play" data-act="watch" title="Watch now">${ICON.play}</button>
        <button class="cb" data-act="info" title="More info">${ICON.info}</button>
        <button class="cb right" data-act="menu" title="More options">${ICON.more}</button>
      </div>
      <div class="card-title">${esc(item.title)}</div>
      <div class="card-meta">${meta.join(' · ')}</div>
    </div>
  </div>`;
}

const addTileHTML = (type, folder) => `<button class="tile tile-add" data-add="${type}" ${folder ? `data-col="${esc(folder.id)}"` : ''} title="Add ${TYPE_ONE[type]}">
  <span class="plus">+</span><span>Add ${TYPE_ONE[type]}</span></button>`;

function rowHTML({ title, link, items, addType, emptyText, tools = '' }) {
  const shown = items.slice(0, ROW_SIZE);
  const head = link ? `<a href="${link}">${esc(title)}<span class="explore">Show more ›</span></a>` : esc(title);
  return `<section class="row">
    <div class="row-head"><h2>${head}</h2>${tools}</div>
    <div class="row-track">
      ${addType ? addTileHTML(addType) : ''}
      ${shown.map(cardHTML).join('')}
      ${items.length && link ? `<a class="tile tile-more" href="${link}"><span class="arrow">›</span><b>Show more</b><span>${items.length} title${items.length === 1 ? '' : 's'}</span></a>` : ''}
      ${items.length ? '' : `<div class="row-empty">${esc(emptyText)}</div>`}
    </div>
  </section>`;
}

// One delegated handler for every card, tile and button in the main view.
$('#view').addEventListener('click', (e) => {
  const add = e.target.closest('[data-add]');
  if (add) { openAdd(add.dataset.add, '', add.dataset.col || ''); return; }
  const action = e.target.closest('[data-do]');
  if (action) { viewAction(action.dataset.do, action.dataset); return; }
  const card = e.target.closest('.card');
  if (!card) return;
  const item = findItem(card.dataset.id);
  if (!item) return;
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'watch') openWatch(item);
  else if (act === 'menu') { e.stopPropagation(); openItemMenu(item, e.target.closest('[data-act]')); }
  else openTitle(item.id);
});
$('#view').addEventListener('keydown', (e) => {
  const card = e.target.closest('.card');
  if (card && e.key === 'Enter') openTitle(card.dataset.id);
});
$('#view').addEventListener('contextmenu', (e) => {
  const card = e.target.closest('.card');
  if (!card) return;
  e.preventDefault();
  const item = findItem(card.dataset.id);
  if (item) openItemMenu(item, { x: e.clientX, y: e.clientY });
});

// Drag a poster onto a folder chip to move it into that folder.
$('#view').addEventListener('dragstart', (e) => {
  const card = e.target.closest('.card');
  if (!card) return;
  e.dataTransfer.setData('text/x-watchlist', card.dataset.id);
  e.dataTransfer.effectAllowed = 'move';
  document.body.classList.add('dragging');
});
$('#view').addEventListener('dragend', () => {
  document.body.classList.remove('dragging');
  $$('.drop-over').forEach((el) => el.classList.remove('drop-over'));
});
$('#view').addEventListener('dragover', (e) => {
  const target = e.target.closest('[data-drop]');
  if (!target || !e.dataTransfer.types.includes('text/x-watchlist')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  target.classList.add('drop-over');
});
$('#view').addEventListener('dragleave', (e) => {
  const target = e.target.closest('[data-drop]');
  if (target && !target.contains(e.relatedTarget)) target.classList.remove('drop-over');
});
$('#view').addEventListener('drop', (e) => {
  const target = e.target.closest('[data-drop]');
  if (!target) return;
  e.preventDefault();
  target.classList.remove('drop-over');
  const id = e.dataTransfer.getData('text/x-watchlist');
  if (id) moveToFolder(id, target.dataset.drop === 'none' ? '' : target.dataset.drop);
});

async function moveToFolder(itemId, folderId) {
  const item = findItem(itemId);
  if (!item) return;
  try {
    upsert(await api('PUT', `/api/items/${itemId}/folder`, { folder: folderId }));
    render(); refreshOpenTitle();
    const folder = S.collections.find((c) => c.id === folderId);
    toast(folder ? `Moved ${item.title} to ${folder.name}` : `Took ${item.title} out of its folder`);
  } catch (err) { fail(err); }
}

function viewAction(name, data) {
  if (name === 'settings') openSettings();
  else if (name === 'watch') { const it = findItem(data.id); if (it) openWatch(it); }
  else if (name === 'info') openTitle(data.id);
  else if (name === 'new-collection') newCollection();
  else if (name === 'new-folder') newCollection(data.type).then((f) => { if (f) { S.folderSel[data.type] = f.id; render(); } });
  else if (name === 'pick-folder') { S.folderSel[data.type] = data.id; render(); }
  else if (name === 'rename-collection') renameCollection(data.id);
  else if (name === 'delete-collection') deleteCollection(data.id);
  else if (name === 'search-online') openAdd(data.type, S.query.trim());
  else if (name === 'clear-genre') { S.genre = ''; render(); }
}

/* ============================================================== views */

function setupBanner() {
  const syncProblem = S.status.sync?.problem
    ? `<div class="setup-banner"><b>Sync:</b> ${esc(S.status.sync.problem)}<span class="spacer"></span>
       <button class="btn btn-sm btn-white" data-do="settings">Sync settings</button></div>` : '';
  return syncProblem + keysBanner();
}

function keysBanner() {
  const missing = [];
  if (!S.status.tmdb) missing.push('your TMDB token (movies & TV)');
  if (!S.status.mal) missing.push('your MyAnimeList Client ID (anime)');
  if (!missing.length) return '';
  return `<div class="setup-banner"><b>Finish setting up:</b> add ${missing.join(' and ')} in Settings.
    <span class="spacer"></span><button class="btn btn-sm btn-white" data-do="settings">Open Settings</button></div>`;
}

function filterNote() {
  return S.genre ? `<span class="chip">Genre: ${esc(S.genre)} <button data-do="clear-genre" title="Clear">✕</button></span>` : '';
}

function pickFeatured() {
  const current = S.featured && findItem(S.featured);
  if (current && (!S.genre || current.genres.includes(S.genre))) return current;
  const pool = applyGenre(S.items);
  const withArt = pool.filter((i) => i.backdrop);
  const top = sortItems(withArt.length ? withArt : pool).slice(0, 5);
  const pick = top[Math.floor(Math.random() * top.length)];
  S.featured = pick ? pick.id : null;
  return pick;
}

function heroHTML(item) {
  const meta = [];
  if (item.rating) meta.push(`<span class="star">★</span> ${fmtRating(item.rating)} ${esc(item.rating_source)}`);
  if (item.year) meta.push(item.year);
  if (isSeries(item) && item.seasons.length > 1) meta.push(`${item.seasons.length} Seasons`);
  else if (isSeries(item) && item.episodes_total) meta.push(`${item.episodes_total} Episodes`);
  else if (item.runtime) meta.push(fmtRuntime(item.runtime));
  if (item.genres.length) meta.push(esc(item.genres.slice(0, 3).join(' · ')));
  const img = item.backdrop
    ? `<img class="hero-img" src="${esc(item.backdrop)}" alt="">`
    : item.poster ? `<img class="hero-img poster-blur" src="${esc(item.poster)}" alt="">` : '';
  return `<section class="hero">${img}
    <div class="hero-content">
      <div class="hero-kind">${TYPE_KIND[item.type]}</div>
      <h1>${esc(item.title)}</h1>
      <div class="hero-meta">${meta.join('<span>·</span>')}</div>
      <p>${esc(item.overview)}</p>
      <div class="btns">
        <button class="btn btn-white" data-do="watch" data-id="${esc(item.id)}">${ICON.play} Watch now</button>
        <button class="btn btn-grey" data-do="info" data-id="${esc(item.id)}">${ICON.info} More info</button>
      </div>
    </div></section>`;
}

function renderHome(view) {
  if (!S.items.length) {
    view.innerHTML = `<section class="hero hero-empty"><div class="hero-content">
      <div class="hero-kind">WELCOME</div>
      <h1>Your watchlist is empty</h1>
      <p>Add the TV shows, anime and movies you plan to watch. Summaries, ratings and trailers are filled in for you.</p>
      <div class="btns">
        <button class="btn btn-white" data-add="tv">+ TV show</button>
        <button class="btn btn-white" data-add="anime">+ Anime</button>
        <button class="btn btn-white" data-add="movie">+ Movie</button>
      </div></div></section>${setupBanner()}`;
    return;
  }
  const featured = pickFeatured();
  const rows = TYPES.map((t) => rowHTML({
    title: TYPE_PLURAL[t], link: `#/${t}`, items: ofType(t), addType: t,
    emptyText: S.genre ? `No ${S.genre} ${TYPE_PLURAL[t].toLowerCase()} in your list.` : `Nothing here yet - add your first ${TYPE_ONE[t]}.`,
  })).join('');
  view.innerHTML = `${featured ? heroHTML(featured) : ''}
    <div class="home-rows ${featured ? '' : 'no-hero'}">${setupBanner()}
    ${S.genre ? `<div class="row">${filterNote()}</div>` : ''}${rows}</div>`;
}

function sortLabel() {
  return S.sort === 'newest' ? 'newest first' : S.sort === 'oldest' ? 'oldest first'
    : 'most popular first';
}

function renderCategory(view, type) {
  const all = ofType(type);
  const folders = sectionFolders(type);
  const folderIds = new Set(folders.map((f) => f.id));
  let sel = S.folderSel[type] || '';
  if (sel && sel !== 'none' && !folderIds.has(sel)) sel = S.folderSel[type] = '';
  if (sel === 'none' && !folders.length) sel = S.folderSel[type] = '';
  const unsorted = all.filter((i) => !i.collections.some((c) => folderIds.has(c)));
  const items = !sel ? all : sel === 'none' ? unsorted : all.filter((i) => i.collections.includes(sel));
  const current = folders.find((f) => f.id === sel);
  const what = type === 'movie' && S.sort === 'popular' ? 'highest grossing first' : sortLabel();
  const chip = (id, label, count, icon = '') => `<button class="folder-chip ${sel === id ? 'on' : ''}" data-do="pick-folder"
      data-type="${type}" data-id="${esc(id)}" ${id ? `data-drop="${esc(id)}"` : ''}>${icon}<span>${esc(label)}</span><small>${count}</small></button>`;
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>${TYPE_PLURAL[type]}</h1>
      <span class="count">${all.length} title${all.length === 1 ? '' : 's'} · ${what}</span>${filterNote()}
      <span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" data-do="new-folder" data-type="${type}">${ICON.folder} New folder</button>
      <button class="btn btn-red btn-sm" data-add="${type}" ${current ? `data-col="${esc(current.id)}"` : ''}>+ Add ${TYPE_ONE[type]}</button></div>
    <div class="folder-bar">
      ${chip('', 'All', all.length)}
      ${folders.map((f) => chip(f.id, f.name, all.filter((i) => i.collections.includes(f.id)).length, ICON.folder)).join('')}
      ${folders.length ? chip('none', 'Not in a folder', unsorted.length) : ''}
    </div>
    <p class="hint">${folders.length ? 'Drag a poster onto a folder to move it there, or use ⋯ → Move to folder.'
      : `Make folders to sort your ${TYPE_PLURAL[type].toLowerCase()} however you like.`}</p>
    ${current ? `<div class="folder-head"><h2>${ICON.folder} ${esc(current.name)}</h2>
      <button class="btn btn-ghost btn-sm" data-do="rename-collection" data-id="${esc(current.id)}">Rename</button>
      <button class="btn btn-ghost btn-sm" data-do="delete-collection" data-id="${esc(current.id)}">Delete folder</button></div>` : ''}
    <div class="grid">${sel === 'none' ? '' : addTileHTML(type, current)}${items.map(cardHTML).join('')}</div>
  </div>`;
}

function renderCollections(view) {
  const rows = globalCollections().map((c) => {
    const items = sortItems(applyGenre(S.items.filter((i) => i.collections.includes(c.id))));
    return rowHTML({
      title: c.name, link: `#/collections/${c.id}`, items,
      emptyText: 'Empty - use ⋯ on any title (or right-click it) to add it here.',
      tools: `<div class="row-tools"><button class="btn btn-ghost btn-sm" data-do="rename-collection" data-id="${esc(c.id)}">Rename</button>
              <button class="btn btn-ghost btn-sm" data-do="delete-collection" data-id="${esc(c.id)}">Delete</button></div>`,
    });
  }).join('');
  view.innerHTML = `<div class="page" style="padding-left:0;padding-right:0">
    <div class="page-head" style="padding:0 var(--side)"><h1>Collections</h1>${filterNote()}<span class="spacer"></span>
      <button class="btn btn-red btn-sm" data-do="new-collection">+ New collection</button></div>
    ${rows || `<div class="empty-state"><h2>No collections yet</h2>
      <p>Collections mix TV shows, anime and movies, like "Weekend binge" or "Watch with friends".<br>A title can be in as many as you like.
      For folders inside one section, use "New folder" on the TV Shows, Anime or Movies page.</p>
      <button class="btn btn-white" data-do="new-collection">+ Create your first collection</button></div>`}
  </div>`;
}

function renderCollection(view, id) {
  const col = S.collections.find((c) => c.id === id);
  if (!col) { location.hash = '#/collections'; return; }
  if (col.type) { S.folderSel[col.type] = col.id; location.hash = `#/${col.type}`; return; }
  const items = sortItems(applyGenre(S.items.filter((i) => i.collections.includes(id))));
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>${esc(col.name)}</h1><span class="count">${items.length} title${items.length === 1 ? '' : 's'}</span>${filterNote()}
      <span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" data-do="rename-collection" data-id="${esc(id)}">Rename</button>
      <button class="btn btn-ghost btn-sm" data-do="delete-collection" data-id="${esc(id)}">Delete</button></div>
    ${items.length ? `<div class="grid">${items.map(cardHTML).join('')}</div>`
      : '<div class="empty-state">This collection is empty. Use ⋯ on any title (or right-click it) to add it here.</div>'}
  </div>`;
}

function renderSearch(view) {
  const q = S.query.trim();
  const items = sortItems(applyGenre(S.items.filter((i) => matchesQuery(i, q))));
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>Results for "${esc(q)}"</h1><span class="count">${items.length} in your list</span>${filterNote()}</div>
    ${items.length ? `<div class="grid">${items.map(cardHTML).join('')}</div>` : '<p class="muted">Nothing in your list matches.</p>'}
    <div class="row-head" style="margin-top:40px"><h2>Add "${esc(q)}" to your list</h2></div>
    <div class="btns">${TYPES.map((t) => `<button class="btn btn-grey btn-sm" data-do="search-online" data-type="${t}">Search ${TYPE_PLURAL[t]}</button>`).join('')}</div>
  </div>`;
}

/* ============================================================== menus */

function closeMenu() { $('#menu-root').innerHTML = ''; }

function placeMenu(menu, anchor) {
  const r = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor.x, bottom: anchor.y, top: anchor.y };
  document.body.append(menu);
  const w = menu.offsetWidth; const h = menu.offsetHeight;
  menu.remove();
  let x = Math.min(r.left, window.innerWidth - w - 8);
  let y = r.bottom + 4;
  if (y + h > window.innerHeight - 8) y = Math.max(8, (r.top ?? y) - h - 4);
  menu.style.left = `${Math.max(8, x)}px`;
  menu.style.top = `${y}px`;
  $('#menu-root').append(menu);
}

function openItemMenu(item, anchor, { inTitle = false } = {}) {
  closeMenu();
  const menu = document.createElement('div');
  menu.className = 'menu ctx';
  const folders = sectionFolders(item.type);
  const inFolder = folders.find((f) => item.collections.includes(f.id));
  const folderBtns = folders.map((f) => `<button data-folder="${esc(f.id)}"><span class="check">${inFolder === f ? '●' : ''}</span>${esc(f.name)}</button>`).join('');
  const cols = globalCollections().map((c) => `<button data-col="${esc(c.id)}"><span class="check">${item.collections.includes(c.id) ? '✓' : ''}</span>${esc(c.name)}</button>`).join('');
  menu.innerHTML = `${inTitle ? '' : `<button data-m="watch">▶&nbsp; Watch now</button><button data-m="info">ⓘ&nbsp; More info</button><hr>`}
    <div class="menu-label">Move to folder (${esc(TYPE_PLURAL[item.type])})</div>${folderBtns}
    ${inFolder ? '<button data-folder=""><span class="check"></span>Not in a folder</button>' : ''}
    <button data-m="new-folder"><span class="check">+</span>New folder…</button><hr>
    <div class="menu-label">Collections</div>${cols}
    <button data-m="new-col"><span class="check">+</span>New collection…</button><hr>
    <button data-m="refresh">↻&nbsp; Refresh info</button>
    <button data-m="remove" class="danger">✕&nbsp; Remove from list</button>`;
  menu.addEventListener('click', async (e) => {
    e.stopPropagation();
    const colBtn = e.target.closest('[data-col]');
    const folderBtn = e.target.closest('[data-folder]');
    try {
      if (folderBtn) {
        closeMenu();
        await moveToFolder(item.id, folderBtn.dataset.folder);
        return;
      }
      if (colBtn) {
        const id = colBtn.dataset.col;
        const now = findItem(item.id);
        const next = now.collections.includes(id) ? now.collections.filter((c) => c !== id) : [...now.collections, id];
        const updated = await api('PUT', `/api/items/${item.id}/collections`, { collections: next });
        upsert(updated);
        colBtn.querySelector('.check').textContent = updated.collections.includes(id) ? '✓' : '';
        render(); refreshOpenTitle();
        return;
      }
      const m = e.target.closest('[data-m]')?.dataset.m;
      if (!m) return;
      closeMenu();
      if (m === 'watch') openWatch(findItem(item.id));
      else if (m === 'info') openTitle(item.id);
      else if (m === 'new-folder') {
        const folder = await newCollection(item.type);
        if (folder) await moveToFolder(item.id, folder.id);
      } else if (m === 'new-col') {
        const col = await newCollection();
        if (col) {
          const now = findItem(item.id);
          upsert(await api('PUT', `/api/items/${item.id}/collections`, { collections: [...now.collections, col.id] }));
          render(); refreshOpenTitle();
          toast(`Added to ${col.name}`);
        }
      } else if (m === 'refresh') {
        toast('Refreshing info…');
        upsert(await api('POST', `/api/items/${item.id}/refresh`));
        render(); refreshOpenTitle();
        toast('Info updated');
      } else if (m === 'remove') removeItem(item.id);
    } catch (err) { fail(err); }
  });
  placeMenu(menu, anchor);
}

async function removeItem(id) {
  const item = findItem(id);
  if (!item) return;
  const ok = await confirmModal(`Remove "${item.title}"?`, `It will be taken off your list. Files on your ${pcName()} are not touched.`, 'Remove');
  if (!ok) return;
  try {
    await api('DELETE', `/api/items/${id}`);
    S.items = S.items.filter((i) => i.id !== id);
    $$('#modal-root .overlay').forEach((o) => { if (o.dataset.itemId === id) closeModal(o); });
    render();
    toast(`Removed ${item.title}`);
  } catch (e) { fail(e); }
}

/* ============================================================= modals */

function openModal(html, cls = '') {
  closeMenu();
  const wrap = document.createElement('div');
  wrap.className = 'overlay';
  wrap.innerHTML = `<div class="modal ${cls}">${html}</div>`;
  wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) closeModal(wrap); });
  wrap.addEventListener('click', (e) => { if (e.target.closest('.modal-close')) closeModal(wrap); });
  $('#modal-root').append(wrap);
  document.body.classList.add('noscroll');
  return wrap;
}

function closeModal(wrap) {
  if (!wrap || !wrap.isConnected) return;
  wrap.remove();
  if (wrap.onClose) wrap.onClose();
  if (!$('#modal-root').children.length) document.body.classList.remove('noscroll');
}

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('#menu-root').children.length) { closeMenu(); return; }
  const top = $('#modal-root').lastElementChild;
  if (top && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) closeModal(top);
  else if (top && document.activeElement?.id !== 'search') document.activeElement.blur();
});

function promptModal({ title, label = '', value = '', placeholder = '', ok = 'Save' }) {
  return new Promise((resolve) => {
    const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><form class="modal-pad">
      <h2>${esc(title)}</h2>${label ? `<p class="sub">${esc(label)}</p>` : ''}
      <input class="input" name="v" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off">
      <div class="btns" style="margin-top:18px"><button class="btn btn-red" type="submit">${esc(ok)}</button>
      <button class="btn btn-ghost" type="button" data-cancel>Cancel</button></div></form>`);
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } closeModal(wrap); };
    wrap.onClose = () => { if (!done) { done = true; resolve(null); } };
    $('form', wrap).addEventListener('submit', (e) => { e.preventDefault(); finish($('input', wrap).value.trim() || null); });
    $('[data-cancel]', wrap).addEventListener('click', () => finish(null));
    setTimeout(() => $('input', wrap).select(), 30);
  });
}

function confirmModal(title, text, ok = 'OK') {
  return new Promise((resolve) => {
    const wrap = openModal(`<div class="modal-pad"><h2>${esc(title)}</h2><p class="sub">${esc(text)}</p>
      <div class="btns"><button class="btn btn-red" data-ok>${esc(ok)}</button><button class="btn btn-ghost" data-cancel>Cancel</button></div></div>`);
    let done = false;
    wrap.onClose = () => { if (!done) { done = true; resolve(false); } };
    $('[data-ok]', wrap).addEventListener('click', () => { done = true; resolve(true); closeModal(wrap); });
    $('[data-cancel]', wrap).addEventListener('click', () => closeModal(wrap));
    $('[data-ok]', wrap).focus();
  });
}

/* ======================================================== collections */

async function newCollection(type = null) {
  const name = await promptModal(type
    ? { title: `New folder in ${TYPE_PLURAL[type]}`, placeholder: 'e.g. Rewatch, Shounen, Seasonal…', ok: 'Create' }
    : { title: 'New collection', label: 'Collections can mix TV shows, anime and movies.', placeholder: 'e.g. Weekend binge', ok: 'Create' });
  if (!name) return null;
  try {
    const col = await api('POST', '/api/collections', { name, type });
    S.collections.push(col);
    render();
    return col;
  } catch (e) { fail(e); return null; }
}

async function renameCollection(id) {
  const col = S.collections.find((c) => c.id === id);
  if (!col) return;
  const name = await promptModal({ title: col.type ? 'Rename folder' : 'Rename collection', value: col.name });
  if (!name) return;
  try {
    Object.assign(col, await api('PUT', `/api/collections/${id}`, { name }));
    render();
  } catch (e) { fail(e); }
}

async function deleteCollection(id) {
  const col = S.collections.find((c) => c.id === id);
  if (!col) return;
  if (!await confirmModal(`Delete "${col.name}"?`, 'The titles in it stay in your list.', 'Delete')) return;
  if (col.type && S.folderSel[col.type] === id) S.folderSel[col.type] = '';
  try {
    await api('DELETE', `/api/collections/${id}`);
    if (location.hash === `#/collections/${id}`) location.hash = '#/collections';
    await reload();
  } catch (e) { fail(e); }
}

/* ============================================================ add flow */

function openAdd(type = 'tv', query = '', preCol = '') {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Add to your list</h2><p class="sub">Search online - the summary, ratings and trailer are added automatically.</p>
    <div class="tabs">${TYPES.map((t) => `<button data-type="${t}">${TYPE_ONE[t] === 'anime' ? 'Anime' : TYPE_ONE[t][0].toUpperCase() + TYPE_ONE[t].slice(1)}</button>`).join('')}</div>
    <div class="input-row"><input class="input" id="add-q" placeholder="Search…" autocomplete="off" spellcheck="false">
      <select class="select" id="add-col" title="Also put it in a folder or collection"></select></div>
    <div id="add-out"></div></div>`, 'add-modal');
  const st = { type, seq: 0 };
  const input = $('#add-q', wrap);
  input.value = query;

  const setType = (t) => {
    st.type = t;
    $$('.tabs button', wrap).forEach((b) => b.classList.toggle('on', b.dataset.type === t));
    input.placeholder = t === 'anime' ? 'Search MyAnimeList, or paste a myanimelist.net/anime/… link' : `Search ${TYPE_PLURAL[t]} on TMDB…`;
    const select = $('#add-col', wrap);
    const keep = select.value || preCol;
    const folders = sectionFolders(t);
    const cols = globalCollections();
    select.innerHTML = '<option value="">No folder</option>'
      + (folders.length ? `<optgroup label="${esc(TYPE_PLURAL[t])} folders">${folders.map((f) => `<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('')}</optgroup>` : '')
      + (cols.length ? `<optgroup label="Collections">${cols.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</optgroup>` : '');
    select.value = [...folders, ...cols].some((c) => c.id === keep) ? keep : '';
    run();
  };
  const run = async () => {
    const out = $('#add-out', wrap);
    const q = input.value.trim();
    const needs = st.type === 'anime' ? !S.status.mal : !S.status.tmdb;
    if (needs) {
      out.innerHTML = `<div class="warn" style="margin-top:14px">${st.type === 'anime' ? 'Add your MyAnimeList Client ID' : 'Add your TMDB token'} in Settings to search ${TYPE_PLURAL[st.type].toLowerCase()}.
        <button class="btn btn-sm btn-white" data-settings>Open Settings</button></div>`;
      $('[data-settings]', out).addEventListener('click', () => openSettings());
      return;
    }
    if (!q) { out.innerHTML = ''; return; }
    const seq = ++st.seq;
    out.innerHTML = '<div class="spinner"></div>';
    try {
      const { results } = await api('GET', `/api/search?type=${st.type}&q=${encodeURIComponent(q)}`);
      if (seq !== st.seq) return;
      out.innerHTML = results.length ? `<div class="results">${results.map((r, i) => resultHTML(r, i)).join('')}</div>`
        : `<p class="muted center" style="margin-top:24px">No results. Try another spelling${st.type === 'anime'
          ? ', the Japanese title, or paste the show\'s myanimelist.net link' : ''}.</p>`;
      st.results = results;
    } catch (e) {
      if (seq === st.seq) out.innerHTML = `<div class="warn" style="margin-top:14px">${esc(e.message)}</div>`;
    }
  };

  let timer;
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 450); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { clearTimeout(timer); run(); } });
  $('.tabs', wrap).addEventListener('click', (e) => { const b = e.target.closest('[data-type]'); if (b) setType(b.dataset.type); });
  $('#add-out', wrap).addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-pick]');
    if (!btn) return;
    const r = st.results[Number(btn.dataset.pick)];
    if (r.in_list) { closeModal(wrap); openTitle(r.in_list); return; }
    btn.disabled = true;
    btn.textContent = 'Adding…';
    try {
      const col = $('#add-col', wrap).value;
      const item = await api('POST', '/api/items', { type: st.type, source: r.source, source_id: r.source_id, collections: col ? [col] : [] });
      upsert(item);
      r.in_list = item.id;
      btn.disabled = false;
      btn.className = 'btn btn-ghost btn-sm';
      btn.textContent = '✓ In your list';
      render();
      toast(`Added ${item.title}`);
    } catch (err) {
      btn.disabled = false;
      btn.textContent = '+ Add';
      fail(err);
    }
  });
  setType(type);
  setTimeout(() => input.focus(), 30);
}

function resultHTML(r, i) {
  const meta = [r.year, r.media_type, r.rating ? `★ ${fmtRating(r.rating)}` : '',
    r.episodes_total ? `${r.episodes_total} eps` : ''].filter(Boolean).join(' · ');
  return `<div class="result">
    ${r.poster ? `<img src="${esc(r.poster)}" alt="" loading="lazy">` : '<div class="noimg"></div>'}
    <div><div class="r-title">${esc(r.title)}${r.original_title ? `<small>${esc(r.original_title)}</small>` : ''}</div>
      <div class="r-meta">${esc(meta)}</div><div class="r-desc">${esc(r.overview)}</div></div>
    <button class="btn btn-sm ${r.in_list ? 'btn-ghost' : 'btn-white'}" data-pick="${i}">${r.in_list ? '✓ In your list' : '+ Add'}</button>
  </div>`;
}

/* ========================================================== title page */

function openTitle(id) {
  const item = findItem(id);
  if (!item) return;
  const existing = $$('#modal-root .overlay').find((o) => o.dataset.itemId === id);
  if (existing) return;
  const wrap = openModal('', 'title-modal');
  wrap.dataset.itemId = id;
  wrap.st = { season: item.progress.season, page: 0, local: null, seasons: {} };
  drawTitle(wrap);
  loadLocal(wrap);
}

function refreshOpenTitle() {
  $$('#modal-root .overlay').forEach((wrap) => {
    if (!wrap.dataset.itemId) return;
    if (!findItem(wrap.dataset.itemId)) { closeModal(wrap); return; }
    if ($('.tm-trailer', wrap)) return;
    const scroll = wrap.scrollTop;
    drawTitle(wrap);
    wrap.scrollTop = scroll;
  });
}

function drawTitle(wrap) {
  const item = findItem(wrap.dataset.itemId);
  const modal = $('.modal', wrap);
  const series = isSeries(item);
  const meta = [];
  if (item.rating) meta.push(`<span class="score">★ ${fmtRating(item.rating)}</span><span>${esc(item.rating_source)}</span>`);
  if (item.year) meta.push(`<span>${item.year}</span>`);
  if (series && item.seasons.length > 1) meta.push(`<span>${item.seasons.length} Seasons</span>`);
  if (series && item.episodes_total) meta.push(`<span>${item.episodes_total} Episodes</span>`);
  if (item.runtime) meta.push(`<span>${fmtRuntime(item.runtime)}${series ? ' / ep' : ''}</span>`);
  if (item.airing_status) meta.push(`<span class="pill">${esc(item.airing_status)}</span>`);

  const side = [];
  if (item.cast.length) side.push(`<div>Cast: <span>${esc(item.cast.slice(0, 6).join(', '))}</span></div>`);
  if (item.genres.length) side.push(`<div>Genres: <span>${esc(item.genres.join(', '))}</span></div>`);
  if (item.studios.length) side.push(`<div>${item.type === 'anime' ? 'Studio' : item.type === 'tv' ? 'Network' : 'Studio'}: <span>${esc(item.studios.join(', '))}</span></div>`);
  if (item.revenue) side.push(`<div>Box office: <span>${fmtMoney(item.revenue)}</span></div>`);
  if (item.mal_rank) side.push(`<div>MAL rank: <span>#${item.mal_rank}</span></div>`);
  if (item.mal_members) side.push(`<div>MAL members: <span>${item.mal_members.toLocaleString()}</span></div>`);
  if (item.votes) side.push(`<div>Votes: <span>${item.votes.toLocaleString()}</span></div>`);
  const folder = sectionFolders(item.type).find((c) => item.collections.includes(c.id));
  const cols = globalCollections().filter((c) => item.collections.includes(c.id));
  side.push(`<div>Folder: <span>${folder ? esc(folder.name) : 'none'}</span></div>`);
  if (cols.length) side.push(`<div>Collections: <span>${esc(cols.map((c) => c.name).join(', '))}</span></div>`);
  const links = Object.entries(item.links || {}).map(([k, url]) => `<button class="linkish" data-link="${esc(url)}">${{ tmdb: 'TMDB', imdb: 'IMDb', mal: 'MyAnimeList' }[k] || esc(k)}</button>`);
  if (links.length) side.push(`<div>Links: <span>${links.join(' · ')}</span></div>`);

  const heroImg = item.backdrop ? `<img src="${esc(item.backdrop)}" alt="">`
    : item.poster ? `<img class="poster-blur" src="${esc(item.poster)}" alt="">` : '';

  let progress = '';
  if (series) {
    const total = item.episodes_total || item.seasons.reduce((n, s) => n + (s.episodes || 0), 0);
    const done = absoluteIndex(item, item.progress) - 1;
    const pct = total ? Math.min(100, (done / total) * 100) : 0;
    progress = `<div class="progress-box"><span class="muted">Up next</span><b>${epLabel(item.progress)}</b>
      <div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>
      <span class="muted small">${done} of ${total || '?'} watched</span>
      <button class="btn btn-ghost btn-sm" data-t="watched">${ICON.check} Mark watched</button></div>`;
  }

  modal.innerHTML = `<button class="modal-close" aria-label="Close">×</button>
    <div class="tm-hero">${heroImg}
      ${item.backdrop && item.poster ? `<img class="poster-side" src="${esc(item.poster)}" alt="">` : ''}
      <div class="tm-hero-content">
        <h1>${esc(item.title)}</h1>
        <div class="btns">
          <button class="btn btn-white" data-t="watch">${ICON.play} ${series ? `Watch ${epLabel(item.progress)}` : 'Watch now'}</button>
          <button class="btn btn-grey" data-t="trailer">${ICON.play} ${item.trailer ? 'Trailer' : 'Find trailer'}</button>
          <button class="btn-round" data-t="menu" title="Collections & more">${ICON.plus}</button>
          <button class="btn-round" data-t="remove" title="Remove from list">${ICON.trash}</button>
        </div>
      </div>
    </div>
    <div class="tm-body">
      <div>
        <div class="tm-meta">${meta.join('')}</div>
        ${item.original_title ? `<p class="tm-tagline" style="font-style:normal">${esc(item.original_title)}</p>` : ''}
        ${progress}
        ${item.tagline ? `<p class="tm-tagline">${esc(item.tagline)}</p>` : ''}
        <p class="tm-overview">${esc(item.overview || 'No summary available.')}</p>
      </div>
      <aside class="tm-side">${side.join('')}</aside>
    </div>
    <section class="tm-section"><h3>On your ${pcName()}</h3><div class="folder-line" id="folder-line"></div></section>
    ${series ? '<section class="tm-section"><h3>Episodes<span class="spacer"></span><span id="ep-picker"></span></h3><div class="episodes" id="ep-list"></div></section>' : ''}`;

  drawFolderLine(wrap);
  if (series) drawEpisodes(wrap);
  modal.onclick = (e) => titleClick(wrap, e);
}

function absoluteIndex(item, p) {
  let n = 0;
  for (const s of item.seasons) {
    if (s.number < p.season) n += s.episodes || 0;
  }
  return n + p.episode;
}

async function titleClick(wrap, e) {
  const item = findItem(wrap.dataset.itemId);
  const link = e.target.closest('[data-link]');
  if (link) { api('POST', '/api/open', { url: link.dataset.link }).catch(fail); return; }
  const ep = e.target.closest('[data-ep]');
  if (ep) {
    const [s, n] = ep.dataset.ep.split(':').map(Number);
    if (e.target.closest('.ep-set')) {
      try {
        upsert(await api('PUT', `/api/items/${item.id}`, { progress: { season: s, episode: n } }));
        render(); refreshOpenTitle();
      } catch (err) { fail(err); }
      return;
    }
    openWatch(item, { season: s, episode: n });
    return;
  }
  const t = e.target.closest('[data-t]')?.dataset.t;
  try {
    if (t === 'watch') openWatch(item);
    else if (t === 'trailer') playTrailer(wrap, item);
    else if (t === 'close-trailer') { $('.tm-trailer', wrap)?.remove(); }
    else if (t === 'youtube') api('POST', '/api/open', { url: `https://www.youtube.com/watch?v=${item.trailer}` }).catch(fail);
    else if (t === 'menu') { e.stopPropagation(); openItemMenu(item, e.target.closest('[data-t]'), { inTitle: true }); }
    else if (t === 'remove') removeItem(item.id);
    else if (t === 'watched') {
      const res = await api('POST', `/api/items/${item.id}/watched`);
      upsert(res.item);
      if (res.finished) toast(`You've finished ${item.title}!`);
      else toast(`Up next: ${epLabel(res.item.progress)}`);
      wrap.st.season = res.item.progress.season;
      render(); refreshOpenTitle();
    } else if (t === 'choose-folder') {
      if (await chooseFolder(item)) loadLocal(wrap);
    } else if (t === 'open-folder') {
      await api('POST', '/api/reveal', { item_id: item.id });
    } else if (t === 'choose-file') {
      openChooseFile(item);
    } else if (t === 'mark-broken' || t === 'mark-ok') {
      upsert(await api('PUT', `/api/items/${item.id}`, { local_broken: t === 'mark-broken' }));
      render(); refreshOpenTitle();
      toast(t === 'mark-broken' ? 'Marked as not working - Watch now will go online' : 'Watch now will play the file again');
    } else if (t === 'forget-folder') {
      upsert(await api('PUT', `/api/items/${item.id}`, { local_folder: '' }));
      wrap.st.local = null;
      render(); refreshOpenTitle();
    }
  } catch (err) { fail(err); }
}

function playTrailer(wrap, item) {
  if (!item.trailer) {
    const q = encodeURIComponent(`${item.title} ${item.year || ''} trailer`);
    api('POST', '/api/open', { url: `https://www.youtube.com/results?search_query=${q}` })
      .then(() => toast(`No trailer saved - opened a YouTube search in ${browserName()}`))
      .catch(fail);
    return;
  }
  const hero = $('.tm-hero', wrap);
  $('.tm-trailer', hero)?.remove();
  const box = document.createElement('div');
  box.className = 'tm-trailer';
  box.innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(item.trailer)}?autoplay=1&rel=0&modestbranding=1"
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin" title="Trailer"></iframe>
    <div class="trailer-bar"><button class="btn btn-grey btn-sm" data-t="close-trailer">✕ Close trailer</button>
    <button class="btn btn-grey btn-sm" data-t="youtube">Open on YouTube</button></div>`;
  hero.append(box);
}

function drawFolderLine(wrap) {
  const item = findItem(wrap.dataset.itemId);
  const line = $('#folder-line', wrap);
  if (!line) return;
  const local = wrap.st.local;
  if (!item.local_folder) {
    line.innerHTML = `<span>No folder chosen yet. Pick the folder with ${isSeries(item) ? 'the episodes' : 'the movie'} and it'll be remembered.</span>
      <button class="btn btn-white btn-sm" data-t="choose-folder">${ICON.folder} Choose folder</button>`;
    return;
  }
  let found = '';
  if (local && !local.exists) found = '<span class="bad">This folder is missing - was it moved or is the drive unplugged?</span>';
  else if (local) {
    const nVid = local.videos.length;
    found = `<span>${nVid} video file${nVid === 1 ? '' : 's'}${isSeries(item) ? `, ${local.available.length} episode${local.available.length === 1 ? '' : 's'} recognised` : ''}</span>`;
  }
  const status = item.local_broken
    ? `<div class="local-status bad">⚠ Marked as not working - Watch now goes online until you fix it.
        Choose the right file or folder, or press "It works now".</div>`
    : '<div class="local-status ok">✓ Watch now plays from here in VLC. If the wrong file plays or it won\'t play, press "Not working".</div>';
  line.innerHTML = `${status}<code>${esc(item.local_folder)}</code>${found}
    <button class="btn btn-ghost btn-sm" data-t="choose-file">Choose file…</button>
    <button class="btn btn-ghost btn-sm" data-t="open-folder">Open folder</button>
    <button class="btn btn-ghost btn-sm" data-t="choose-folder">Change folder</button>
    ${item.local_broken ? '<button class="btn btn-white btn-sm" data-t="mark-ok">It works now</button>'
      : '<button class="btn btn-ghost btn-sm" data-t="mark-broken">Not working</button>'}
    <button class="btn btn-ghost btn-sm" data-t="forget-folder">Forget</button>`;
}

async function loadLocal(wrap) {
  const item = findItem(wrap.dataset.itemId);
  if (!item || !item.local_folder) return;
  try {
    wrap.st.local = await api('GET', `/api/items/${item.id}/local`);
  } catch { wrap.st.local = null; }
  if (!wrap.isConnected) return;
  drawFolderLine(wrap);
  if (isSeries(item)) drawEpisodes(wrap);
}

async function drawEpisodes(wrap) {
  const item = findItem(wrap.dataset.itemId);
  const list = $('#ep-list', wrap);
  const picker = $('#ep-picker', wrap);
  if (!list) return;
  const st = wrap.st;
  const seasons = item.seasons.length ? item.seasons : [{ number: 1, episodes: item.episodes_total || 0 }];
  if (!seasons.some((s) => s.number === st.season)) st.season = seasons[0].number;
  const season = seasons.find((s) => s.number === st.season);
  let count = season.episodes;
  const available = new Set(st.local?.available || []);
  if (!count) {
    // Unknown episode count (still airing): use what's on disk, or the progress.
    const onDisk = [...available].filter((k) => k.startsWith(`${st.season}:`)).map((k) => Number(k.split(':')[1]));
    count = Math.max(item.progress.season === st.season ? item.progress.episode : 0, ...onDisk, 12);
  }
  const PAGE = 100;
  const pages = Math.ceil(count / PAGE);
  if (st.page >= pages) st.page = 0;

  picker.innerHTML = `${seasons.length > 1 ? `<select class="select" id="season-sel">${seasons.map((s) => `<option value="${s.number}" ${s.number === st.season ? 'selected' : ''}>Season ${s.number}</option>`).join('')}</select>` : ''}
    ${pages > 1 ? `<select class="select" id="page-sel">${Array.from({ length: pages }, (_, i) => `<option value="${i}" ${i === st.page ? 'selected' : ''}>Episodes ${i * PAGE + 1}-${Math.min(count, (i + 1) * PAGE)}</option>`).join('')}</select>` : ''}`;
  $('#season-sel', picker)?.addEventListener('change', (e) => { st.season = Number(e.target.value); st.page = 0; drawEpisodes(wrap); });
  $('#page-sel', picker)?.addEventListener('change', (e) => { st.page = Number(e.target.value); drawEpisodes(wrap); });

  // TMDB has episode names and pictures for TV shows.
  let details = null;
  if (item.type === 'tv' && item.tmdb_ref.startsWith('tv/')) {
    details = st.seasons[st.season];
    if (details === undefined) {
      st.seasons[st.season] = null;
      api('GET', `/api/items/${item.id}/season/${st.season}`)
        .then((d) => { st.seasons[st.season] = d.episodes; if (wrap.isConnected) drawEpisodes(wrap); })
        .catch(() => { st.seasons[st.season] = []; });
    }
  }
  const byNum = {};
  (details || []).forEach((d) => { byNum[d.number] = d; });
  const p = item.progress;
  const rows = [];
  for (let n = st.page * PAGE + 1; n <= Math.min(count, (st.page + 1) * PAGE); n++) {
    const d = byNum[n];
    const key = `${st.season}:${n}`;
    const watched = st.season < p.season || (st.season === p.season && n < p.episode);
    const current = st.season === p.season && n === p.episode;
    const tags = [
      current ? '<span class="tag tag-next">UP NEXT</span>' : '',
      available.has(key) ? `<span class="tag tag-pc">ON ${pcName().toUpperCase()}</span>` : '',
      watched ? '<span class="tag tag-done">✓ Watched</span>' : '',
      current ? '' : '<button class="ep-set" title="Make this the next episode">Set as next</button>',
    ].join('');
    if (d) {
      rows.push(`<div class="ep ${current ? 'current' : ''} ${watched ? 'watched' : ''}" data-ep="${key}">
        <div class="ep-num">${n}</div>
        ${d.still ? `<img class="ep-thumb" src="${esc(d.still)}" alt="" loading="lazy">` : `<div class="ep-thumb">${ICON.play}</div>`}
        <div><div class="ep-title">${esc(d.name || `Episode ${n}`)}${d.runtime ? `<small>${d.runtime}m</small>` : ''}</div>
          <div class="ep-desc">${esc(d.overview)}</div></div>
        <div class="ep-tags">${tags}</div></div>`);
    } else {
      rows.push(`<div class="ep compact ${current ? 'current' : ''} ${watched ? 'watched' : ''}" data-ep="${key}">
        <div class="ep-num">${n}</div><div class="ep-title">Episode ${n}</div><div class="ep-tags">${tags}</div></div>`);
    }
  }
  list.innerHTML = rows.join('');
}

/* ========================================================== watch flow */

async function chooseFolder(item) {
  let path = '';
  try {
    ({ path } = await api('POST', '/api/browse', {
      kind: 'folder', initial: item.local_folder,
      title: `Choose the folder with ${item.title}`,
    }));
  } catch (e) {
    path = await promptModal({ title: `Folder for ${item.title}`, label: `${e.message}. Paste the folder path:`, placeholder: 'D:\\Anime\\Frieren' });
  }
  if (!path) return null;
  try {
    const updated = await api('PUT', `/api/items/${item.id}`, { local_folder: path });
    upsert(updated);
    render(); refreshOpenTitle();
    toast('Folder saved - it will be remembered');
    return updated;
  } catch (e) { fail(e); return null; }
}

// Watch now: a title with a working folder plays straight away; one marked
// "not working" goes online; anything else asks On my PC / Online.
function openWatch(item, ep) {
  const wrap = openModal('', 'watch-modal');
  wrap.st = { id: item.id, ep: ep ? { ...ep } : { ...item.progress } };
  if (item.local_folder && !item.local_broken) autoLocal(wrap);
  else if (item.local_folder) watchOnline(wrap, { reason: 'Its file is marked as not working, so going online instead.', fix: true });
  else watchChoice(wrap);
}

// Pick a file by hand (from the title page or after a wrong episode).
function openChooseFile(item, ep) {
  const wrap = openModal('', 'watch-modal');
  wrap.st = { id: item.id, ep: ep ? { ...ep } : { ...item.progress } };
  watchLocal(wrap, { choose: true });
}

const whatLabel = (wrap) => {
  const item = findItem(wrap.st.id);
  return isSeries(item) ? epLabel(wrap.st.ep) : item.title;
};

async function fetchLocal(wrap, path = '') {
  const q = new URLSearchParams({ season: wrap.st.ep.season, episode: wrap.st.ep.episode, path });
  wrap.st.info = await api('GET', `/api/items/${wrap.st.id}/local?${q}`);
  return wrap.st.info;
}

async function autoLocal(wrap) {
  const item = findItem(wrap.st.id);
  const what = whatLabel(wrap);
  watchBody(wrap, `<div class="center"><div class="spinner"></div><p class="muted">Starting ${esc(what)}…</p></div>`);
  let info;
  try {
    info = await fetchLocal(wrap);
  } catch (e) {
    return watchOnline(wrap, { reason: `Couldn't read ${item.title}'s folder (${e.message}), so going online.`, fix: true });
  }
  if (!wrap.isConnected) return;
  if (!info.exists) {
    return watchOnline(wrap, { reason: `${item.title}'s folder wasn't found (moved, renamed or the drive isn't plugged in), so going online.`, fix: true });
  }
  if (!info.target) {
    return watchOnline(wrap, { reason: `${what} isn't in ${item.title}'s folder, so going online.`, fix: true });
  }
  try {
    await playFile(wrap, info.target, info.subtitles[0]);
  } catch (e) {
    if (wrap.isConnected) watchOnline(wrap, { reason: `The file couldn't be played (${e.message}), so going online.`, fix: true });
  }
}

async function playFile(wrap, video, sub) {
  const item = findItem(wrap.st.id);
  const res = await api('POST', `/api/items/${item.id}/play`, {
    path: video.path, subtitle: sub ? sub.path : '',
    season: wrap.st.ep.season, episode: wrap.st.ep.episode,
  });
  await reload();
  if (wrap.isConnected) drawPlaying(wrap, res, video, sub);
}

function drawPlaying(wrap, res, video, sub) {
  const item = findItem(wrap.st.id);
  const body = watchBody(wrap, `<div class="center">
    <div class="result-icon">▶️</div>
    <h2>Playing ${esc(whatLabel(wrap))} in VLC</h2>
    <p class="sub">${esc(video.name)}<br>
      <span class="small">Subtitles: ${sub ? esc(sub.name) : 'none found'}${res.queued ? ` · the next ${res.queued} episode${res.queued === 1 ? '' : 's'} play after it, in order` : ''}</span></p>
    <div class="btns" style="justify-content:center"><button class="btn btn-white" data-w="done">Done</button>
      <button class="btn btn-ghost" data-w="choose">Wrong episode? Choose the file</button></div>
    <div class="btns" style="justify-content:center;margin-top:10px">
      <button class="btn btn-ghost btn-sm" data-w="broken">File not working - watch online instead</button></div>
  </div>`);
  body.addEventListener('click', (e) => localAction(wrap, e));
  if (item.format === 'movie') $('[data-w="choose"]', body).textContent = 'Wrong file? Choose another';
}

function watchBody(wrap, html) {
  $('.modal', wrap).innerHTML = `<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">${html}</div>`;
  return $('.modal-pad', wrap);
}

function watchChoice(wrap) {
  const item = findItem(wrap.st.id);
  const ep = wrap.st.ep;
  const seasons = item.seasons.length ? item.seasons : [{ number: 1, episodes: item.episodes_total || 0 }];
  const picker = isSeries(item) ? `<span class="ep-picker">
      ${seasons.length > 1 ? `<select class="select" id="w-season">${seasons.map((s) => `<option value="${s.number}" ${s.number === ep.season ? 'selected' : ''}>Season ${s.number}</option>`).join('')}</select>` : ''}
      <span>Episode</span><input class="input" id="w-ep" type="number" min="1" value="${ep.episode}"></span>` : '';
  const region = regionName(S.status.region || 'PK');
  const body = watchBody(wrap, `<h2>How do you want to watch?</h2>
    <p class="sub">${esc(item.title)}${picker ? ' · ' : ''}${picker}</p>
    <div class="choices">
      <button class="choice" data-choice="local">${ICON.pc}<b>On my ${pcName()}</b><span>${item.local_folder ? 'Play from your saved folder in VLC' : 'Pick the folder once, then it plays in VLC'}</span></button>
      <button class="choice" data-choice="online">${ICON.globe}<b>Online</b><span>Netflix → HBO Max (${esc(region)}) → everythingmoe</span></button>
    </div>`);
  const readEp = () => {
    const s = $('#w-season', body);
    const e = $('#w-ep', body);
    if (s) ep.season = Number(s.value);
    if (e) ep.episode = Math.max(1, Number(e.value) || 1);
  };
  body.addEventListener('click', (e) => {
    const c = e.target.closest('[data-choice]')?.dataset.choice;
    if (!c) return;
    readEp();
    if (c === 'local') startLocal(wrap); else watchOnline(wrap);
  });
}

async function startLocal(wrap) {
  let item = findItem(wrap.st.id);
  if (!item.local_folder || item.local_broken) {
    watchBody(wrap, '<div class="center"><div class="spinner"></div><p class="muted">Choose the folder in the window that just opened…</p></div>');
    item = await chooseFolder(item);
    if (!wrap.isConnected) return;
    if (!item) { watchChoice(wrap); return; }
  }
  autoLocal(wrap);
}

async function watchLocal(wrap, { path = '', choose = false } = {}) {
  let item = findItem(wrap.st.id);
  if (!item.local_folder) {
    watchBody(wrap, '<div class="center"><div class="spinner"></div><p class="muted">Choose the folder in the window that just opened…</p></div>');
    item = await chooseFolder(item);
    if (!wrap.isConnected) return;
    if (!item) { watchChoice(wrap); return; }
  }
  watchBody(wrap, '<div class="center"><div class="spinner"></div><p class="muted">Looking for the file…</p></div>');
  let info;
  try {
    info = await fetchLocal(wrap, path);
  } catch (e) { fail(e); watchChoice(wrap); return; }
  if (!wrap.isConnected) return;
  if (!info.exists) {
    const body = watchBody(wrap, `<h2>Folder not found</h2><p class="sub">${esc(item.local_folder)}<br>It may have been moved, renamed, or be on a drive that isn't plugged in.</p>
      <div class="btns"><button class="btn btn-white" data-w="change">${ICON.folder} Choose the folder again</button><button class="btn btn-ghost" data-w="back">Back</button></div>`);
    body.addEventListener('click', (e) => localAction(wrap, e));
    return;
  }
  drawLocal(wrap, { choose });
}

function videoLabel(v) {
  if (!v.episodes.length) return '';
  return v.season != null ? `S${v.season} E${v.episodes.join('-')}` : `E${v.episodes.join('-')}`;
}

function drawLocal(wrap, { choose = false } = {}) {
  const item = findItem(wrap.st.id);
  const info = wrap.st.info;
  const target = info.target;
  const series = isSeries(item);
  const what = series ? epLabel(wrap.st.ep) : item.title;
  const videoList = `<div class="file-list">${info.videos.map((v, i) => `<button data-video="${i}">
      ${videoLabel(v) ? `<span class="tag">${esc(videoLabel(v))}</span>` : ''}<span class="fname">${esc(v.rel)}</span></button>`).join('')}</div>`;
  const footer = `<div class="footer-line">${ICON.folder.replace('<svg', '<svg width="16" height="16"')}<code>${esc(item.local_folder)}</code>
    <span class="spacer" style="flex:1"></span><button class="btn btn-ghost btn-sm" data-w="browse">Browse for a file…</button>
    <button class="btn btn-ghost btn-sm" data-w="change">Change folder</button>
    <button class="btn btn-ghost btn-sm" data-w="back">Back</button></div>`;

  if (!target) {
    const body = watchBody(wrap, `<h2>Couldn't find ${esc(what)}</h2>
      <p class="sub">${info.videos.length ? `There are ${info.videos.length} video files in the folder, but none looks like ${esc(what)}. Pick the right one:` : 'There are no video files in this folder.'}</p>
      ${info.videos.length ? videoList : ''}${footer}`);
    body.addEventListener('click', (e) => localAction(wrap, e));
    return;
  }

  const subs = info.subtitles;
  const subOptions = [
    ...subs.map((s, i) => `<option value="${i}" ${i === 0 ? 'selected' : ''}>${esc(s.rel)}${s.english ? ' (English)' : ''}</option>`),
    `<option value="none" ${subs.length ? '' : 'selected'}>${subs.length ? 'No subtitles' : 'No subtitle files found'}</option>`,
    '<option value="pick">Choose a subtitle file…</option>',
  ].join('');
  const body = watchBody(wrap, `<h2>${choose ? 'Choose what to play' : `Ready to play ${esc(what)}`}</h2>
    <p class="sub">${esc(item.title)}${choose ? ` · picked: ${esc(what)}` : ''}</p>
    <div class="file-card">${ICON.file}<div><b>${esc(target.name)}</b><small>${esc(target.rel)}</small></div></div>
    <label class="field"><span>Subtitles</span>
      <div class="input-row"><select class="select" id="w-sub" style="flex:1">${subOptions}</select>
      <button class="btn btn-ghost btn-sm" data-w="reveal-sub" title="Show the subtitle file in its folder" ${subs.length ? '' : 'disabled'}>Show file</button></div>
      <small>Subtitle files with the same name as the video load automatically. ${series ? 'The next episodes are queued in VLC with their matching subtitles.' : ''}</small></label>
    <div class="btns"><button class="btn btn-red" data-w="play">${ICON.play} Play in VLC</button>
      <button class="btn btn-ghost" data-w="reveal">Show in folder</button></div>
    ${info.videos.length > 1 ? `<details style="margin-top:16px" ${choose ? 'open' : ''}><summary>All files in the folder (${info.videos.length}) - click one to pick it</summary>${videoList}</details>` : ''}
    ${footer}`);
  if (!S.status.vlc) {
    body.insertAdjacentHTML('afterbegin', `<div class="warn" style="margin-bottom:16px">VLC wasn't found on this ${pcName()}. Install it from videolan.org, or set its location in Settings.</div>`);
  }
  body.addEventListener('click', (e) => localAction(wrap, e));
  $('#w-sub', body).addEventListener('change', async (e) => {
    if (e.target.value !== 'pick') return;
    try {
      const { path } = await api('POST', '/api/browse', { kind: 'file', types: 'subtitle', title: 'Choose a subtitle file', initial: item.local_folder });
      if (path) {
        info.subtitles.unshift({ path, name: path.split(/[\\/]/).pop(), rel: path.split(/[\\/]/).pop(), english: false });
        drawLocal(wrap);
        return;
      }
    } catch (err) { fail(err); }
    e.target.value = info.subtitles.length ? '0' : 'none';
  });
}

async function localAction(wrap, e) {
  const item = findItem(wrap.st.id);
  const info = wrap.st.info;
  const video = e.target.closest('[data-video]');
  if (video) {
    const v = info.videos[Number(video.dataset.video)];
    if (isSeries(item) && v.episodes.length) {
      wrap.st.ep = { season: v.season ?? wrap.st.ep.season, episode: v.episodes[0] };
    }
    watchLocal(wrap, { path: v.path, choose: true });
    return;
  }
  const w = e.target.closest('[data-w]')?.dataset.w;
  if (!w) return;
  try {
    if (w === 'back') watchChoice(wrap);
    else if (w === 'done') closeModal(wrap);
    else if (w === 'choose') {
      if (wrap.st.info?.exists) drawLocal(wrap, { choose: true }); else watchLocal(wrap, { choose: true });
    } else if (w === 'broken') {
      upsert(await api('PUT', `/api/items/${item.id}`, { local_broken: true }));
      render(); refreshOpenTitle();
      toast('Marked as not working - fix it any time from the title page');
      watchOnline(wrap, { reason: 'Marked the file as not working. Watch now goes online until you fix it.', fix: true });
    } else if (w === 'change') {
      const updated = await chooseFolder(item);
      if (updated && wrap.isConnected) autoLocal(wrap);
    } else if (w === 'browse') {
      const { path } = await api('POST', '/api/browse', { kind: 'file', types: 'video', title: `Choose a file for ${item.title}`, initial: item.local_folder });
      if (!path) return;
      upsert(await api('PUT', `/api/items/${item.id}`, { local_file: path }));
      render(); refreshOpenTitle();
      watchLocal(wrap, { path, choose: true });
    } else if (w === 'reveal') {
      await api('POST', '/api/reveal', { item_id: item.id, path: info.target.path });
    } else if (w === 'reveal-sub') {
      const sel = $('#w-sub', wrap).value;
      const sub = info.subtitles[Number(sel)];
      if (sub) await api('POST', '/api/reveal', { item_id: item.id, path: sub.path });
    } else if (w === 'play') {
      const sel = $('#w-sub', wrap).value;
      const sub = info.subtitles[Number(sel)];
      const btn = e.target.closest('[data-w]');
      btn.disabled = true;
      if (findItem(item.id).local_broken) {
        upsert(await api('PUT', `/api/items/${item.id}`, { local_broken: false }));
      }
      await playFile(wrap, info.target, sub);
    }
  } catch (err) {
    fail(err);
    const btn = e.target.closest('[data-w]');
    if (btn) btn.disabled = false;
  }
}

async function watchOnline(wrap, { reason = '', fix = false } = {}) {
  const item = findItem(wrap.st.id);
  const region = regionName(S.status.region || 'PK');
  watchBody(wrap, `<div class="center"><div class="spinner"></div><h2>Checking Netflix and HBO Max…</h2><p class="muted">Looking up where ${esc(item.title)} streams in ${esc(region)}</p></div>`);
  try {
    if (isSeries(item)) {
      upsert(await api('PUT', `/api/items/${item.id}`, { progress: wrap.st.ep }));
    }
    const res = await api('POST', `/api/items/${item.id}/online`);
    if (!wrap.isConnected) return;
    const found = res.service === 'Netflix' || res.service === 'HBO Max';
    const body = watchBody(wrap, `<div class="center">
      ${reason ? `<div class="warn" style="margin-bottom:18px;justify-content:center">${esc(reason)}</div>` : ''}
      <div class="result-icon">${found ? '✅' : '🔎'}</div>
      <h2>${found ? `Opening ${esc(res.service)}` : `Not on Netflix or HBO Max in ${esc(region)}`}</h2>
      <p class="sub">${found ? `${esc(item.title)} is on ${esc(res.service)} in ${esc(region)}. It's open in ${browserName()}${isSeries(item) ? ` - you're up to ${epLabel(wrap.st.ep)}` : ''}.`
        : `everythingmoe.com is open in ${browserName()} so you can pick a site from there.`}</p>
      ${res.services.length ? `<div class="muted small">Streaming in ${esc(region)} on:</div><div class="services" style="justify-content:center">${res.services.map((s) => `<span class="chip">${esc(s)}</span>`).join('')}</div>` : ''}
      ${res.message ? `<p class="note">${esc(res.message)}</p>` : ''}
      <div class="btns" style="justify-content:center;margin-top:16px"><button class="btn btn-white" data-w="done">Done</button>
      <button class="btn btn-ghost" data-w="again">Open the page again</button>
      ${fix ? `<button class="btn btn-ghost" data-w="fix">Fix the file on my ${pcName()}</button>` : ''}</div></div>`);
    body.addEventListener('click', (e) => {
      const w = e.target.closest('[data-w]')?.dataset.w;
      if (w === 'done') closeModal(wrap);
      if (w === 'again') api('POST', '/api/open', { url: res.url }).catch(fail);
      if (w === 'fix') watchLocal(wrap, { choose: true });
    });
    render(); refreshOpenTitle();
  } catch (e) {
    fail(e);
    if (wrap.isConnected) watchChoice(wrap);
  }
}

/* ============================================================ settings */

const COUNTRIES = 'PK IN AE SA QA KW OM BH BD LK NP AF IR TR EG MA US CA GB IE AU NZ DE FR IT ES NL BE SE NO DK FI PL PT CH AT CZ RO GR HU JP KR CN HK TW SG MY ID TH PH VN BR MX AR CL CO PE ZA NG KE'.split(' ');

async function openSettings() {
  let data;
  try { data = await api('GET', '/api/settings'); } catch (e) { fail(e); return; }
  const s = data.settings;
  const mac = data.status.platform === 'darwin';
  const sync = data.status.sync || {};
  const countries = [...new Set([s.region, ...COUNTRIES])]
    .map((c) => ({ c, name: regionName(c) })).sort((a, b) => a.name.localeCompare(b.name));
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><form class="modal-pad" autocomplete="off">
    <h2>Settings</h2><p class="sub">Saved only on this ${pcName()}. Keys and app locations are set separately on each computer.</p>
    <h4>Online info</h4>
    <label class="field"><span>TMDB token (movies & TV shows)</span>
      <input class="input" name="tmdb_api_key" type="password" value="${esc(s.tmdb_api_key)}" placeholder="Paste your API Read Access Token or API Key">
      <small>themoviedb.org → profile → Settings → API. The long "API Read Access Token" or the short "API Key" both work.</small></label>
    <label class="field"><span>MyAnimeList Client ID (anime)</span>
      <input class="input" name="mal_client_id" type="password" value="${esc(s.mal_client_id)}" placeholder="Paste your Client ID">
      <small>myanimelist.net/apiconfig → your app → Client ID.</small></label>
    <div class="btns" style="margin-bottom:6px"><button class="btn btn-ghost btn-sm" type="button" data-s="show">Show keys</button>
      <button class="btn btn-ghost btn-sm" type="button" data-s="test">Save & test keys</button><span id="test-out" class="small"></span></div>
    <h4>Sync between computers</h4>
    <label class="field"><span>Sync folder</span>
      <div class="input-row"><input class="input" name="sync_folder" value="${esc(s.sync_folder)}" placeholder="Not syncing - choose a folder in Google Drive, OneDrive, Dropbox or iCloud Drive">
      <button class="btn btn-ghost btn-sm" type="button" data-s="sync-browse">Choose…</button>
      ${s.sync_folder ? '<button class="btn btn-ghost btn-sm" type="button" data-s="sync-off">Stop syncing</button>' : ''}</div>
      <small>${sync.problem ? `<span class="bad">${esc(sync.problem)}</span>`
        : s.sync_folder ? `<span class="ok">✓ Syncing.</span> Your list is in ${esc(sync.file)}.`
        : 'Your list is only on this computer right now.'}
        Choose a folder that both computers sync, then choose the same folder on the other computer and the two lists are merged.
        Titles, progress, folders and collections sync; each computer remembers its own video folders.</small></label>
    <h4>Watching online</h4>
    <label class="field"><span>Your country</span>
      <select class="select" name="region">${countries.map(({ c, name }) => `<option value="${c}" ${c === s.region ? 'selected' : ''}>${esc(name)} (${c})</option>`).join('')}</select>
      <small>Used to check what's on Netflix and HBO Max where you are.</small></label>
    <label class="field"><span>Browser</span>
      <div class="input-row"><input class="input" name="browser_path" value="${esc(s.browser_path)}" placeholder="${esc(data.status.browser || (mac ? '/Applications/Opera GX.app' : 'Opera GX: ...\\AppData\\Local\\Programs\\Opera GX\\launcher.exe'))}">
      <button class="btn btn-ghost btn-sm" type="button" data-s="browser">Browse</button></div>
      <small>${data.status.browser ? `<span class="ok">✓ Using:</span> ${esc(data.status.browser)}. Leave empty to keep this.`
        : `<span class="bad">Opera GX wasn't found</span> - links open in your default browser. ${mac ? 'Browse to Opera GX in Applications' : "Browse to Opera GX's launcher.exe"} to use it.`}
        Netflix, HBO Max, everythingmoe and trailers open here.</small></label>
    <label class="field"><span>Netflix link</span><input class="input" name="netflix_url" value="${esc(s.netflix_url)}"></label>
    <label class="field"><span>HBO Max link</span><input class="input" name="hbomax_url" value="${esc(s.hbomax_url)}"></label>
    <label class="field"><span>If neither has it, open</span><input class="input" name="fallback_url" value="${esc(s.fallback_url)}">
      <small>{title} in a link is replaced with the title's name.</small></label>
    <h4>Watching on your ${pcName()}</h4>
    <label class="field"><span>VLC location</span>
      <div class="input-row"><input class="input" name="vlc_path" value="${esc(s.vlc_path)}" placeholder="${esc(data.status.vlc || (mac ? '/Applications/VLC.app' : 'C:\\Program Files\\VideoLAN\\VLC\\vlc.exe'))}">
      <button class="btn btn-ghost btn-sm" type="button" data-s="vlc">Browse</button></div>
      <small>${data.status.vlc ? `<span class="ok">✓ Found VLC:</span> ${esc(data.status.vlc)}. Leave empty to use this.` : `<span class="bad">VLC not found.</span> Install it from videolan.org or browse to ${mac ? 'VLC in Applications' : 'vlc.exe'}.`}</small></label>
    <label class="check-row"><input type="checkbox" name="queue_next_episodes" ${s.queue_next_episodes ? 'checked' : ''}> Queue the following episodes in VLC</label>
    <div class="btns" style="margin-top:22px"><button class="btn btn-red" type="submit">Save</button>
      <button class="btn btn-ghost" type="button" data-s="cancel">Cancel</button></div>
  </form>`, 'settings-modal');
  const form = $('form', wrap);
  const values = () => {
    const f = new FormData(form);
    return {
      tmdb_api_key: f.get('tmdb_api_key'), mal_client_id: f.get('mal_client_id'), region: f.get('region'),
      netflix_url: f.get('netflix_url'), hbomax_url: f.get('hbomax_url'), fallback_url: f.get('fallback_url'),
      vlc_path: f.get('vlc_path'), browser_path: f.get('browser_path'), sync_folder: f.get('sync_folder'), queue_next_episodes: form.queue_next_episodes.checked,
    };
  };
  const save = async () => {
    const res = await api('PUT', '/api/settings', values());
    await reload();  // turning sync on can bring in titles from the other computer
    return res;
  };
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await save(); toast('Settings saved'); closeModal(wrap); } catch (err) { fail(err); }
  });
  form.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-s]')?.dataset.s;
    if (!a) return;
    try {
      if (a === 'cancel') closeModal(wrap);
      else if (a === 'show') {
        $$('input[type=password], input[data-shown]', form).forEach((i) => {
          i.dataset.shown = '1';
          i.type = i.type === 'password' ? 'text' : 'password';
        });
      } else if (a === 'vlc') {
        const { path } = await api('POST', '/api/browse', mac
          ? { kind: 'file', types: 'app', title: 'Find VLC', initial: '/Applications' }
          : { kind: 'file', types: 'exe', title: 'Find vlc.exe', initial: 'C:\\Program Files\\VideoLAN\\VLC' });
        if (path) form.vlc_path.value = path;
      } else if (a === 'browser') {
        const { path } = await api('POST', '/api/browse', mac
          ? { kind: 'file', types: 'app', title: 'Find Opera GX', initial: '/Applications' }
          : { kind: 'file', types: 'exe', title: "Find Opera GX's launcher.exe" });
        if (path) form.browser_path.value = path;
      } else if (a === 'sync-browse') {
        const { path } = await api('POST', '/api/browse', { kind: 'folder', title: 'Choose a folder both computers sync', initial: form.sync_folder.value });
        if (path) form.sync_folder.value = path;
      } else if (a === 'sync-off') {
        form.sync_folder.value = '';
        await save();
        toast('Sync turned off - your list stays on this computer');
        closeModal(wrap); openSettings();
      } else if (a === 'test') {
        const out = $('#test-out', form);
        out.textContent = 'Testing…';
        await save();
        const r = await api('POST', '/api/settings/test');
        out.innerHTML = `TMDB: ${r.tmdb ? `<span class="bad">✗ ${esc(r.tmdb)}</span>` : '<span class="ok">✓ working</span>'} &nbsp;
          MyAnimeList: ${r.mal ? `<span class="bad">✗ ${esc(r.mal)}</span>` : '<span class="ok">✓ working</span>'}`;
      }
    } catch (err) { fail(err); }
  });
}

/* ================================================================ go */

reload().catch(fail);
