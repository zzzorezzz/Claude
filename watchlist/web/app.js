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
  people: '<svg viewBox="0 0 24 24"><path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm7.5-1a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM9 13c-3.3 0-7 1.6-7 4v3h14v-3c0-2.4-3.7-4-7-4zm7.5-1c-.5 0-1 0-1.6.1 1.3.9 2.1 2.2 2.1 3.9v3h5v-2.6c0-2.3-3.2-4.4-5.5-4.4z"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M6 4l15 8-15 8z"/></svg>',
  info: '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z"/></svg>',
  more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M9 3h6l1 2h4v2H4V5h4zm-3 6h12l-1 12H7z"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M11 3h2v9.6l3.3-3.3 1.4 1.4L12 16.4l-5.7-5.7 1.4-1.4 3.3 3.3zM4 18h16v2H4z"/></svg>',
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
  library: {},         // what was found in the downloads folder
};

const DL_ORDER = ['anime', 'tv', 'movie'];
const hasFiles = (item) => !!item.local_folder || !!(S.library.items || {})[item.id];
const fmtTime = (sec) => {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600); const m = Math.floor((sec % 3600) / 60); const x = String(sec % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${x}` : `${m}:${x}`;
};
const posKey = (item, ep) => (isSeries(item) ? `${ep.season}:${ep.episode}` : 'movie');
const savedPos = (item, ep) => {
  const p = (item.positions || {})[posKey(item, ep || item.progress)];
  return p && p.pos >= 30 && !(p.dur && (p.pos / p.dur >= 0.9 || p.dur - p.pos <= 120)) ? p : null;
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
  if (S.status?.party && !data.status.party) {
    api('GET', '/api/party').then((st) => { if (st.ended) toast(`The watch party ended: ${st.ended}`); }).catch(() => {});
  }
  S.items = data.items;
  S.collections = data.collections;
  S.status = data.status;
  S.library = data.library || {};
  S.version = data.version;
  render();
  refreshOpenTitle();
  if (S.redrawQuestions) S.redrawQuestions();
  maybeAskQuestions();
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
// Pictures fade in once they've loaded, instead of popping in.
document.addEventListener('load', (e) => {
  if (e.target.tagName === 'IMG') e.target.classList.add('loaded');
}, true);
// A card's slide-in plays once, not again whenever the card is moved.
document.addEventListener('animationend', (e) => {
  if (e.target.classList?.contains('enter')) e.target.classList.remove('enter');
});

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
// Your list: everything except what you've moved to Watched (that has its own page).
const listed = () => S.items.filter((i) => !i.in_watched);
const ofType = (type) => sortItems(applyGenre(listed().filter((i) => i.type === type)));

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
  if (parts[0] === 'downloads' && TYPES.includes(parts[1])) return { name: 'downloads', type: parts[1] };
  if (parts[0] === 'find') return { name: 'find', mode: parts[1] || '' };
  if (parts[0] === 'watched') return { name: 'watched' };
  return { name: 'home' };
}

// Updating the screen: cards that didn't change are kept as they are (their
// posters stay loaded, nothing flickers); only new or changed ones are created,
// and those slide in.
const hashText = (text) => {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
  return h.toString(36);
};

function setView(view, html) {
  const old = new Map();
  view.querySelectorAll('.card[data-sig]').forEach((el) => {
    const list = old.get(el.dataset.sig) || [];
    list.push(el);
    old.set(el.dataset.sig, list);
  });
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  let fresh = 0;
  tpl.content.querySelectorAll('.card[data-sig]').forEach((el) => {
    const keep = old.get(el.dataset.sig)?.shift();
    if (keep) el.replaceWith(keep);
    else {
      el.classList.add('enter');
      el.style.setProperty('--i', String(Math.min(fresh++, 14)));
    }
  });
  view.replaceChildren(tpl.content);
  view.querySelectorAll('img').forEach((img) => { if (img.complete && img.naturalWidth) img.classList.add('loaded'); });
}
const viewTarget = { set innerHTML(html) { setView($('#view'), html); } };

function render() {
  const r = route();
  $$('.nav-links a').forEach((a) => a.classList.toggle('active', a.dataset.route === r.name && !S.query));
  renderNavButtons();
  const view = viewTarget;
  if (S.query.trim()) renderSearch(view);
  else if (r.name === 'home') renderHome(view);
  else if (TYPES.includes(r.name)) renderCategory(view, r.name);
  else if (r.name === 'downloads') renderDownloads(view, r.type);
  else if (r.name === 'find') renderFind(view, r.mode);
  else if (r.name === 'watched') renderWatched(view);
  else if (r.id) renderCollection(view, r.id);
  else renderCollections(view);
}

window.addEventListener('hashchange', () => {
  if (S.query) { S.query = ''; clearSearchBox(); }
  const go = () => { render(); window.scrollTo(0, 0); };
  // A smooth cross-fade between pages where the window supports it.
  if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.startViewTransition(go);
  } else {
    go();
    const view = $('#view');
    view.classList.remove('page-in');
    void view.offsetWidth;
    view.classList.add('page-in');
  }
});
window.addEventListener('scroll', () => $('#nav').classList.toggle('solid', window.scrollY > 30), { passive: true });

/* ============================================================== nav */

function renderNavButtons() {
  $('#btn-torrents')?.classList.toggle('hidden', !(S.status?.torrents?.count));
  $('#btn-party').classList.toggle('in-party', !!S.status?.party);
  $('#btn-party').title = S.status?.party ? 'Your watch party' : 'Join a watch party';
  const yearBtn = $('#btn-year');
  yearBtn.textContent = S.sort === 'newest' ? 'Newest ↓' : S.sort === 'oldest' ? 'Oldest ↑' : 'Popular';
  yearBtn.title = S.sort === 'popular' ? 'Most popular first - click to sort by year' : `By year, ${S.sort} first - click to change`;
  yearBtn.classList.toggle('on', S.sort !== 'popular');
  // The section links, folded into a menu when the window is narrow.
  const r = route();
  const here = $(`.nav-links a[data-route="${r.name}"]`);
  $('#browse-label').textContent = here && !S.query ? here.textContent : 'Browse';
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
$('#btn-browse').addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = $('#browse-menu');
  if (!menu.classList.contains('hidden')) { menu.classList.add('hidden'); return; }
  const r = route();
  menu.innerHTML = $$('.nav-links a').map((a) => `<button data-href="${a.getAttribute('href')}" class="${a.dataset.route === r.name && !S.query ? 'sel' : ''}">${esc(a.textContent)}</button>`).join('');
  menu.classList.remove('hidden');
});
$('#browse-menu').addEventListener('click', (e) => {
  const b = e.target.closest('[data-href]');
  if (!b) return;
  $('#browse-menu').classList.add('hidden');
  if (S.query) { S.query = ''; clearSearchBox(); }
  if (location.hash === b.dataset.href || (!location.hash && b.dataset.href === '#/')) render();
  else location.hash = b.dataset.href;
});
$('#genre-menu').addEventListener('click', (e) => {
  const b = e.target.closest('[data-genre]');
  if (!b) return;
  S.genre = b.dataset.genre;
  $('#genre-menu').classList.add('hidden');
  render();
});
document.addEventListener('click', (e) => {
  if (!e.target.closest('.dropdown')) { $('#genre-menu').classList.add('hidden'); $('#browse-menu').classList.add('hidden'); }
  if (!e.target.closest('.ctx')) closeMenu();
});

let searchTimer;
function clearSearchBox() {
  $('#search').value = '';
  $('#search').closest('.search').classList.remove('filled');
}
// Clicking the section you're already on (for example Home after searching) still
// takes you back to it: the address doesn't change, so it's done here.
$$('.nav-links a, .logo').forEach((a) => a.addEventListener('click', () => {
  if (a.getAttribute('href') === (location.hash || '#/') || (a.getAttribute('href') === '#/' && !location.hash)) {
    if (S.query) { S.query = ''; clearSearchBox(); }
    render();
    window.scrollTo(0, 0);
  }
}));

$('#search').addEventListener('input', (e) => {
  e.target.closest('.search').classList.toggle('filled', !!e.target.value);
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.query = e.target.value; render(); }, 150);
});
$('#search').addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { e.target.value = ''; S.query = ''; e.target.closest('.search').classList.remove('filled'); e.target.blur(); render(); }
});
$('#btn-settings').addEventListener('click', () => openSettings());
$('#btn-party').addEventListener('click', () => openJoinParty());
$('#btn-torrents')?.addEventListener('click', () => openTorrents());
$('#btn-surprise')?.addEventListener('click', () => openSurprise());

/* ============================================================= cards */

function cardHTML(item, opts = {}) {
  const meta = [];
  if (item.rating) meta.push(`<span class="star">★</span> ${fmtRating(item.rating)}`);
  if (item.year) meta.push(item.year);
  if (isSeries(item)) meta.push(epLabel(item.progress));
  else if (item.runtime) meta.push(fmtRuntime(item.runtime));
  const badge = !hasFiles(item) ? ''
    : item.local_broken ? '<span class="badge-pc warn">FILE ISSUE</span>' : `<span class="badge-pc">ON ${pcName().toUpperCase()}</span>`;
  const inner = `<div class="poster-fallback">${esc(item.title)}</div>
    ${item.poster ? `<img class="poster" src="${esc(item.poster)}" alt="" loading="lazy" decoding="async">` : ''}
    ${opts.resume ? resumeBarHTML(item) : badge}
    ${item.in_watched && route().name !== 'watched' ? `<span class="badge-watched">${ICON.check} WATCHED</span>` : ''}
    <div class="card-info">
      <div class="card-buttons">
        <button class="cb play" data-act="watch" title="Watch now">${ICON.play}</button>
        <button class="cb" data-act="info" title="More info">${ICON.info}</button>
        <button class="cb right" data-act="menu" title="More options">${ICON.more}</button>
      </div>
      <div class="card-title">${esc(item.title)}</div>
      <div class="card-meta">${meta.join(' · ')}</div>
    </div>`;
  return `<div class="card${opts.resume ? ' card-resume' : ''}" data-id="${esc(item.id)}" ${opts.resume ? 'data-resume="1"' : ''} data-sig="${hashText(item.id + inner)}" tabindex="0" draggable="true">${inner}</div>`;
}

// Continue watching: what you were in the middle of, most recent first.
function continueItems() {
  const cutoff = Date.now() / 1000 - 90 * 86400;
  return listed().filter((i) => i.last_played && i.last_played > cutoff && hasFiles(i)).filter((i) => {
    if (savedPos(i)) return true;
    if (!isSeries(i)) return false;
    const total = i.seasons.filter((s) => s.number > 0).reduce((n, s) => n + (s.episodes || 0), 0) || i.episodes_total || 0;
    return !total || absoluteIndex(i, i.progress) <= total;
  }).sort((a, b) => b.last_played - a.last_played).slice(0, 10);
}

function resumeBarHTML(item) {
  const pos = savedPos(item);
  const pct = pos && pos.dur ? Math.min(100, (pos.pos / pos.dur) * 100) : 0;
  const left = pos && pos.dur ? `${Math.max(1, Math.round((pos.dur - pos.pos) / 60))} min left` : '';
  return `<div class="resume-tag">${isSeries(item) ? `<b>${esc(epLabel(item.progress))}</b>` : ''}${left ? `<span>${left}</span>` : (isSeries(item) ? '<span>next up</span>' : '')}</div>
    <div class="resume-bar"><i style="width:${pct.toFixed(1)}%"></i></div>`;
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
  const sortBtn = e.target.closest('[data-sortnow]');
  if (sortBtn) { sortNow(sortBtn.dataset.sortnow === 'ai'); return; }
  const add = e.target.closest('[data-add]');
  if (add) { openAdd(add.dataset.add, '', add.dataset.col || ''); return; }
  const action = e.target.closest('[data-do]');
  if (action) { viewAction(action.dataset.do, action.dataset); return; }
  const card = e.target.closest('.card');
  if (!card) return;
  const item = findItem(card.dataset.id);
  if (!item) return;
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'watch' || (!act && card.dataset.resume)) openWatch(item);
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
  else if (name === 'share-collection') openShareCollection(data.id);
  else if (name === 'open-shared') openSharedCollection();
  else if (name === 'delete-collection') deleteCollection(data.id);
  else if (name === 'search-online') openAdd(data.type, S.query.trim());
  else if (name === 'set-downloads') chooseDownloadsFolder();
  else if (name === 'questions') openQuestions();
  else if (name === 'sort-now') sortNow();
  else if (name === 'rescan') {
    toast('Looking through your downloads…');
    api('POST', '/api/library/rescan').then(() => reload()).then(() => toast('Downloads updated')).catch(fail);
  }
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

function heroHTML(item, { resume = false } = {}) {
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
      <div class="hero-kind">${resume ? 'CONTINUE WATCHING' : TYPE_KIND[item.type]}</div>
      <h1>${esc(item.title)}</h1>
      <div class="hero-meta">${meta.join('<span>·</span>')}</div>
      <p>${esc(item.overview)}</p>
      <div class="btns">
        <button class="btn btn-white" data-do="watch" data-id="${esc(item.id)}">${ICON.play} ${watchLabel(item)}</button>
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
  const downloaded = listed().filter(hasFiles);
  // Top of the screen: what you were last watching from your downloads.
  const recent = downloaded.filter((i) => i.last_played).sort((a, b) => b.last_played - a.last_played)[0];
  const featured = recent || pickFeatured();
  const rows = TYPES.map((t) => rowHTML({
    title: TYPE_PLURAL[t], link: `#/${t}`, items: ofType(t), addType: t,
    emptyText: S.genre ? `No ${S.genre} ${TYPE_PLURAL[t].toLowerCase()} in your list.` : `Nothing here yet - add your first ${TYPE_ONE[t]}.`,
  })).join('');
  view.innerHTML = `${featured ? heroHTML(featured, { resume: featured === recent }) : ''}
    <div class="home-rows ${featured ? '' : 'no-hero'}">${setupBanner()}
    ${S.genre ? `<div class="row">${filterNote()}</div>` : ''}
    ${continueRowHTML()}
    ${downloadsSectionHTML()}
    <div class="section-head"><h2>Your list</h2><span class="muted small">everything you've added, downloaded or not</span></div>
    ${rows}</div>`;
}

function continueRowHTML() {
  const items = continueItems();
  if (!items.length || S.genre) return '';
  return `<section class="row row-continue"><div class="row-head"><h2>Continue watching</h2></div>
    <div class="row-track">${items.map((i) => cardHTML(i, { resume: true })).join('')}</div></section>`;
}

// Sort now, with the small "with AI" button sitting on top of it.
const sortButtonsHTML = (small = true) => `<span class="sort-group">
  <button class="sort-ai" data-sortnow="ai" title="Also ask your AI helper about what the rules and searches can't place">✨ with AI</button>
  <button class="btn ${small ? 'btn-sm' : ''} btn-white" data-sortnow="plain" ${S.library.sort_now?.running ? 'disabled' : ''}
    title="Sort what clearly matches your list, and search MyAnimeList / TMDB for the rest">${S.library.sort_now?.running ? 'Sorting…' : 'Sort now'}</button></span>`;

function questionsBanner() {
  const qs = S.library.questions || [];
  const waiting = S.library.waiting || 0;
  const other = qs.filter((x) => x.kind !== 'title').length;
  if (!waiting && !qs.length) return '';
  const parts = [];
  if (waiting) parts.push(`<b>${waiting} download${waiting === 1 ? '' : 's'}</b> waiting to be sorted`);
  if (other) parts.push(`${other} question${other === 1 ? '' : 's'} about episodes and subtitles`);
  return `<div class="help-banner">${parts.join(' · ') || 'Some downloads need you'}<span class="spacer"></span>
    ${waiting ? sortButtonsHTML() : ''}
    <button class="btn btn-sm btn-ghost" data-do="questions">Sort them myself</button></div>`;
}

function downloadsSectionHTML() {
  if (!S.status.downloads) {
    return `<section class="dl-setup"><div><h2>Downloads</h2>
      <p class="muted">Choose the one folder your downloads go into. The app recognises which show or movie each file is from its name, and asks you about any it isn't sure of.</p></div>
      <button class="btn btn-white" data-do="set-downloads">${ICON.folder} Choose downloads folder</button></section>`;
  }
  const lib = S.library;
  const rows = DL_ORDER.map((t) => {
    const items = sortItems(applyGenre(listed().filter((i) => i.type === t && hasFiles(i))));
    return items.length ? rowHTML({ title: TYPE_PLURAL[t], link: `#/downloads/${t}`, items, emptyText: '' }) : '';
  }).join('');
  return `<div class="section-head"><h2>Downloaded</h2>
      <span class="muted small">${lib.error ? esc(lib.error) : `${lib.videos || 0} video${lib.videos === 1 ? '' : 's'} in your downloads folder`}</span>
      <button class="btn btn-ghost btn-sm" data-do="rescan">Rescan</button></div>
    ${questionsBanner()}
    ${rows || '<div class="row"><p class="muted">Nothing in your downloads matches a title in your list yet. Add the shows you have, then press "Sort now" - nothing is sorted until you do.</p></div>'}
    <div class="section-gap"></div>`;
}

// Watched: titles you've finished, kept as your collection, out of the everyday rows.
function renderWatched(view) {
  const all = sortItems(applyGenre(S.items.filter((i) => i.in_watched)));
  const groups = TYPES.map((t) => [t, all.filter((i) => i.type === t)]).filter(([, list]) => list.length);
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>Watched</h1>
      <span class="count">${all.length} title${all.length === 1 ? '' : 's'} · ${sortLabel()}</span>${filterNote()}</div>
    <p class="hint">Everything you've watched, kept as your collection. Mark any title as watched from its ⋯ menu;
      the same menu moves it back to your list.</p>
    ${groups.length ? groups.map(([t, list]) => `<h2 class="watched-head">${TYPE_PLURAL[t]} <span class="muted small">${list.length}</span></h2>
      <div class="grid">${list.map(cardHTML).join('')}</div>`).join('')
    : `<p class="muted">${S.genre ? `No watched ${esc(S.genre)} titles.` : 'Nothing here yet. Use ⋯ → Mark as watched on any title.'}</p>`}
  </div>`;
}

function renderDownloads(view, type) {
  const items = sortItems(applyGenre(listed().filter((i) => i.type === type && hasFiles(i))));
  view.innerHTML = `<div class="page">
    <div class="page-head"><h1>Downloaded ${TYPE_PLURAL[type]}</h1>
      <span class="count">${items.length} title${items.length === 1 ? '' : 's'}</span>${filterNote()}
      <span class="spacer"></span><button class="btn btn-ghost btn-sm" data-do="rescan">Rescan</button></div>
    ${questionsBanner()}
    ${items.length ? `<div class="grid">${items.map(cardHTML).join('')}</div>` : '<p class="muted">Nothing downloaded here yet.</p>'}
  </div>`;
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
      tools: `<div class="row-tools"><button class="btn btn-ghost btn-sm" data-do="share-collection" data-id="${esc(c.id)}">${ICON.people} Share</button>
              <button class="btn btn-ghost btn-sm" data-do="rename-collection" data-id="${esc(c.id)}">Rename</button>
              <button class="btn btn-ghost btn-sm" data-do="delete-collection" data-id="${esc(c.id)}">Delete</button></div>`,
    });
  }).join('');
  view.innerHTML = `<div class="page" style="padding-left:0;padding-right:0">
    <div class="page-head" style="padding:0 var(--side)"><h1>Collections</h1>${filterNote()}<span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" data-do="open-shared">Open a shared collection…</button>
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
      <button class="btn btn-ghost btn-sm" data-do="share-collection" data-id="${esc(id)}">${ICON.people} Share</button>
      <button class="btn btn-ghost btn-sm" data-do="rename-collection" data-id="${esc(id)}">Rename</button>
      <button class="btn btn-ghost btn-sm" data-do="delete-collection" data-id="${esc(id)}">Delete</button></div>
    ${items.length ? `<div class="grid">${items.map(cardHTML).join('')}</div>`
      : '<div class="empty-state">This collection is empty. Use ⋯ on any title (or right-click it) to add it here.</div>'}
  </div>`;
}

/* ---- sharing a collection with someone else who has Watchlist */

async function openShareCollection(id) {
  const col = S.collections.find((c) => c.id === id);
  if (!col) return;
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Share "${esc(col.name)}"</h2>
    <p class="sub">Anyone with Watchlist can open it with <b>Collections → Open a shared collection…</b>, see every title, and add the collection to their own list.
      Only the titles are shared - not your files, progress or keys.</p>
    <div id="share-out"><div class="spinner"></div></div></div>`, 'share-modal');
  try {
    const r = await api('POST', `/api/collections/${id}/share`);
    $('#share-out', wrap).innerHTML = `<h4>Share code <span class="muted small">${r.count} title${r.count === 1 ? '' : 's'}</span></h4>
      <p class="muted small">Copy it and paste it into any chat.</p>
      <textarea class="input share-code" rows="4" readonly>${esc(r.code)}</textarea>
      <div class="btns"><button class="btn btn-white" data-s="copy">Copy the code</button>
        <button class="btn btn-ghost" data-s="file">Save as a file instead</button></div>
      <div id="share-file"></div>`;
  } catch (e) { $('#share-out', wrap).innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
  wrap.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-s]');
    if (!b) return;
    if (b.dataset.s === 'copy') {
      const box = $('.share-code', wrap);
      try { await navigator.clipboard.writeText(box.value); } catch { box.select(); document.execCommand('copy'); }
      b.textContent = 'Copied ✓';
    } else if (b.dataset.s === 'file') {
      b.disabled = true;
      try {
        const r = await api('POST', `/api/collections/${id}/save`);
        $('#share-file', wrap).innerHTML = `<p class="ok small">Saved to ${esc(r.path)} - send that file to them (it's opened for you).</p>`;
      } catch (err) { fail(err); }
      b.disabled = false;
    }
  });
}

function openSharedCollection() {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Open a shared collection</h2>
    <p class="sub">Paste the share code you were sent (it starts with WLC1.), or choose the file.</p>
    <textarea class="input shared-paste" rows="3" placeholder="WLC1.…"></textarea>
    <div class="btns"><button class="btn btn-white" data-s="peek" disabled>Open</button>
      <button class="btn btn-ghost" data-s="choose">Choose a file…</button></div>
    <div id="shared-view"></div></div>`, 'shared-modal');
  const paste = $('.shared-paste', wrap);
  let source = {};
  paste.addEventListener('input', () => { $('[data-s="peek"]', wrap).disabled = !paste.value.trim(); });
  const view = async () => {
    const out = $('#shared-view', wrap);
    out.innerHTML = '<div class="spinner"></div>';
    try {
      const data = await api('POST', '/api/shared/peek', source);
      const fresh = data.items.filter((x) => !x.in_list).length;
      out.innerHTML = `<div class="shared-head"><h3>${esc(data.name)}</h3>
          <span class="muted small">${data.items.length} title${data.items.length === 1 ? '' : 's'}${data.by ? ` · shared by ${esc(data.by)}` : ''} · ${fresh} not in your list</span></div>
        <div class="shared-grid">${data.items.map((x) => `<div class="shared-card${x.in_list ? ' in' : ''}">
          <div class="shared-poster"><span>${esc(x.title)}</span>${x.poster ? `<img src="${esc(x.poster)}" alt="" loading="lazy">` : ''}</div>
          <b title="${esc(x.title)}">${esc(x.title)}</b>
          <small>${[{ tv: 'TV show', anime: 'Anime', movie: 'Movie' }[x.type], x.year, x.rating ? `★ ${fmtRating(x.rating)}` : ''].filter(Boolean).map(esc).join(' · ')}</small>
          ${x.in_list ? `<small class="ok">${ICON.check} In your list</small>` : ''}</div>`).join('')}</div>
        <div class="btns"><button class="btn btn-red" data-s="import">Add it to my Watchlist</button></div>
        <p class="muted small">Adds a collection called "${esc(data.name)}" to your Collections${fresh ? `, and adds the ${fresh} title${fresh === 1 ? '' : 's'} you don't have yet to your list` : ' (every title in it is in your list already)'}.</p>`;
    } catch (e) { out.innerHTML = `<div class="warn">${esc(e.message)}</div>`; }
  };
  wrap.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-s]');
    if (!b) return;
    if (b.dataset.s === 'peek') { source = { code: paste.value }; view(); }
    else if (b.dataset.s === 'choose') {
      try {
        const r = await api('POST', '/api/browse', { kind: 'file', title: 'Choose the shared collection file', types: 'collection' });
        if (r.path) { source = { file: r.path }; view(); }
      } catch (err) { fail(err); }
    } else if (b.dataset.s === 'import') {
      b.disabled = true;
      b.textContent = 'Adding…';
      try {
        const r = await api('POST', '/api/shared/import', source);
        closeModal(wrap);
        await reload();
        toast(`Added "${r.collection.name}" - ${r.titles} title${r.titles === 1 ? '' : 's'}${r.failed ? ` (${r.failed} couldn't be found)` : ''}`);
        location.hash = `#/collections/${r.collection.id}`;
      } catch (err) { fail(err); b.disabled = false; b.textContent = 'Add it to my Watchlist'; }
    }
  });
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
    <button data-m="watched">${item.in_watched ? '↩&nbsp; Move back to my list' : '✓&nbsp; Mark as watched'}</button>
    ${maybeAnimeOnTmdb(item) ? '<button data-m="use-mal">⇄&nbsp; Use MyAnimeList info</button>' : ''}
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
      } else if (m === 'watched') await toggleWatched(item.id);
      else if (m === 'use-mal') openUseMal(findItem(item.id));
      else if (m === 'remove') removeItem(item.id);
    } catch (err) { fail(err); }
  });
  placeMenu(menu, anchor);
}

// Anime always come from MyAnimeList; one that came from TMDB can be switched.
const animeOnTmdb = (item) => item.source === 'tmdb'
  && (item.type === 'anime' || ((item.genres || []).includes('Animation') && item.language === 'ja'));
const maybeAnimeOnTmdb = (item) => item.source === 'tmdb' && (item.type === 'anime' || (item.genres || []).includes('Animation'));

async function openUseMal(item) {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Use MyAnimeList for ${esc(item.title)}</h2>
    <p class="sub">Its details come from TMDB now. MyAnimeList's seasons and episode numbers match how anime are released and named.
      Your files, where you're up to, folders and collections are kept${item.type === 'anime' ? '' : ', and it moves to Anime'}.</p>
    <div id="mal-matches">${'<div class="rel-row skeleton"><div class="sk-poster"></div><div class="sk-lines"><i></i><i></i></div></div>'.repeat(3)}</div></div>`, 'mal-modal');
  let rows = [];
  try { rows = (await api('GET', `/api/items/${item.id}/mal-matches`)).results; } catch (e) { $('#mal-matches', wrap).innerHTML = `<div class="warn">${esc(e.message)}</div>`; return; }
  $('#mal-matches', wrap).innerHTML = rows.length ? rows.map((r, i) => `<div class="mal-row">
      ${r.poster ? `<img src="${esc(r.poster)}" alt="" loading="lazy">` : '<span class="rel-ph"></span>'}
      <div><b>${esc(r.title)}</b><small>${[kindName(r), r.year, r.episodes_total ? `${r.episodes_total} episodes` : ''].filter(Boolean).map(esc).join(' · ')}</small>
        ${r.in_list ? '<small class="muted">Already in your list - the two will become one.</small>' : ''}</div>
      <button class="btn btn-sm ${i ? 'btn-ghost' : 'btn-white'}" data-use="${i}">Use this</button></div>`).join('')
    : '<p class="muted">Nothing found on MyAnimeList under this name.</p>';
  wrap.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-use]');
    if (!b) return;
    b.disabled = true;
    b.textContent = 'Switching…';
    try {
      const updated = await api('POST', `/api/items/${item.id}/use-mal`, { mal_id: rows[Number(b.dataset.use)].source_id });
      closeModal(wrap);
      await reload();
      toast(`${updated.title} now uses MyAnimeList`);
      refreshOpenTitle();
      if (updated.id !== item.id) openTitle(updated.id);
    } catch (err) { fail(err); b.disabled = false; b.textContent = 'Use this'; }
  });
}

async function toggleWatched(id) {
  const item = findItem(id);
  const watched = !item.in_watched;
  upsert(await api('PUT', `/api/items/${id}`, { watched }));
  render(); refreshOpenTitle();
  toast(watched ? `${item.title} moved to Watched` : `${item.title} is back in your list`);
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

/* ==================================================== downloads folder */

function watchLabel(item) {
  const pos = savedPos(item);
  if (isSeries(item)) return pos ? `Resume ${epLabel(item.progress)} · ${fmtTime(pos.pos)}` : `Watch ${epLabel(item.progress)}`;
  return pos ? `Resume · ${fmtTime(pos.pos)}` : 'Watch now';
}

async function chooseDownloadsFolder() {
  try {
    const { path } = await api('POST', '/api/browse', { kind: 'folder', title: 'Choose your downloads folder', initial: S.status.downloads || '' });
    if (!path) return;
    await api('PUT', '/api/settings', { downloads_folder: path });
    await reload();
    const n = Object.keys(S.library.items || {}).length;
    toast(`Downloads folder set - matched files to ${n} title${n === 1 ? '' : 's'}`);
    if ((S.library.questions || []).length) openQuestions();
  } catch (e) { fail(e); }
}

// New files the app isn't sure about: ask once, without nagging about the same ones.
// New downloads wait in the banner above your downloads; nothing pops up by itself.
function maybeAskQuestions() {}

function questionGuessType(q) {
  if (q.files.some((f) => /^\[|\s-\s\d/.test(f.split(/[\\/]/).pop()))) return 'anime';
  return q.files.some((f) => /s\d{1,2}e\d|episode|\bep\s?\d/i.test(f)) ? 'tv' : 'movie';
}

function openQuestions() {
  if ($$('#modal-root .questions-modal').length) return;
  const wrap = openModal('', 'questions-modal');
  const draw = () => {
    const qs = S.library.questions || [];
    const titleOpts = [...S.items].sort((a, b) => a.title.localeCompare(b.title))
      .map((i) => `<option value="${esc(i.id)}">${esc(i.title)} (${TYPE_ONE[i.type]})</option>`).join('');
    const fileList = (files) => `<div class="q-files">${files.slice(0, 3).map((f) => `<div>${esc(f)}</div>`).join('')}
      ${files.length > 3 ? `<div class="muted">…and ${files.length - 3} more</div>` : ''}</div>`;
    const suggestions = (q) => (q.suggest || []).length ? `<div class="q-suggest">
        <span class="muted small">${(q.suggest[0].why || '').includes('AI') ? 'Found with AI' : 'Found online'} - add it to your list and sort these files there:</span>
        <div class="q-chips">${q.suggest.map((x, n) => `<button class="q-chip" data-a="suggest" data-s="${n}" title="${esc(x.relation || x.why || '')}">
          ${x.poster ? `<img src="${esc(x.poster)}" alt="" loading="lazy">` : '<span class="q-chip-ph"></span>'}
          <span><b>${esc(x.title)}</b><small>${[x.year, kindName(x)].filter(Boolean).map(esc).join(' · ')}${x.relation ? ` · ${esc(x.relation)}` : ''}</small></span>
          <i>+</i></button>`).join('')}</div></div>` : '';
    const card = (q, i) => {
      if (q.kind === 'title') {
        const cands = q.candidates.map(findItem).filter(Boolean);
        const hinted = (q.match && findItem(q.match)) || (q.hint_item && findItem(q.hint_item));
        return `<div class="q-card" data-q="${i}">
          <div class="q-head"><b>"${esc(q.guess || 'no name')}"${q.season ? ` · season ${q.season}` : ''}</b>
            <span class="muted small">${q.files.length} file${q.files.length === 1 ? '' : 's'}${q.file_kind && q.file_kind !== 'episode' ? ` · looks like ${q.file_kind === 'ova' ? 'an OVA' : `a ${q.file_kind}`}` : ''}</span>
            ${q.reason === 'held' ? `<span class="q-tag">${q.was ? `you took ${q.files.length === 1 ? 'it' : 'these'} out of ${esc(q.was)}` : 'kept out by you'}</span>`
              : q.reason === 'undone' ? '<span class="q-tag">you undid this - Sort now leaves it to you</span>'
              : q.reason === 'ignore' ? '<span class="q-tag">you ignored this name before</span>' : ''}</div>
          ${fileList(q.files)}
          ${q.hint ? `<div class="q-hint">✨ ${esc(q.hint)}</div>` : ''}
          ${suggestions(q)}
          <div class="q-actions">
            ${hinted ? `<button class="btn btn-sm btn-white" data-a="item" data-item="${esc(hinted.id)}">Yes, it's ${esc(hinted.title)}</button>` : ''}
            ${cands.filter((c) => c !== hinted).map((c) => `<button class="btn btn-sm ${hinted ? 'btn-ghost' : 'btn-white'}" data-a="item" data-item="${esc(c.id)}">It's ${esc(c.title)}</button>`).join('')}
            <select class="select q-pick"><option value="">${cands.length ? 'Another title…' : 'It\'s a title in my list…'}</option>${titleOpts}</select>
            <button class="btn btn-sm btn-ghost" data-a="add">+ Add it to my list</button>
            <button class="btn btn-sm btn-ghost" data-a="ignore">Ignore</button>
          </div></div>`;
      }
      if (q.kind === 'episode') {
        const item = findItem(q.item);
        return `<div class="q-card" data-q="${i}">
          <div class="q-head"><b>Which episode of ${esc(item ? item.title : 'this show')} is this?</b></div>
          ${fileList(q.files)}
          <div class="q-actions"><span class="muted small">Season</span><input class="input q-num q-season" type="number" min="1" value="1">
            <span class="muted small">Episode</span><input class="input q-num q-episode" type="number" min="1" placeholder="#">
            <button class="btn btn-sm btn-white" data-a="episode">Save</button>
            <button class="btn btn-sm btn-ghost" data-a="ignore-file">It's an extra - ignore it</button></div></div>`;
      }
      return `<div class="q-card" data-q="${i}">
        <div class="q-head"><b>Which video is this subtitle for?</b></div>
        ${fileList(q.files)}
        <div class="q-actions"><select class="select q-video"><option value="">Choose the video…</option>
          ${(q.choices || []).map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select>
          <button class="btn btn-sm btn-white" data-a="subtitle">Save</button>
          <button class="btn btn-sm btn-ghost" data-a="ignore-sub">Ignore</button></div></div>`;
    };
    const indexed = qs.map((q, i) => [q, i]);
    const ready = indexed.filter(([q]) => q.kind === 'title' && ['sure', 'known'].includes(q.reason));
    const held = indexed.filter(([q]) => q.kind === 'title' && ['held', 'undone', 'ignore'].includes(q.reason));
    const unsure = indexed.filter(([q]) => q.kind === 'title' && q.reason === 'unsure');
    const episodes = indexed.filter(([q]) => q.kind === 'episode');
    const subs = indexed.filter(([q]) => q.kind === 'subtitle');
    const unknown = indexed.filter(([q]) => q.reason === 'unknown');
    const section = (title, list) => (list.length ? `<h4>${title}</h4>${list.map(([q, i]) => card(q, i)).join('')}` : '');
    const auto = [...(S.library.auto || [])].reverse().slice(0, 25);
    const sortedForYou = auto.length ? `<details class="auto-list" ${qs.length ? '' : 'open'}>
      <summary><b>Sorted for you (${auto.length})</b> - by Sort now; undo anything that's wrong</summary>
      ${auto.map((x) => `<div class="auto-row"><span class="auto-by ${['search', 'rules'].includes(x.by) ? 'search' : 'ai'}">${AI_NAME[x.by] || (x.by === 'rules' ? 'Name' : 'Search')}</span>
        <span class="auto-what"><b>${esc(x.label || x.key)}</b>${x.why ? `<small>${esc(x.why)}</small>` : ''}</span>
        <span class="muted small">${esc(ago(x.at))}</span>
        <button class="btn btn-sm btn-ghost" data-undo="${esc(x.key)}">Undo</button></div>`).join('')}</details>` : '';
    const busy = S.library.busy ? `<div class="sorting-now"><i></i>${esc(S.library.busy)}</div>` : '';
    $('.modal', wrap).innerHTML = `<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
      <div class="q-title"><h2>Sort your downloads</h2>
        ${(S.library.waiting || 0) ? sortButtonsHTML(false) : ''}</div>
      <p class="sub">Nothing is sorted until you say so. Pick where files belong here, or press <b>Sort now</b>: it sorts what clearly matches your list and searches MyAnimeList / TMDB for the rest (<b>with AI</b> also asks your AI helper). Files you've sorted, or taken out of a title, are never changed.</p>
      ${sortNowReport()}
      ${busy}
      ${S.library.gemini_error ? `<div class="warn small">AI helper: ${esc(S.library.gemini_error)}</div>` : ''}
      ${!qs.length ? '<div class="empty-state">All sorted - nothing needs your help. 🎉</div>' : ''}
      ${section('Ready to sort - these clearly match your list', ready)}
      ${section('Not sure which title these are', unsure)}
      ${section('Which episode?', episodes)}
      ${section('Subtitles without a video', subs)}
      ${unknown.length ? `<details ${unsure.length + episodes.length + subs.length ? '' : 'open'}><summary><b>Not in your list (${unknown.length})</b> - add them, point them at a title, or ignore them</summary>
        ${unknown.map(([q, i]) => card(q, i)).join('')}</details>` : ''}
      ${held.length ? `<details><summary><b>Kept out by you (${held.length})</b> - Sort now leaves these alone</summary>
        ${held.map(([q, i]) => card(q, i)).join('')}</details>` : ''}
      ${sortedForYou}
    </div>`;
  };
  // Redrawn when the background sorter changes something, unless you're typing.
  let lastSig = '';
  const sig = () => JSON.stringify([(S.library.questions || []).map((q) => [q.key, q.reason, q.files.length, q.hint, (q.suggest || []).length]),
    (S.library.auto || []).length, S.library.busy, S.library.gemini_error, S.library.sort_now]);
  S.redrawQuestions = () => {
    if (!wrap.isConnected) { S.redrawQuestions = null; return; }
    const active = document.activeElement;
    if (sig() === lastSig || (active && wrap.contains(active) && /INPUT|SELECT/.test(active.tagName))) return;
    lastSig = sig();
    draw();
  };
  // Answering doesn't reload the whole app: the answer brings back the new
  // downloads state, and the answered card slides away.
  const answer = async (body, card) => {
    const request = api('POST', '/api/library/answer', body);
    if (card) {
      card.style.height = `${card.offsetHeight}px`;
      card.classList.add('leaving');
    }
    S.library = await request;
    await new Promise((r) => setTimeout(r, card ? 220 : 0));
    render();
    if (wrap.isConnected) draw();
  };
  wrap.addEventListener('change', (e) => {
    const pick = e.target.closest('.q-pick');
    if (!pick || !pick.value) return;
    const q = S.library.questions[Number(pick.closest('[data-q]').dataset.q)];
    answer({ kind: 'title', key: q.key, item: pick.value }, pick.closest('.q-card')).catch(fail);
  });
  wrap.addEventListener('click', async (e) => {
    const sortBtn = e.target.closest('[data-sortnow]');
    if (sortBtn) { sortNow(sortBtn.dataset.sortnow === 'ai'); return; }
    const undo = e.target.closest('[data-undo]');
    if (undo) {
      const row = undo.closest('.auto-row');
      row.style.height = `${row.offsetHeight}px`;
      row.classList.add('leaving');
      try {
        S.library = await api('POST', '/api/library/undo', { key: undo.dataset.undo });
        await reload();
        toast('Undone - it\'s back in your questions');
      } catch (err) { fail(err); }
      return;
    }
    const btn = e.target.closest('[data-a]');
    if (!btn) return;
    const box = btn.closest('[data-q]');
    const q = S.library.questions[Number(box.dataset.q)];
    const a = btn.dataset.a;
    try {
      if (a === 'item') await answer({ kind: 'title', key: q.key, item: btn.dataset.item }, box);
      else if (a === 'ignore') await answer({ kind: 'title', key: q.key, item: 'ignore' }, box);
      else if (a === 'ignore-file') await answer({ kind: 'file', key: q.key, item: 'ignore' }, box);
      else if (a === 'ignore-sub') await answer({ kind: 'subtitle', key: q.key, video: 'ignore' }, box);
      else if (a === 'episode') {
        const episode = Number($('.q-episode', box).value);
        if (!episode) { toast('Type the episode number first', 'error'); return; }
        await answer({ kind: 'episode', key: q.key, item: q.item, season: Number($('.q-season', box).value) || 1, episode }, box);
      } else if (a === 'subtitle') {
        const video = $('.q-video', box).value;
        if (!video) { toast('Choose the video first', 'error'); return; }
        await answer({ kind: 'subtitle', key: q.key, video }, box);
      } else if (a === 'suggest') {
        const x = q.suggest[Number(btn.dataset.s)];
        btn.disabled = true;
        btn.classList.add('busy');
        await answer({ kind: 'title', key: q.key, add: { type: x.type, source: x.source, source_id: x.source_id } }, box);
        await reload();
        toast(`Added ${x.title} and sorted ${q.files.length} file${q.files.length === 1 ? '' : 's'}`);
      } else if (a === 'add') {
        openAdd(questionGuessType(q), q.guess, '', (item) => {
          answer({ kind: 'title', key: q.key, item: item.id }).catch(fail);
        });
      }
    } catch (err) { fail(err); }
  });
  lastSig = sig();
  draw();
}

// Sort now: search for and ask the AI about every waiting download, and follow along.
async function sortNow(withAi = false) {
  if (S.library.sort_now?.running) return;
  try {
    S.library = await api('POST', '/api/library/sort', { ai: withAi });
    S.library.sort_now = { ...(S.library.sort_now || {}), running: true };
    if ($$('#modal-root .questions-modal').length && S.redrawQuestions) S.redrawQuestions(); else openQuestions();
    for (;;) {
      await new Promise((r) => setTimeout(r, 1200));
      S.library = await api('GET', '/api/library');
      if (S.redrawQuestions) S.redrawQuestions();
      if (!S.library.sort_now?.running) break;
    }
    await reload();
    const r = S.library.sort_now?.last;
    if (r) toast(r.sorted ? `Sorted ${r.sorted} group${r.sorted === 1 ? '' : 's'} of downloads${r.left ? ` · ${r.left} still need you` : ''}`
      : r.error ? `Nothing more sorted: ${r.error}` : 'Nothing more could be sorted for sure - the rest need you', r.sorted || !r.error ? '' : 'error');
  } catch (err) { fail(err); }
}

function sortNowReport() {
  const r = S.library.sort_now?.last;
  if (!r || S.library.sort_now.running || Date.now() / 1000 - r.at > 600) return '';
  const parts = [r.sorted ? `<b>Sorted ${r.sorted} group${r.sorted === 1 ? '' : 's'}</b>${r.ai ? ` (with ${esc(r.ai)})` : ''}` : 'Nothing more could be sorted for sure'];
  if (r.suggested) parts.push(`${r.suggested} with a suggestion below`);
  return `<div class="sortnow-report ${r.error && !r.sorted ? 'warn' : ''}">${parts.join(' · ')}${r.error ? ` - ${esc(r.error)}` : ''}</div>`;
}

function kindName(x) {
  const k = (x.media_kind || x.media_type || '').toLowerCase().replace(/_/g, ' ');
  if (k === 'tv' || k === 'ona') return x.type === 'anime' ? 'TV anime' : 'TV';
  if (k) return { ova: 'OVA', special: 'Special', 'tv special': 'TV special', movie: 'Film', music: 'Music video' }[k] || k.toUpperCase();
  return x.type === 'movie' ? 'Film' : x.type === 'tv' ? 'TV' : '';
}

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - (t || 0));
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function aiKeyRow(s, st, provider, label, field, help) {
  const rest = (st.ai_resting || {})[provider];
  return `<label class="field"><span>${label}</span>
    <div class="input-row"><input class="input" name="${field}" type="password" value="${esc(s[field] || '')}" placeholder="Optional - paste a key to use it">
    <button class="btn btn-ghost btn-sm" type="button" data-s="ai-test" data-p="${provider}" data-field="${field}">Test</button></div>
    <small id="ai-out-${provider}">${rest ? `<span class="bad">Resting for ${Math.max(1, Math.round(rest[0] / 60))} min: ${esc(rest[1])}.</span> The others are used meanwhile; Test tries it again now. `
      : s[field] ? '<span class="ok">✓ Key added.</span> ' : ''}${help}</small></label>`;
}

// Saves the key and checks it answers. Always ends with a clear result.
async function testAiKey(form, btn) {
  const provider = btn.dataset.p;
  const out = $(`#ai-out-${provider}`, form);
  const started = Date.now();
  const tick = () => { out.innerHTML = `<span class="testing"><i></i>Testing… ${Math.round((Date.now() - started) / 1000)}s</span> (a slow model is skipped for the next one)`; };
  tick();
  const timer = setInterval(tick, 1000);
  btn.disabled = true;
  const tried = (r) => (r.tried && r.tried.length > 1
    ? `<details class="ai-tried"><summary>What was tried</summary>${r.tried.map((t) => `<div>${esc(t)}</div>`).join('')}</details>` : '');
  try {
    const r = await api('POST', '/api/settings/ai', { provider, key: form[btn.dataset.field].value.trim() });
    out.innerHTML = r.ok
      ? `<span class="ok">✓ Working</span> - using ${esc(r.model)}. Saved.${tried(r)}`
      : `<span class="bad">✗ ${esc(r.error)}</span>${{
        key: ' Check you copied the whole key.',
        credit: '',
        region: '',
        daily: ' The key is saved; the other helpers are used until it comes back.',
        limit: ' The key is saved; the app tries again later by itself.',
        busy: ' The key is saved; the app tries again later by itself.',
        timeout: ' The key is saved; the app tries again later by itself.',
        offline: ' The key is saved; the app tries again once it can connect.',
      }[r.kind] || ''}${tried(r)}`;
    S.status = (await api('GET', '/api/settings')).status;
  } catch (err) {
    out.innerHTML = `<span class="bad">✗ ${esc(err.message)}</span>`;
  } finally {
    clearInterval(timer);
    btn.disabled = false;
  }
}

const AI_NAME = { gemini: 'Gemini', groq: 'Groq', openrouter: 'OpenRouter', openai: 'ChatGPT', xai: 'Grok' };

/* ======================================================== Find Media */

const THIS_YEAR = new Date().getFullYear();
S.find = {
  top: { type: 'anime', year: THIS_YEAR, order: 'popular', results: null, error: '' },
  search: { types: [], q: '', genres: [], year: '', sort: 'popular', format: '', results: null, page: 1, more: false, error: '', notes: [], meta: null, panel: false },
  mood: { messages: [], result: null, error: '', busy: false },
};
const FIND_TABS = [['top', 'Top by year'], ['search', 'Search & browse'], ['mood', "I'm in the mood for…"]];
const KIND_LABEL = { tv: 'TV shows', movie: 'Movies', anime: 'Anime' };
const resultType = (r) => (r.source === 'mal' ? 'anime' : String(r.source_id).startsWith('movie/') ? 'movie' : 'tv');
const findKind = (r) => kindName({ ...r, type: r.type || resultType(r) });

function renderFind(view, mode) {
  // Already showing this tab (the window refreshes now and then): only update what's in your list.
  const page = $('#view .find-page');
  if (page && page.dataset.mode === mode) { refreshFindCards(); return; }
  view.innerHTML = `<div class="page find-page" data-mode="${mode}">
    <div class="page-head"><h1>Find Media</h1>
      <span class="muted">New things to watch, from TMDB and MyAnimeList${(S.status.ai || []).length ? ' - and your AI helper' : ''}</span></div>
    ${mode ? `<nav class="find-tabs">${FIND_TABS.map(([k, l]) => `<a href="#/find/${k}" class="${k === mode ? 'on' : ''}">${l}</a>`).join('')}</nav>` : ''}
    <div id="find-body"></div></div>`;
  drawFind(mode);
}

function drawFind(mode) {
  const body = $('#find-body');
  if (!body) return;
  if (!mode) {
    body.innerHTML = `<div class="find-start">
      <a class="find-tile" href="#/find/top"><span class="find-icon">🏆</span><b>Top by year</b><span>The 100 most popular or highest rated TV shows, movies or anime of any year.</span></a>
      <a class="find-tile" href="#/find/search"><span class="find-icon">🔎</span><b>Search & browse</b><span>Keywords, genres and types across TMDB and MyAnimeList, sorted by popularity, rating, box office or date.</span></a>
      <a class="find-tile mood" href="#/find/mood"><span class="find-icon">✨</span><b>I'm in the mood for…</b><span>Tell the AI what you feel like watching. It may ask a question or two, then picks from your list and new things.</span></a></div>`;
    return;
  }
  if (mode === 'top') drawFindTop(body);
  else if (mode === 'search') drawFindSearch(body);
  else drawFindMood(body);
}

const segmented = (name, options, value) => `<div class="seg" data-seg="${name}">${options.map(([k, l]) => `<button class="${String(k) === String(value) ? 'on' : ''}" data-v="${esc(k)}">${esc(l)}</button>`).join('')}</div>`;

function findCardHTML(r, list, i, rank) {
  const meta = [r.year, findKind(r), r.rating ? `★ ${fmtRating(r.rating)}` : ''].filter(Boolean);
  return `<div class="find-card" data-list="${list}" data-i="${i}" data-in="${esc(r.in_list || '')}" style="--i:${Math.min(i, 20)}">
    <div class="find-poster"><span>${esc(r.title)}</span>${r.poster ? `<img src="${esc(r.poster)}" alt="" loading="lazy" decoding="async">` : ''}
      ${rank ? `<span class="find-rank">${rank}</span>` : ''}</div>
    <div class="find-info"><b title="${esc(r.title)}">${esc(r.title)}</b><small>${meta.map(esc).join(' · ')}</small>
      ${r.why ? `<p class="find-why">${esc(r.why)}</p>` : ''}
      ${r.in_list ? `<button class="find-add in" data-f="open">${ICON.check} In your list</button>`
    : `<button class="find-add" data-f="add">+ Add</button>`}</div></div>`;
}

function findList(list) {
  const st = S.find;
  return { top: st.top.results, search: st.search.results, 'mood-new': st.mood.result?.new, 'mood-list': st.mood.result?.from_list }[list] || [];
}

function refreshFindCards() {
  $$('#find-body .find-card').forEach((el) => {
    const r = findList(el.dataset.list)[Number(el.dataset.i)];
    if (!r) return;
    const mine = r.id && findItem(r.id) ? r.id : (S.items.find((x) => x.source === r.source && x.source_id === String(r.source_id)) || {}).id;
    r.in_list = mine || null;
    if ((mine || '') !== el.dataset.in) {
      const i = Number(el.dataset.i);
      el.outerHTML = findCardHTML(r, el.dataset.list, i, el.dataset.list === 'top' ? i + 1 : 0);
    }
  });
}

// Only the newest request counts (you may switch type or year before an answer comes back).
const findShowing = (mode) => { const r = route(); return r.name === 'find' && r.mode === mode && !S.query.trim(); };

async function findTopLoad() {
  const st = S.find.top;
  const ticket = (st.ticket = (st.ticket || 0) + 1);
  Object.assign(st, { results: null, error: '', loading: true });
  if (findShowing('top')) drawFind('top');
  let results = [], error = '';
  try {
    results = (await api('GET', `/api/find/top?type=${st.type}&year=${st.year}&order=${st.order}`)).results;
  } catch (e) { error = e.message; }
  if (ticket !== st.ticket) return;
  Object.assign(st, { results, error, loading: false });
  if (findShowing('top')) drawFind('top');
}

function drawFindTop(body) {
  const st = S.find.top;
  const years = Array.from({ length: THIS_YEAR + 2 - 1960 }, (_, i) => THIS_YEAR + 1 - i);
  body.innerHTML = `<div class="find-controls">
      ${segmented('top-type', Object.entries(KIND_LABEL), st.type)}
      <select class="select" data-f="top-year">${years.map((y) => `<option ${y === st.year ? 'selected' : ''}>${y}</option>`).join('')}</select>
      ${segmented('top-order', [['popular', 'Most popular'], ['rated', 'Highest rated']], st.order)}
    </div>
    ${st.error ? `<div class="warn">${esc(st.error)}</div>` : ''}
    ${st.results === null ? `<div class="find-grid">${'<div class="find-card skeleton"><div class="find-poster sk-poster"></div><div class="sk-lines"><i></i><i></i></div></div>'.repeat(12)}</div>`
    : st.results.length ? `<p class="muted small">${st.order === 'rated' ? 'Highest rated' : 'Most popular'} ${{ tv: 'TV shows', movie: 'movies', anime: 'anime' }[st.type]} of ${st.year}: ${st.results.length}</p>
      <div class="find-grid">${st.results.map((r, i) => findCardHTML(r, 'top', i, i + 1)).join('')}</div>`
      : st.error ? '' : '<p class="muted">Nothing found for that year.</p>'}`;
  if (st.results === null && !st.loading) findTopLoad();
}

async function findSearchLoad(more = false, quiet = false) {
  const st = S.find.search;
  const ticket = (st.ticket = (st.ticket || 0) + 1);
  st.error = '';
  if (!more) { st.page = 1; if (!quiet) { st.results = null; st.notes = []; } }
  st.loading = true;
  if (findShowing('search') && !quiet) drawFind('search');
  const q = new URLSearchParams({ types: st.types.join(','), q: st.q, genres: st.genres.join(','), year: st.year, sort: st.sort, format: st.format, page: st.page });
  try {
    const r = await api('GET', `/api/find/search?${q}`);
    if (ticket !== st.ticket) return;
    st.results = more ? [...(st.results || []), ...r.results] : r.results;
    st.more = r.more;
    st.notes = r.notes || [];
    // While MyAnimeList's anime are still being collected, look again in a while
    // (only if you haven't moved on or loaded more).
    if (r.building && !more) {
      setTimeout(() => { if (st.ticket === ticket && st.page === 1 && findShowing('search')) findSearchLoad(false, true); }, 15000);
    }
  } catch (e) {
    if (ticket !== st.ticket) return;
    st.error = e.message;
    if (more) st.page -= 1; else st.results = [];
  }
  st.loading = false;
  if (findShowing('search')) drawFind('search');
}

async function findSearchMeta() {
  const st = S.find.search;
  try { st.meta = await api('GET', '/api/find/genres'); } catch (e) { st.metaError = e.message; }
}

// The genres to choose from: one type's own, or those for several types at once.
function searchGenreList() {
  const st = S.find.search;
  if (!st.meta) return [];
  if (st.types.length === 1) return st.meta[st.types[0]];
  const kinds = st.types.length ? st.types : ['tv', 'anime', 'movie'];
  return st.meta.all.filter((g) => g.types.some((t) => kinds.includes(t)));
}

const genreName = (key) => {
  const meta = S.find.search.meta;
  if (!meta) return key;
  for (const list of [meta.all, meta.movie, meta.tv, meta.anime]) {
    const g = list.find((x) => x.key === key);
    if (g) return g.name;
  }
  return key;
};

function genreBarHTML() {
  const st = S.find.search;
  return `<div class="genre-bar">
      <button class="btn btn-sm ${st.panel ? 'btn-white' : 'btn-ghost'}" data-f="genre-panel">Genres${st.genres.length ? ` (${st.genres.length})` : ''} <span class="caret">${st.panel ? '▴' : '▾'}</span></button>
      ${st.genres.map((k) => `<span class="chip on">${esc(genreName(k))}<button data-f="genre-off" data-key="${esc(k)}" aria-label="Remove">×</button></span>`).join('')}
      ${st.genres.length ? '<button class="linkish" data-f="genre-clear">Clear</button>' : '<span class="muted small">any genre</span>'}
    </div>`;
}

function drawFindSearch(body) {
  const st = S.find.search;
  if (!st.meta && !st.metaError) {
    body.innerHTML = '<div class="spinner"></div>';
    findSearchMeta().then(() => {
      if (st.results === null && !st.loading) findSearchLoad();     // start with what's popular
      else if (findShowing('search')) drawFind('search');
    });
    return;
  }
  const years = Array.from({ length: THIS_YEAR + 2 - 1900 }, (_, i) => THIS_YEAR + 1 - i);
  const all = !st.types.length;
  const sorts = (st.meta?.sorts || [['popular', 'Most popular']]).filter(([k]) => k !== 'grossing' || all || st.types.includes('movie'));
  const showFormat = all || st.types.includes('anime');
  const list = searchGenreList();
  const groups = [];
  for (const g of list) {
    let grp = groups.find((x) => x.name === g.group);
    if (!grp) groups.push((grp = { name: g.group, items: [] }));
    grp.items.push(g);
  }
  const typeBtns = Object.entries(KIND_LABEL).map(([k, l]) => `<button type="button" class="${st.types.includes(k) ? 'on' : ''}" data-type="${k}">${l}</button>`).join('');
  body.innerHTML = `<form class="find-controls" data-f="search-form">
      <div class="seg multi" title="Choose one or more. None chosen searches everything.">${typeBtns}</div>
      <span class="muted small">${all ? 'All types' : ''}</span>
      <input class="input find-q" name="q" value="${esc(st.q)}" placeholder="Keywords (optional)" autocomplete="off">
      <select class="select" name="year"><option value="">Any year</option>${years.map((y) => `<option ${String(y) === String(st.year) ? 'selected' : ''}>${y}</option>`).join('')}</select>
      ${showFormat ? `<select class="select" name="format" title="Anime format">${(st.meta?.formats || []).map(([k, l]) => `<option value="${k}" ${k === st.format ? 'selected' : ''}>${k ? `Anime: ${l}` : 'Any anime format'}</option>`).join('')}</select>` : ''}
      <select class="select" name="sort">${sorts.map(([k, l]) => `<option value="${k}" ${k === st.sort ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <button class="btn btn-red" type="submit">Search</button>
    </form>
    ${genreBarHTML()}
    ${st.panel ? `<div class="genre-panel">
      <input class="input genre-filter" placeholder="Find a genre…" autocomplete="off">
      ${st.metaError ? `<div class="warn">${esc(st.metaError)}</div>` : ''}
      ${groups.map((grp) => `<div class="genre-group"><h5>${esc(grp.name)}</h5><div class="genre-chips">${grp.items.map((g) => `<button type="button" class="genre-chip ${st.genres.includes(g.key) ? 'on' : ''}" data-key="${esc(g.key)}" data-name="${esc(g.name.toLowerCase())}"
        title="${[g.types ? `For ${g.types.map((t) => KIND_LABEL[t]).join(', ')}` : '', g.tag ? 'a TMDB tag' : g.tagged?.length ? 'TV shows by TMDB tag' : ''].filter(Boolean).join(' - ')}">${esc(g.name)}${g.tag ? '<i class="tag-dot">•</i>' : ''}</button>`).join('')}</div></div>`).join('')}
      <p class="muted small">Pick as many as you like: titles must have all of them. Then press Search.
        ${list.some((g) => g.tag) ? 'Genres marked • are TMDB tags: added by its users, so popular titles have them and obscure ones less often.' : ''}</p></div>` : ''}
    ${st.error ? `<div class="warn">${esc(st.error)}</div>` : ''}
    ${st.notes.length ? `<div class="find-notes">${st.notes.map((n) => `<p>${esc(n)}</p>`).join('')}</div>` : ''}
    ${st.results === null ? `<div class="find-grid">${'<div class="find-card skeleton"><div class="find-poster sk-poster"></div><div class="sk-lines"><i></i><i></i></div></div>'.repeat(12)}</div>`
    : st.results.length ? `<div class="find-grid">${st.results.map((r, i) => findCardHTML(r, 'search', i)).join('')}</div>
      <p class="muted small center">${st.results.length} shown</p>
      ${st.more ? `<div class="center"><button class="btn btn-ghost" data-f="more" ${st.loading ? 'disabled' : ''}>${st.loading ? 'Loading…' : 'Show 50 more'}</button></div>` : ''}`
      : st.error ? '' : '<p class="muted">Nothing found - try fewer filters, other keywords or another year.</p>'}`;
}

function drawFindMood(body) {
  const st = S.find.mood;
  const hasAi = (S.status.ai || []).length;
  const bubbles = st.messages.map((m) => `<div class="bubble ${m.role}">${esc(m.text)}</div>`).join('');
  const r = st.result;
  body.innerHTML = `<div class="mood">
    ${hasAi ? '' : '<div class="warn">This needs an AI helper. Add a free key (Gemini, Groq or OpenRouter) in Settings. <button class="btn btn-sm btn-white" data-f="settings">Open Settings</button></div>'}
    <div class="chat">${bubbles || '<div class="bubble ai">What are you in the mood for? Describe it any way you like - a feeling, a genre, something like a show you loved, how much time you have…</div>'}
      ${st.busy ? '<div class="bubble ai typing"><i></i><i></i><i></i></div>' : ''}</div>
    ${st.error ? `<div class="warn">${esc(st.error)}</div>` : ''}
    ${r ? '' : `<form class="mood-form" data-f="mood-form"><textarea class="input" name="text" rows="2" placeholder="e.g. something cosy and funny, not too long, like Spy x Family" ${hasAi ? '' : 'disabled'}></textarea>
      <button class="btn btn-red" type="submit" ${hasAi && !st.busy ? '' : 'disabled'}>Send</button></form>`}
    ${r ? `<div class="mood-result">${r.intro ? `<p class="mood-intro">${esc(r.intro)} <span class="muted small">- ${esc(r.by || 'AI')}</span></p>` : ''}
      ${r.from_list.length ? `<h3>From your list</h3><div class="find-grid">${r.from_list.map((x, i) => findCardHTML({ ...x, in_list: x.id }, 'mood-list', i)).join('')}</div>` : ''}
      ${r.new.length ? `<h3>New for you</h3><div class="find-grid">${r.new.map((x, i) => findCardHTML(x, 'mood-new', i)).join('')}</div>` : '<p class="muted">No new titles could be confirmed on TMDB / MyAnimeList this time.</p>'}
      <div class="center"><button class="btn btn-ghost" data-f="mood-again">Start over</button></div></div>` : ''}</div>`;
  if (!r) setTimeout(() => $('.mood-form textarea')?.focus(), 30);
}

async function moodSend(text) {
  const st = S.find.mood;
  st.messages.push({ role: 'you', text });
  st.busy = true; st.error = '';
  drawFind('mood');
  try {
    const r = await api('POST', '/api/find/mood', { messages: st.messages });
    if (r.question) st.messages.push({ role: 'ai', text: r.question });
    else st.result = r;
  } catch (e) { st.error = e.message; }
  st.busy = false;
  if (findShowing('mood')) drawFind('mood');
}

async function findOverview(r, wrap) {
  try {
    const { overview } = await api('GET', `/api/find/about?source=${r.source}&id=${encodeURIComponent(r.source_id)}`);
    r.overview = overview || '';
  } catch { r.overview = r.overview || ''; }
  const p = wrap.querySelector('.find-overview');
  if (p) p.textContent = r.overview || 'No summary available.';
}

function findPreview(r) {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="find-preview">
    ${r.poster ? `<img src="${esc(r.poster)}" alt="">` : ''}
    <div class="modal-pad"><h2>${esc(r.title)}</h2>
      <p class="muted">${[r.year, findKind(r), r.rating ? `★ ${fmtRating(r.rating)}` : ''].filter(Boolean).map(esc).join(' · ')}</p>
      ${r.original_title ? `<p class="muted small">${esc(r.original_title)}</p>` : ''}
      ${r.why ? `<p class="find-why">${esc(r.why)}</p>` : ''}
      <p class="find-overview">${r.overview ? esc(r.overview) : '<span class="spin-dot"></span> Loading the summary…'}</p>
      <div class="btns">${r.in_list ? `<button class="btn btn-white" data-p="open">Open in your list</button>` : '<button class="btn btn-red" data-p="add">+ Add to my list</button>'}</div></div></div>`, 'find-modal');
  if (!r.overview) findOverview(r, wrap);
  wrap.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-p]')?.dataset.p;
    if (a === 'open') { closeModal(wrap); openTitle(r.in_list || r.id); }
    if (a === 'add') {
      e.target.disabled = true;
      try { await findAdd(r); closeModal(wrap); } catch (err) { fail(err); e.target.disabled = false; }
    }
  });
}

async function findAdd(r) {
  const added = await api('POST', '/api/items', { type: resultType(r), source: r.source, source_id: r.source_id });
  upsert(added);
  r.in_list = added.id;
  toast(`Added ${added.title} to ${TYPE_PLURAL[added.type]}`);
  refreshFindCards();
  render();
}

$('#view').addEventListener('click', async (e) => {
  if (route().name !== 'find') return;
  const segBtn = e.target.closest('[data-seg] button');
  if (segBtn) {
    const which = segBtn.closest('[data-seg]').dataset.seg;
    const v = segBtn.dataset.v;
    if (which === 'top-type') { S.find.top.type = v; findTopLoad(); }
    if (which === 'top-order') { S.find.top.order = v; findTopLoad(); }
    return;
  }
  const st = S.find.search;
  const typeBtn = e.target.closest('.seg.multi [data-type]');
  if (typeBtn) {
    const k = typeBtn.dataset.type;
    st.types = st.types.includes(k) ? st.types.filter((x) => x !== k) : [...st.types, k];
    // Genres from another type's list don't carry over.
    const still = new Set(searchGenreList().map((g) => g.key));
    st.genres = st.genres.filter((g) => still.has(g));
    if (st.sort === 'grossing' && st.types.length && !st.types.includes('movie')) st.sort = 'popular';
    drawFind('search');
    return;
  }
  const chip = e.target.closest('.genre-chip');
  if (chip) {
    const k = chip.dataset.key;
    st.genres = st.genres.includes(k) ? st.genres.filter((x) => x !== k) : [...st.genres, k];
    chip.classList.toggle('on', st.genres.includes(k));
    $('.genre-bar').outerHTML = genreBarHTML();
    return;
  }
  const f = e.target.closest('[data-f]')?.dataset.f;
  const card = e.target.closest('.find-card');
  if (f === 'more') { st.page += 1; findSearchLoad(true); return; }
  if (f === 'genre-panel') { st.panel = !st.panel; drawFind('search'); if (st.panel) $('.genre-filter')?.focus(); return; }
  if (f === 'genre-off') {
    const k = e.target.closest('[data-key]').dataset.key;
    st.genres = st.genres.filter((x) => x !== k);
    $$('.genre-chip').forEach((c) => { if (c.dataset.key === k) c.classList.remove('on'); });
    $('.genre-bar').outerHTML = genreBarHTML();
    return;
  }
  if (f === 'genre-clear') { st.genres = []; $$('.genre-chip.on').forEach((c) => c.classList.remove('on')); $('.genre-bar').outerHTML = genreBarHTML(); return; }
  if (f === 'settings') { openSettings(); return; }
  if (f === 'mood-again') { S.find.mood = { messages: [], result: null, error: '', busy: false }; drawFind('mood'); return; }
  if (card && !card.classList.contains('skeleton')) {
    const r = findList(card.dataset.list)[Number(card.dataset.i)];
    if (!r) return;
    if (f === 'add') {
      e.target.closest('button').disabled = true;
      try { await findAdd(r); } catch (err) { fail(err); }
    } else if (f === 'open') openTitle(r.in_list || r.id);
    else findPreview(r);
  }
});
$('#view').addEventListener('change', (e) => {
  if (e.target.dataset.f === 'top-year') { S.find.top.year = Number(e.target.value); findTopLoad(); }
  const form = e.target.closest('[data-f="search-form"]');
  if (form && e.target.name && e.target.name !== 'q') {
    S.find.search[e.target.name] = e.target.value;
    if (e.target.name === 'sort' || e.target.name === 'year' || e.target.name === 'format') findSearchLoad();
  }
});
$('#view').addEventListener('input', (e) => {
  if (e.target.name === 'q' && e.target.closest('[data-f="search-form"]')) S.find.search.q = e.target.value;
  if (e.target.classList.contains('genre-filter')) filterGenres(e.target.value);
});

function filterGenres(text) {
  const want = text.trim().toLowerCase();
  $$('.genre-chip').forEach((c) => c.classList.toggle('hidden', !!want && !c.dataset.name.includes(want)));
  $$('.genre-group').forEach((g) => g.classList.toggle('hidden', !g.querySelector('.genre-chip:not(.hidden)')));
}
$('#view').addEventListener('submit', (e) => {
  const form = e.target.closest('[data-f]');
  if (!form) return;
  e.preventDefault();
  const data = new FormData(form);
  if (form.dataset.f === 'search-form') {
    Object.assign(S.find.search, { q: String(data.get('q') || ''), year: data.get('year') || '', sort: data.get('sort') || 'popular', format: data.get('format') || '', panel: false });
    findSearchLoad();
  } else if (form.dataset.f === 'mood-form') {
    const text = String(data.get('text') || '').trim();
    if (text && !S.find.mood.busy) moodSend(text);
  }
});
$('#view').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && e.target.closest('.mood-form textarea')) {
    e.preventDefault();
    e.target.closest('form').requestSubmit();
  }
});

/* ======================================================== Surprise me */

const SURPRISE_TIME = [['', 'Any'], ['short', 'Under 30 min'], ['hour', 'About an hour'], ['long', 'A whole evening']];
const SURPRISE_MOOD = [['', 'Anything'], ['new', 'Something new'], ['continue', 'Carry on with something']];

function surprisePool(f) {
  return listed().filter((i) => {
    if (f.type && i.type !== f.type) return false;
    if (f.genre && !i.genres.includes(f.genre)) return false;
    if (f.downloaded && !hasFiles(i)) return false;
    const started = !!i.last_played || i.progress.episode > 1 || i.progress.season > 1;
    if (f.mood === 'new' && started) return false;
    if (f.mood === 'continue' && !started) return false;
    const mins = i.runtime || (isSeries(i) ? 24 : 100);
    if (f.time === 'short' && mins > 32) return false;
    if (f.time === 'hour' && (isSeries(i) ? mins > 65 : mins > 80)) return false;
    if (f.time === 'long' && !isSeries(i) && mins < 85) return false;
    return true;
  });
}

function openSurprise() {
  const genres = [...new Set(listed().flatMap((i) => i.genres))].sort();
  let f = {};
  try { f = JSON.parse(localStorage.getItem('surprise') || '{}'); } catch { f = {}; }
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad surprise">
    <h2>🎲 Surprise me</h2><p class="sub">Picks something from your list. Narrow it down if you like.</p>
    <div class="surprise-filters">
      ${segmented('sp-type', [['', 'Anything'], ...Object.entries(KIND_LABEL)], f.type || '')}
      ${segmented('sp-time', SURPRISE_TIME, f.time || '')}
      ${segmented('sp-mood', SURPRISE_MOOD, f.mood || '')}
      <div class="surprise-row"><select class="select" id="sp-genre"><option value="">Any genre</option>${genres.map((g) => `<option ${g === f.genre ? 'selected' : ''}>${esc(g)}</option>`).join('')}</select>
        <label class="check-row"><input type="checkbox" id="sp-dl" ${f.downloaded ? 'checked' : ''}> Only what's on my ${pcName()}</label></div>
    </div>
    <div class="surprise-stage" id="sp-stage"></div>
    <div class="btns center-btns"><button class="btn btn-red" data-sp="roll">🎲 Pick something</button></div></div>`, 'surprise-modal');
  const read = () => {
    const seg = (n) => $(`[data-seg="${n}"] .on`, wrap)?.dataset.v || '';
    f = { type: seg('sp-type'), time: seg('sp-time'), mood: seg('sp-mood'), genre: $('#sp-genre', wrap).value, downloaded: $('#sp-dl', wrap).checked };
    try { localStorage.setItem('surprise', JSON.stringify(f)); } catch { /* fine */ }
    return f;
  };
  const roll = () => {
    const pool = surprisePool(read());
    const stage = $('#sp-stage', wrap);
    if (!pool.length) { stage.innerHTML = '<p class="muted center">Nothing in your list fits that - try fewer filters.</p>'; return; }
    const pick = pool[Math.floor(Math.random() * pool.length)];
    // A quick shuffle through the posters before it lands.
    const reel = Array.from({ length: Math.min(10, pool.length * 2) }, () => pool[Math.floor(Math.random() * pool.length)]);
    let n = 0;
    clearInterval(wrap.reel);
    wrap.reel = setInterval(() => {
      const x = n < reel.length ? reel[n] : pick;
      stage.innerHTML = surpriseCard(x, n >= reel.length);
      if (n++ >= reel.length) clearInterval(wrap.reel);
    }, 70);
  };
  wrap.addEventListener('click', (e) => {
    const segBtn = e.target.closest('[data-seg] button');
    if (segBtn) { $$('button', segBtn.parentElement).forEach((b) => b.classList.toggle('on', b === segBtn)); roll(); return; }
    const a = e.target.closest('[data-sp]')?.dataset.sp;
    const id = e.target.closest('[data-id]')?.dataset.id;
    if (a === 'roll') roll();
    else if (a === 'watch') { closeModal(wrap); openWatch(findItem(id)); }
    else if (a === 'info') { closeModal(wrap); openTitle(id); }
  });
  wrap.addEventListener('change', (e) => { if (e.target.id === 'sp-genre' || e.target.id === 'sp-dl') roll(); });
  wrap.onClose = () => clearInterval(wrap.reel);
  roll();
}

function surpriseCard(item, landed) {
  const meta = [{ tv: 'TV show', anime: 'Anime', movie: 'Movie' }[item.type], item.year, item.rating ? `★ ${fmtRating(item.rating)}` : '',
    isSeries(item) ? (item.last_played ? `up to ${epLabel(item.progress)}` : 'not started') : (item.runtime ? fmtRuntime(item.runtime) : '')].filter(Boolean);
  return `<div class="surprise-card ${landed ? 'landed' : ''}" data-id="${esc(item.id)}">
    <div class="surprise-poster"><span class="surprise-dice">🎬</span>${item.poster ? `<img src="${esc(item.poster)}" alt="">` : ''}</div>
    <div class="surprise-info"><h3>${esc(item.title)}</h3><p class="muted small">${meta.map(esc).join(' · ')}</p>
      ${landed ? `<p class="surprise-overview">${esc(item.overview || '')}</p>
      <div class="btns"><button class="btn btn-white" data-sp="watch">${ICON.play} Watch now</button>
        <button class="btn btn-ghost" data-sp="info">More info</button>
        <button class="btn btn-ghost" data-sp="roll">Pick another</button></div>` : ''}</div></div>`;
}

/* ======================================================== MyAnimeList import */

const MAL_STATUS = { watching: 'Watching', plan_to_watch: 'Plan to watch', on_hold: 'On hold', completed: 'Completed', dropped: 'Dropped' };

function openImportMal() {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Import your MyAnimeList list</h2>
    <p class="sub">Anime from your list are added with their info, and "episodes watched" sets where you're up to.</p>
    ${S.status.mal ? '' : '<div class="warn">Add your MyAnimeList Client ID in Settings first.</div>'}
    <label class="field"><span>Your MyAnimeList user name</span>
      <div class="input-row"><input class="input" id="mal-user" placeholder="e.g. Ali_123" autocomplete="off">
      <button class="btn btn-white btn-sm" data-i="look">Look up</button></div>
      <small>Your list has to be public. Otherwise, export it at myanimelist.net → Profile → Export
        and <button class="linkish" data-i="file">choose the exported file</button> instead.</small></label>
    <div id="import-out"></div></div>`, 'import-modal');
  let source = null;
  const out = $('#import-out', wrap);
  const preview = async (src) => {
    out.innerHTML = '<div class="skeleton"><div class="sk-lines"><i></i><i></i></div></div>';
    try {
      const r = await api('POST', '/api/import/mal', { ...src, preview: true });
      source = src;
      out.innerHTML = `<p><b>${r.total} anime</b> on that list. Bring in:</p>
        ${Object.entries(MAL_STATUS).map(([k, label]) => `<label class="check-row"><input type="checkbox" name="${k}" ${['watching', 'plan_to_watch', 'on_hold'].includes(k) && r.counts[k] ? 'checked' : ''} ${r.counts[k] ? '' : 'disabled'}>
          ${label} <span class="muted">(${r.counts[k]})</span></label>`).join('')}
        <p class="muted small">Ones already in your list keep their place (and move forward if MyAnimeList says you're further).</p>
        <div class="btns"><button class="btn btn-red" data-i="go">Import</button></div>`;
    } catch (e) { out.innerHTML = `<div class="warn">${esc(e.message)}</div>`; }
  };
  const follow = async () => {
    for (;;) {
      const st = await api('GET', '/api/import/status');
      const pct = st.total ? (st.done / st.total) * 100 : 100;
      out.innerHTML = `<div class="import-progress"><div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>
        <p>${st.running ? `Adding ${esc(st.current)}… (${st.done} of ${st.total})` : `<b>Done:</b> ${st.added} added${st.already ? `, ${st.already} were already in your list` : ''}.`}</p>
        ${st.failed.length ? `<details><summary class="bad small">${st.failed.length} couldn't be added</summary><div class="small muted">${st.failed.map(esc).join('<br>')}</div></details>` : ''}</div>`;
      if (!st.running) { await reload(); return; }
      if (st.done % 5 === 0) reload().catch(() => {});
      await new Promise((r) => setTimeout(r, 900));
      if (!wrap.isConnected) return;
    }
  };
  wrap.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-i]')?.dataset.i;
    if (!a) return;
    e.preventDefault();
    if (a === 'look') {
      const user = $('#mal-user', wrap).value.trim();
      if (!user) { toast('Type your user name first', 'error'); return; }
      preview({ user });
    } else if (a === 'file') {
      try {
        const { path } = await api('POST', '/api/browse', { kind: 'file', types: 'list', title: 'Choose your MyAnimeList export' });
        if (path) preview({ file: path });
      } catch (err) { fail(err); }
    } else if (a === 'go') {
      const statuses = $$('input[type=checkbox]:checked', out).map((c) => c.name);
      if (!statuses.length) { toast('Tick at least one', 'error'); return; }
      try {
        await api('POST', '/api/import/mal', { ...source, statuses });
        follow();
      } catch (err) { fail(err); }
    }
  });
  $('#mal-user', wrap).addEventListener('keydown', (e) => { if (e.key === 'Enter') $('[data-i="look"]', wrap).click(); });
}

/* ======================================================== watch together */

const PARTY_NOTE = '<p class="muted small">Each of you plays your own copy in the built-in player. Pausing, playing or skipping on either side does the same for both of you, the next episode follows along, and the party ends when the host closes their player.</p>';

function partyClicks(wrap) {
  wrap.addEventListener('click', async (e) => {
    const p = e.target.closest('[data-p]')?.dataset.p;
    if (p === 'copy') {
      const code = $('.party-code', wrap).textContent.trim();
      navigator.clipboard?.writeText(code).then(() => toast('Invite copied - send it to them'), () => {
        const r = document.createRange(); r.selectNodeContents($('.party-code', wrap));
        getSelection().removeAllRanges(); getSelection().addRange(r);
        toast('Press Ctrl+C (Cmd+C) to copy');
      });
    } else if (p === 'leave') {
      try { await api('POST', '/api/party/leave'); toast('You left the watch party'); closeModal(wrap); } catch (err) { fail(err); }
    }
  });
}

// Who's there and what's playing, kept up to date while the window is open.
function followParty(wrap, box) {
  const draw = async () => {
    if (!wrap.isConnected) return;
    let st;
    try { st = await api('GET', '/api/party'); } catch { st = null; }
    if (st) {
      box.innerHTML = st.active ? `<div class="party-live">
          <div class="party-row"><span class="live-dot"></span><b>${st.role === 'host' ? 'Your watch party' : `${esc(st.leader || 'The host')}'s watch party`}</b>
            <span class="muted small">${st.encrypted ? 'encrypted · ' : ''}${esc(st.server)}</span></div>
          ${st.playing ? `<div class="small">Playing <b>${esc(st.playing)}</b></div>` : ''}
          ${st.missing ? `<div class="warn small">They're playing ${esc(st.missing)}, which isn't in your downloads.</div>` : ''}
          <div class="party-people">${st.people.map((p) => `<span class="person${p.me ? ' me' : ''}">${esc(p.name)}${p.me ? ' (you)' : ''}${p.file ? '' : ' <i>no file</i>'}</span>`).join('')}</div>
          ${st.people.length < 2 && st.role === 'host' ? '<div class="muted small">Waiting for them to join…</div>' : ''}
          <button class="btn btn-sm btn-ghost" data-p="leave">${st.role === 'host' ? 'End the watch party' : 'Leave'}</button></div>`
        : st.ended ? `<div class="warn">The watch party ended: ${esc(st.ended)}.</div>` : '';
    }
    setTimeout(draw, 1500);
  };
  draw();
}

async function openStartParty(item) {
  const series = isSeries(item);
  // The same episodes, and files, the title page shows.
  let local = {};
  try { local = await api('GET', `/api/items/${item.id}/local`); } catch { /* the server says what's missing */ }
  const byEp = local.by_episode || {};
  const avail = series ? Object.keys(byEp).sort((a, b) => {
    const [as, ae] = a.split(':').map(Number);
    const [bs, be] = b.split(':').map(Number);
    return as - bs || ae - be;
  }) : [];
  const current = `${item.progress.season}:${item.progress.episode}`;
  const options = avail.map((k) => { const [s, n] = k.split(':').map(Number); return `<option value="${k}" ${k === current ? 'selected' : ''}>${esc(epLabel({ season: s, episode: n }))}</option>`; }).join('');
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>${ICON.people} Watch ${esc(item.title)} together</h2>
    <p class="sub">With someone on another computer, kept in step over the internet.</p>
    ${S.status.mpv ? '' : '<div class="warn">This needs the built-in player, which is missing.</div>'}
    ${series && avail.length ? `<label class="field"><span>Episode</span><select class="select" id="party-ep">${options}</select></label>` : ''}
    <div class="btns"><button class="btn btn-red" data-p="start">Start the watch party</button></div>
    <div id="party-out"></div><div id="party-live"></div>${PARTY_NOTE}</div>`, 'party-modal');
  partyClicks(wrap);
  $('[data-p="start"]', wrap).addEventListener('click', async (e) => {
    const key = $('#party-ep', wrap)?.value || current;
    const [season, episode] = key.split(':').map(Number);
    const path = series ? byEp[key]?.path : local.target?.path;
    e.target.disabled = true;
    e.target.textContent = 'Starting…';
    try {
      const r = await api('POST', '/api/party/start', { item_id: item.id, season, episode, path: path || '' });
      if (!r.ok) { $('#party-out', wrap).innerHTML = `<div class="warn">${esc(r.message)}</div>`; return; }
      e.target.closest('.btns').remove();
      $('#party-ep', wrap)?.closest('.field').remove();
      $('#party-out', wrap).innerHTML = `<div class="party-invite">
        <p><b>The player is open, paused on ${esc(r.file)}.</b> Send this invite; they paste it into <b>Join a watch party</b> (the ${ICON.people} button at the top of their Watchlist):</p>
        <div class="party-code-row"><code class="party-code">${esc(r.code)}</code><button class="btn btn-sm btn-white" data-p="copy">Copy</button></div>
        <p class="muted small">When they've joined, press Space in the player to start for both of you.
          Someone using the Syncplay app instead can join server <b>${esc(r.server)}</b>, room <b>${esc(r.room)}</b>.</p></div>`;
      followParty(wrap, $('#party-live', wrap));
    } catch (err) {
      $('#party-out', wrap).innerHTML = `<div class="warn">${esc(err.message)}</div>`;
    } finally { e.target.disabled = false; if (e.target.isConnected) e.target.textContent = 'Start the watch party'; }
  });
}

async function openJoinParty() {
  // In a party already: show it.
  try {
    const st = await api('GET', '/api/party');
    if (st.active) {
      const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
        <h2>${ICON.people} Watch party</h2><div id="party-live"></div>${PARTY_NOTE}</div>`, 'party-modal');
      partyClicks(wrap);
      followParty(wrap, $('#party-live', wrap));
      return;
    }
  } catch { /* show the join window */ }
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>${ICON.people} Join a watch party</h2>
    <p class="sub">Paste the invite you were sent. It starts with WLP1.</p>
    <textarea class="input party-paste" rows="3" placeholder="WLP1.…"></textarea>
    <div id="party-peek"></div>
    <div class="btns"><button class="btn btn-red" data-p="join" disabled>Join</button></div>
    <div id="party-out"></div><div id="party-live"></div>${PARTY_NOTE}</div>`, 'party-modal');
  partyClicks(wrap);
  const box = $('.party-paste', wrap);
  const join = $('[data-p="join"]', wrap);
  box.focus();
  box.addEventListener('input', async () => {
    const code = box.value.trim();
    join.disabled = true;
    $('#party-peek', wrap).innerHTML = '';
    if (!code) return;
    try {
      const inv = await api('POST', '/api/party/peek', { code });
      $('#party-peek', wrap).innerHTML = `<div class="party-what">${esc(inv.title)}${inv.episode ? ` · ${esc(epLabel({ season: inv.season, episode: inv.episode }))}` : ''}
        <span class="muted small">${inv.host ? `from ${esc(inv.host)} · ` : ''}${inv.in_list ? 'in your list' : 'not in your list yet - it will be added'}</span></div>`;
      join.disabled = false;
    } catch (err) {
      $('#party-peek', wrap).innerHTML = `<p class="bad small">${esc(err.message)}</p>`;
    }
  });
  const go = async (path = '') => {
    join.disabled = true;
    join.textContent = 'Joining…';
    try {
      const r = await api('POST', '/api/party/join', { code: box.value.trim(), path });
      if (r.ok) {
        $('#party-out', wrap).innerHTML = `<p class="ok"><b>✓ Joined.</b> Playing your copy: ${esc(r.file)}. It starts when ${esc(r.leader || 'the host')} presses play.</p>`;
        join.closest('.btns').remove();
        box.remove();
        followParty(wrap, $('#party-live', wrap));
        await reload();
        return;
      }
      $('#party-out', wrap).innerHTML = `<div class="warn">${esc(r.message)} <button class="btn btn-sm btn-white" data-p="choose">Choose the file…</button></div>`;
      $('[data-p="choose"]', wrap).addEventListener('click', async () => {
        const { path: chosen } = await api('POST', '/api/browse', { kind: 'file', title: 'Choose the episode to watch' });
        if (chosen) go(chosen);
      });
      await reload();
    } catch (err) {
      $('#party-out', wrap).innerHTML = `<div class="warn">${esc(err.message)}</div>`;
    } finally { if (join.isConnected) { join.disabled = false; join.textContent = 'Join'; } }
  };
  join.addEventListener('click', () => go());
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

function openAdd(type = 'tv', query = '', preCol = '', onAdded = null) {
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
    // Shimmering placeholders while the search runs.
    out.innerHTML = `<div class="results">${'<div class="result skeleton"><div class="sk-poster"></div><div class="sk-lines"><i></i><i></i><i></i></div></div>'.repeat(4)}</div>`;
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
      if (onAdded) { closeModal(wrap); onAdded(item); toast(`Added ${item.title} and sorted its files`); return; }
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
  loadRelated(wrap);
}

function refreshOpenTitle() {
  $$('#modal-root .overlay').forEach((wrap) => {
    if (!wrap.dataset.itemId) return;
    if (!findItem(wrap.dataset.itemId)) { closeModal(wrap); return; }
    if ($('.tm-trailer', wrap)) return;
    const sig = titleSig(findItem(wrap.dataset.itemId));
    if (sig === wrap.dataset.sig) return;
    const scroll = wrap.scrollTop;
    drawTitle(wrap);
    wrap.scrollTop = scroll;
  });
}

// Everything a title page shows: if none of it changed, the page isn't redrawn.
const titleSig = (item) => JSON.stringify([item.modified, item.local_folder, item.local_broken,
  (S.library.items || {})[item.id], S.collections.length, S.status.player, S.status.mpv]);

function drawTitle(wrap) {
  const item = findItem(wrap.dataset.itemId);
  wrap.dataset.sig = titleSig(item);
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
    const total = item.seasons.filter((s) => s.number > 0).reduce((n, s) => n + (s.episodes || 0), 0) || item.episodes_total || 0;
    const done = absoluteIndex(item, item.progress) - 1;
    const pct = total ? Math.min(100, (done / total) * 100) : 0;
    progress = `<div class="progress-box"><span class="muted">Up next</span><b>${epLabel(item.progress)}</b>
      <div class="bar"><i style="width:${pct.toFixed(1)}%"></i></div>
      <span class="muted small">${done} of ${total || '?'} watched</span>
      <button class="btn btn-ghost btn-sm" data-t="watched">${ICON.check} Mark watched</button>
      ${(S.status.ai || []).length && done > 0 ? '<button class="btn btn-ghost btn-sm" data-t="recap" title="A spoiler-free recap of what you\'ve watched, written by AI">✨ Previously on…</button>' : ''}</div>`;
  }

  modal.innerHTML = `<button class="modal-close" aria-label="Close">×</button>
    <div class="tm-hero">${heroImg}
      ${item.backdrop && item.poster ? `<img class="poster-side" src="${esc(item.poster)}" alt="">` : ''}
      <div class="tm-hero-content">
        <h1>${esc(item.title)}</h1>
        <div class="btns">
          <button class="btn btn-white" data-t="watch">${ICON.play} ${esc(watchLabel(item))}</button>
          ${hasFiles(item) ? '<button class="btn btn-grey" data-t="vlc" title="Play the file in VLC instead of the built-in player">Play in VLC</button>' : ''}
          <button class="btn btn-grey ${hasFiles(item) ? '' : 'hidden'}" data-t="party" title="Watch in sync with someone else, each on your own computer">${ICON.people} Watch together</button>
          <button class="btn btn-grey" data-t="trailer">${ICON.play} ${item.trailer ? 'Trailer' : 'Find trailer'}</button>
          <button class="btn-round" data-t="menu" title="Collections & more">${ICON.plus}</button>
          <button class="btn-round" data-t="remove" title="Remove from list">${ICON.trash}</button>
        </div>
      </div>
    </div>
    <div class="tm-layout"><div class="tm-main">
    <div class="tm-body">
      <div>
        <div class="tm-meta">${meta.join('')}</div>
        ${item.original_title ? `<p class="tm-tagline" style="font-style:normal">${esc(item.original_title)}</p>` : ''}
        ${progress}
        ${item.tagline ? `<p class="tm-tagline">${esc(item.tagline)}</p>` : ''}
        <p class="tm-overview">${esc(item.overview || 'No summary available.')}</p>
        ${animeOnTmdb(item) ? `<div class="mal-switch">${ICON.info || ''}<span>This anime's details come from TMDB, whose seasons and episode numbers don't match MyAnimeList's or most downloads'.</span>
          <button class="btn btn-sm btn-white" data-t="use-mal">Use MyAnimeList instead</button></div>` : ''}
        ${item.in_watched ? `<div class="watched-note">${ICON.check} In your Watched list <button class="linkish" data-t="unwatch">Move back to my list</button></div>` : ''}
      </div>
      <aside class="tm-side">${side.join('')}</aside>
    </div>
    <section class="tm-section"><h3>On your ${pcName()}</h3><div class="folder-line" id="folder-line"></div></section>
    <section class="tm-section" id="torrent-section"><h3>Stream from a torrent<span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" data-t="torrents-window">${ICON.download || '⇩'} Torrents window</button></h3>
      <div id="torrent-box"><div class="muted small">…</div></div></section>
    <section class="tm-section hidden" id="extras-section"><h3>Extras <span class="muted small" id="extras-count"></span></h3>
      <div class="extras" id="extras"></div></section>
    <section class="tm-section hidden" id="family-section"><h3>From the same folder <span class="muted small">films, OVAs, specials and extras next to this title's files</span></h3>
      <div id="family"></div></section>
    ${series ? '<section class="tm-section"><h3>Episodes<span class="spacer"></span><span id="ep-picker"></span></h3><div id="ep-summary"></div><div class="episodes" id="ep-list"></div></section>' : ''}
    </div><aside class="tm-related" id="related"></aside></div>`;

  drawFolderLine(wrap);
  drawRelated(wrap);
  if (series) drawEpisodes(wrap);
  modal.onclick = (e) => titleClick(wrap, e);
}

function absoluteIndex(item, p) {
  if (p.season === 0) return 1;
  let n = 0;
  for (const s of item.seasons) {
    if (s.number > 0 && s.number < p.season) n += s.episodes || 0;
  }
  return n + p.episode;
}
const seasonName = (n) => (n === 0 ? 'Specials' : `Season ${n}`);

// Everything connected to a title, beside its page: found when you press the button
// (MyAnimeList's links through the whole franchise, plus the AI), remembered after.
async function loadRelated(wrap, run = false) {
  const item = findItem(wrap.dataset.itemId);
  if (run) { wrap.st.related = { running: true, entries: wrap.st.related?.entries || null }; drawRelated(wrap); }
  try {
    wrap.st.related = await api(run ? 'POST' : 'GET', `/api/items/${item.id}/connected`);
    // Asked for it: show the whole guide as soon as it's ready.
    if (run && wrap.isConnected && wrap.st.related.entries) openGuide(wrap);
  } catch (e) {
    wrap.st.related = { entries: wrap.st.related?.entries || null, note: e.message, failed: true };
  }
  if (wrap.isConnected) drawRelated(wrap);
}

/* ---- the story guide: everything connected, in watch order, explained */

const prefGet = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const prefSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private window */ } };
const IMPORTANCE = { essential: 'Essential', recommended: 'Recommended', optional: 'Optional', skippable: 'Skippable' };
const CANON = { canon: 'Canon', 'anime original': 'Anime original', 'non-canon': 'Non-canon', recap: 'Recap', 'alternate version': 'Alternate version' };
const guideKey = (x) => (x.self ? 'self' : `${x.source}:${x.source_id}`);

function guideStatus(x) {
  const pc = pcName();
  if (x.in_list && x.files) {
    if (x.episodes_total > 1) {
      return x.episodes_here >= x.episodes_total
        ? `<span class="rel-pill in">${ICON.check} All ${x.episodes_total} episodes on your ${pc}</span>`
        : `<span class="rel-pill part">${x.episodes_here} of ${x.episodes_total} episodes on your ${pc}</span>`;
    }
    return `<span class="rel-pill in">${ICON.check} On your ${pc}</span>`;
  }
  if (x.in_list) return '<span class="rel-pill">In your list · not downloaded</span>';
  if (x.waiting) return `<span class="rel-pill dl">In your downloads · ${x.waiting} file${x.waiting === 1 ? '' : 's'} waiting for Sort now</span>`;
  return `<span class="rel-pill miss">Not on your ${pc}</span>`;
}

function openGuide(wrap) {
  const m = openModal('<button class="modal-close" aria-label="Close">×</button><div class="guide"></div>', 'guide-modal');
  m.st = { order: prefGet('guideOrder', 'release'), spoilers: false };
  drawGuide(m, wrap);
  m.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-g]');
    if (!b) return;
    const all = guideAll(wrap);
    const x = all[Number(b.dataset.i)];
    if (b.dataset.g === 'order') { m.st.order = b.dataset.v; prefSet('guideOrder', m.st.order); drawGuide(m, wrap); }
    else if (b.dataset.g === 'spoilers') { m.st.spoilers = !m.st.spoilers; drawGuide(m, wrap); }
    else if (b.dataset.g === 'refresh') { closeModal(m); loadRelated(wrap, true); }
    else if (b.dataset.g === 'open' && x) { closeModal(m); openTitle(x.in_list); }
    else if (b.dataset.g === 'add' && x) {
      b.disabled = true;
      try {
        await addRelated(wrap, x);
        await reload();
        await loadRelated(wrap);
        drawGuide(m, wrap);
      } catch (err) { fail(err); b.disabled = false; }
    }
  });
}

function guideAll(wrap) {
  const data = wrap.st.related || {};
  return [...(data.self ? [data.self] : []), ...(data.entries || [])];
}

function drawGuide(m, wrap) {
  const box = $('.guide', m);
  if (!box) return;
  const item = findItem(wrap.dataset.itemId);
  const data = wrap.st.related || {};
  const guide = data.guide;
  const all = guideAll(wrap);
  const notes = guide?.entries || {};
  const order = m.st.order === 'story' && guide?.story_order?.length ? 'story' : 'release';
  const when = (x) => x.date || (x.year ? `${x.year}-99` : '9999');
  let main = all.map((x, i) => i);
  let aside = [];
  if (order === 'story') {
    const pos = new Map(guide.story_order.map((k, n) => [k, n]));
    main = main.filter((i) => pos.has(guideKey(all[i]))).sort((a, b) => pos.get(guideKey(all[a])) - pos.get(guideKey(all[b])));
    aside = all.map((x, i) => i).filter((i) => !pos.has(guideKey(all[i]))).sort((a, b) => when(all[a]).localeCompare(when(all[b])));
  } else {
    main.sort((a, b) => when(all[a]).localeCompare(when(all[b])));
  }
  const full = all.filter((x) => x.in_list && x.files && !(x.episodes_total > 1 && x.episodes_here < x.episodes_total)).length;
  const partly = all.filter((x) => x.in_list && x.files && x.episodes_total > 1 && x.episodes_here < x.episodes_total).length;
  const missing = all.filter((x) => !x.files).length;
  const card = (i, n) => {
    const x = all[i];
    const g = notes[guideKey(x)] || {};
    const kind = (x.media_kind || '').toLowerCase() === 'ona' ? 'ONA' : kindName(x);
    const meta = [kind, x.year, x.episodes_total > 1 ? `${x.episodes_total} episodes` : '',
      x.self ? '' : `${x.relation || ''}${x.via ? ` (of ${x.via})` : ''}`].filter(Boolean);
    return `<div class="g-card${x.self ? ' self' : ''}" style="--i:${Math.min(n, 16)}">
      <div class="g-num">${n === null ? '•' : n + 1}</div>
      ${x.poster ? `<img class="g-poster" src="${esc(x.poster)}" alt="" loading="lazy">` : '<span class="g-poster rel-ph"></span>'}
      <div class="g-body">
        <div class="g-title"><b>${esc(x.title)}</b>${x.self ? '<span class="g-you">This title</span>' : ''}</div>
        <div class="g-meta">${meta.map(esc).join(' · ')}</div>
        ${g.importance || (g.canon && g.canon !== 'unknown') ? `<div class="g-tags">
          ${g.importance ? `<span class="g-imp ${g.importance}">${IMPORTANCE[g.importance]}</span>` : ''}
          ${g.canon && g.canon !== 'unknown' ? `<span class="g-canon">${CANON[g.canon] || esc(g.canon)}</span>` : ''}</div>` : ''}
        ${g.connects ? `<p><span class="g-label">How it connects</span>${esc(g.connects)}</p>` : ''}
        ${g.placement ? `<p><span class="g-label">When to watch</span>${esc(g.placement)}</p>` : ''}
        ${g.about ? `<p><span class="g-label">What it's about</span>${esc(g.about)}</p>` : ''}
        ${m.st.spoilers && g.spoilers ? `<p class="g-spoil"><span class="g-label">Spoilers</span>${esc(g.spoilers)}</p>` : ''}
        <div class="g-status">${guideStatus(x)}
          ${x.self ? '' : x.in_list ? `<button class="btn btn-sm btn-ghost" data-g="open" data-i="${i}">Open</button>`
    : `<button class="btn btn-sm btn-white" data-g="add" data-i="${i}">+ Add to my list</button>`}</div>
      </div></div>`;
  };
  const aiNames = data.ai || S.status.ai || [];
  box.innerHTML = `<div class="g-head">
      <h2>Everything connected to ${esc(item.title)}</h2>
      <p class="g-count"><b>${all.length}</b> titles · <span class="ok">${full} complete on your ${pcName()}</span>${partly ? ` · <span class="warn-text">${partly} partly downloaded</span>` : ''} · <span class="${missing ? 'bad' : ''}">${missing} not on your ${pcName()}</span></p>
    </div>
    ${guide?.overview ? `<div class="g-overview${m.st.spoilers ? ' spoil' : ''}">${esc(m.st.spoilers && guide.overview_spoilers ? guide.overview_spoilers : guide.overview)}
      <span class="muted small"> - ${esc(guide.by || 'AI')}</span></div>`
    : `<div class="g-overview muted">${aiNames.length
      ? `This was found before the story guide existed. <button class="linkish" data-g="refresh">Find again</button> to have ${esc(aiNames[0])} explain how each one connects, when to watch it and whether it's canon.`
      : 'Add a free AI key in Settings to see how each one connects to the story, when to watch it, and whether it\'s canon. The facts below come from MyAnimeList / TMDB.'}</div>`}
    <div class="g-tools">
      <div class="seg"><button class="${order === 'release' ? 'on' : ''}" data-g="order" data-v="release">Release order</button>
        <button class="${order === 'story' ? 'on' : ''}" data-g="order" data-v="story" ${guide?.story_order?.length ? '' : 'disabled title="Needs the AI guide"'}>Story order</button></div>
      ${guide ? `<button class="g-switch ${m.st.spoilers ? 'on' : ''}" data-g="spoilers"><i></i> Show spoilers</button>` : ''}
      <span class="spacer"></span>
      <button class="btn btn-sm btn-ghost" data-g="refresh">↻ Find again</button>
    </div>
    <div class="g-list">${main.map((i, n) => card(i, n)).join('')}</div>
    ${aside.length ? `<h4 class="g-aside">Not part of the story's timeline <span class="muted small">recaps, music videos, extras</span></h4>
      <div class="g-list">${aside.map((i) => card(i, null)).join('')}</div>` : ''}
    ${data.note ? `<p class="muted small">${esc(data.note)}</p>` : ''}
    ${data.at ? `<p class="muted small">Looked ${esc(ago(data.at))}.</p>` : ''}`;
}

const KIND_GROUPS = [['tv', 'TV seasons & series'], ['movie', 'Films'], ['ova', 'OVAs & ONAs'],
  ['special', 'Specials & extras'], ['other', 'Other']];

function connectedKind(x) {
  const k = (x.media_kind || x.media_type || '').toLowerCase().replace(/ /g, '_');
  if (k === 'movie' || x.type === 'movie') return 'movie';
  if (k === 'ova' || k === 'ona') return 'ova';
  if (['special', 'tv_special', 'music', 'pv', 'cm'].includes(k)) return 'special';
  if (k === 'tv' || x.type === 'tv' || x.format === 'series' || !k) return 'tv';
  return 'other';
}

function drawRelated(wrap) {
  const box = $('#related', wrap);
  if (!box) return;
  const item = findItem(wrap.dataset.itemId);
  const data = wrap.st.related || {};
  const aiNames = data.ai || S.status.ai || [];
  const button = (label) => `<button class="btn btn-white rel-find" data-r="find" ${data.running ? 'disabled' : ''}>
    ${data.running ? '<span class="spin-dot"></span> Looking…' : label}</button>`;
  const head = `<div class="rel-head"><h3>Everything connected</h3>
    ${data.entries ? '<button class="rel-refresh" data-r="find" title="Look again">↻</button>' : ''}</div>`;
  if (!data.entries) {
    box.innerHTML = head + `<p class="muted small">Every season, film, OVA, ONA, special, sequel, prequel and spin-off of ${esc(item.title)}${aiNames.length ? `, found through ${item.type === 'anime' ? 'MyAnimeList and ' : ''}${esc(aiNames[0])}` : item.type === 'anime' ? ', found through MyAnimeList' : ''}, with what's on your ${pcName()} and what isn't.</p>
      ${button(`✨ Find everything connected`)}
      ${data.running ? `<p class="muted small">This can take up to a minute for a big franchise.</p>${'<div class="rel-row skeleton"><div class="sk-poster"></div><div class="sk-lines"><i></i><i></i></div></div>'.repeat(3)}` : ''}
      ${data.note ? `<p class="bad small">${esc(data.note)}</p>` : ''}`;
    return;
  }
  const entries = data.entries;
  const here = entries.filter((x) => x.in_list && x.files);
  const listed = entries.filter((x) => x.in_list && !x.files);
  const missing = entries.filter((x) => !x.in_list);
  const row = (x) => {
    const i = entries.indexOf(x);
    const eps = x.in_list && x.episodes_total > 1 ? ` · ${x.episodes_here} of ${x.episodes_total} episodes` : '';
    const status = x.in_list
      ? (x.files ? `<span class="rel-pill in">${ICON.check} On your ${pcName()}${eps}</span>` : '<span class="rel-pill">In your list · not downloaded</span>')
      : x.waiting ? `<span class="rel-pill dl">In your downloads · ${x.waiting} file${x.waiting === 1 ? '' : 's'} not sorted</span>`
        : '<span class="rel-pill miss">Not on your PC</span>';
    return `<div class="rel-row${x.in_list ? ' is-in' : ''}" ${x.in_list ? `data-open="${esc(x.in_list)}"` : ''} style="--i:${Math.min(i, 14)}">
      ${x.poster ? `<img src="${esc(x.poster)}" alt="" loading="lazy">` : '<span class="rel-ph"></span>'}
      <div class="rel-text"><b>${esc(x.title)}</b><small>${[kindName(x), x.year, x.relation].filter(Boolean).map(esc).join(' · ')}${x.via ? ` (of ${esc(x.via)})` : ''}</small>${status}</div>
      ${x.in_list ? '' : `<button class="rel-add" data-r="add" data-i="${i}" title="Add to your list">+</button>`}</div>`;
  };
  const groups = KIND_GROUPS.map(([k, label]) => [label, entries.filter((x) => connectedKind(x) === k)])
    .filter(([, list]) => list.length);
  box.innerHTML = head
    + `<button class="btn btn-red rel-guide" data-r="guide">📖 Open the story guide</button>`
    + `<div class="rel-summary"><b>${entries.length}</b> connected · <span class="ok">${here.length} on your ${pcName()}</span>
        ${listed.length ? ` · ${listed.length} in your list, not downloaded` : ''} · <span class="${missing.length ? 'bad' : ''}">${missing.length} not in your list</span></div>`
    + (missing.length > 1 ? `<button class="btn btn-sm btn-white rel-all" data-r="all">+ Add all ${missing.length} missing</button>` : '')
    + (entries.length ? groups.map(([g, list]) => `<div class="rel-group"><h5>${esc(g)} <span>${list.length}</span></h5>${list.map(row).join('')}</div>`).join('')
      : '<p class="muted small">Nothing connected was found.</p>')
    + (data.note ? `<p class="muted small">${esc(data.note)}</p>` : '')
    + (data.at ? `<p class="muted small">Looked ${esc(ago(data.at))}.</p>` : '');
}

async function addRelated(wrap, x, quiet) {
  const added = await api('POST', '/api/items', { type: x.type, source: x.source, source_id: x.source_id });
  upsert(added);
  x.in_list = added.id;
  if (!quiet) toast(`Added ${added.title}`);
}

async function relatedClick(wrap, e) {
  const r = e.target.closest('[data-r]');
  if (r) {
    const entries = wrap.st.related?.entries || [];
    if (r.dataset.r === 'find') { loadRelated(wrap, true); return true; }
    if (r.dataset.r === 'guide') { openGuide(wrap); return true; }
    try {
      if (r.dataset.r === 'add') {
        r.disabled = true;
        r.classList.add('spin');
        await addRelated(wrap, entries[Number(r.dataset.i)]);
      } else if (r.dataset.r === 'all') {
        const missing = entries.filter((x) => !x.in_list);
        r.disabled = true;
        for (const [n, x] of missing.entries()) {
          r.textContent = `Adding ${n + 1} of ${missing.length}…`;
          await addRelated(wrap, x, true);
        }
        toast(`Added ${missing.length} titles`);
      }
    } catch (err) { fail(err); }
    await reload();
    loadRelated(wrap);
    return true;
  }
  const open = e.target.closest('[data-open]');
  if (open) { openTitle(open.dataset.open); return true; }
  return false;
}

async function showRecap(item) {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Previously on ${esc(item.title)}</h2><p class="sub" id="recap-sub">Up to ${epLabel(item.progress)} - no spoilers past that. Written by AI.</p>
    <div id="recap">${'<div class="skeleton"><div class="sk-lines"><i></i><i></i><i></i></div></div>'.repeat(2)}</div></div>`, 'recap-modal');
  try {
    const r = await api('POST', `/api/items/${item.id}/recap`, item.progress);
    $('#recap', wrap).innerHTML = r.known && r.points.length
      ? `<ul class="recap">${r.points.map((p, i) => `<li style="--i:${i}">${esc(p)}</li>`).join('')}</ul>`
      : `<p class="muted">${esc(r.by)} doesn't know this one well enough to recap it without guessing.</p>`;
    $('#recap-sub', wrap).textContent = `Up to ${epLabel(item.progress)} - no spoilers past that. Written by ${r.by}.`;
  } catch (e) {
    $('#recap', wrap).innerHTML = `<div class="warn">${esc(e.message)}</div>`;
  }
}

async function titleClick(wrap, e) {
  const item = findItem(wrap.dataset.itemId);
  if (e.target.closest('#related')) { await relatedClick(wrap, e); return; }
  const move = e.target.closest('[data-move]');
  if (move) {
    e.stopPropagation();
    const f = wrap.st.local.by_episode[move.dataset.move];
    const v = (wrap.st.local.videos || []).find((x) => x.path === f.path) || { ...f, kind: 'episode' };
    openMoveFile(item, v);
    return;
  }
  const famTitle = e.target.closest('[data-open-title]');
  if (famTitle) { openTitle(famTitle.dataset.openTitle); return; }
  const famPlay = e.target.closest('[data-family-play]');
  if (famPlay) {
    const v = wrap.st.local.family.files[Number(famPlay.dataset.familyPlay)];
    api('POST', `/api/items/${item.id}/play`, { path: v.path, extra: true })
      .then((res) => toast(`Playing ${v.name}${res.player === 'vlc' ? ' in VLC' : ''}`)).catch(fail);
    return;
  }
  const famMove = e.target.closest('[data-family-move]');
  if (famMove) {
    const v = wrap.st.local.family.files[Number(famMove.dataset.familyMove)];
    openMoveFile(null, { ...v, episodes: [] });
    return;
  }
  const link = e.target.closest('[data-link]');
  if (link) { api('POST', '/api/open', { url: link.dataset.link }).catch(fail); return; }
  const extra = e.target.closest('[data-extra]');
  if (extra) {
    const v = wrap.st.local.extras[Number(extra.dataset.extra)];
    api('POST', `/api/items/${item.id}/play`, { path: v.path, extra: true })
      .then((res) => toast(`Playing ${extraLabel(v)}${res.player === 'vlc' ? ' in VLC' : ''}`)).catch(fail);
    return;
  }
  const del = e.target.closest('[data-del]');
  if (del) {
    e.stopPropagation();
    deleteEpisodeFile(wrap, item, del.dataset.del);
    return;
  }
  const torBtn = e.target.closest('[data-t^="tor-"], [data-t="add-magnet"], [data-t="torrents-window"]');
  if (torBtn) { await torrentClick(wrap, item, torBtn); return; }
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
    else if (t === 'vlc') openWatch(item, null, { player: 'vlc' });
    else if (t === 'trailer') playTrailer(wrap, item);
    else if (t === 'close-trailer') { $('.tm-trailer', wrap)?.remove(); }
    else if (t === 'youtube') api('POST', '/api/open', { url: `https://www.youtube.com/watch?v=${item.trailer}` }).catch(fail);
    else if (t === 'menu') { e.stopPropagation(); openItemMenu(item, e.target.closest('[data-t]'), { inTitle: true }); }
    else if (t === 'remove') removeItem(item.id);
    else if (t === 'recap') showRecap(item);
    else if (t === 'files') openTitleFiles(item, wrap.st.local);
    else if (t === 'party') openStartParty(item);
    else if (t === 'use-mal') openUseMal(item);
    else if (t === 'unwatch') toggleWatched(item.id);
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
    } else if (t === 'set-downloads') {
      chooseDownloadsFolder();
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
  const dl = (S.library.items || {})[item.id];
  const playerName = S.status.player === 'vlc' || !S.status.mpv ? 'VLC' : 'the built-in player';
  const brokenButtons = item.local_broken ? '<button class="btn btn-white btn-sm" data-t="mark-ok">It works now</button>'
    : '<button class="btn btn-ghost btn-sm" data-t="mark-broken">Not working</button>';
  const statusLine = item.local_broken
    ? `<div class="local-status bad">⚠ Marked as not working - Watch now goes online until you fix it.
        Choose the right file or folder, or press "It works now".</div>`
    : `<div class="local-status ok">✓ Watch now plays from here in ${playerName}. If the wrong file plays or it won't play, press "Not working".</div>`;
  if (!item.local_folder) {
    if (dl) {
      line.innerHTML = `${statusLine}<span>${dl.files} file${dl.files === 1 ? '' : 's'} in your downloads${isSeries(item) ? `, ${dl.episodes.length} episode${dl.episodes.length === 1 ? '' : 's'} recognised` : ''}</span>
        <button class="btn btn-ghost btn-sm" data-t="files" title="See every file connected to this title, and move any of them">Files…</button>
        <button class="btn btn-ghost btn-sm" data-t="choose-file">Choose file…</button>
        <button class="btn btn-ghost btn-sm" data-t="choose-folder">Use a different folder…</button>${brokenButtons}`;
      return;
    }
    line.innerHTML = `<span>Not in your downloads${S.status.downloads ? '' : ' (no downloads folder set yet)'}. You can also pick a folder just for this title.</span>
      ${S.status.downloads ? '' : `<button class="btn btn-white btn-sm" data-t="set-downloads">${ICON.folder} Set downloads folder</button>`}
      <button class="btn btn-ghost btn-sm" data-t="choose-folder">${ICON.folder} Choose a folder</button>`;
    return;
  }
  let found = '';
  if (local && !local.exists) found = '<span class="bad">This folder is missing - was it moved or is the drive unplugged?</span>';
  else if (local) {
    const nVid = local.videos.length;
    found = `<span>${nVid} video file${nVid === 1 ? '' : 's'}${isSeries(item) ? `, ${local.available.length} episode${local.available.length === 1 ? '' : 's'} recognised` : ''}</span>`;
  }
  line.innerHTML = `${statusLine}<code>${esc(item.local_folder)}</code>${found}${dl ? `<span>+ ${dl.files} in your downloads</span>` : ''}
    <button class="btn btn-ghost btn-sm" data-t="choose-file">Choose file…</button>
    <button class="btn btn-ghost btn-sm" data-t="open-folder">Open folder</button>
    <button class="btn btn-ghost btn-sm" data-t="choose-folder">Change folder</button>
    ${brokenButtons}
    <button class="btn btn-ghost btn-sm" data-t="forget-folder">Forget</button>`;
}

async function loadLocal(wrap) {
  const item = findItem(wrap.dataset.itemId);
  if (!item) return;
  clearTimeout(wrap.torTimer);
  const [local, tor] = await Promise.all([
    api('GET', `/api/items/${item.id}/local`).catch(() => null),
    api('GET', `/api/items/${item.id}/torrents`).catch(() => null),
  ]);
  if (!wrap.isConnected) return;
  const sig = JSON.stringify(local?.by_episode || {});
  const changed = sig !== wrap.st.localSig;
  wrap.st.localSig = sig;
  wrap.st.local = local;
  wrap.st.torrents = tor;
  drawTorrentBox(wrap);
  // A show you only stream from a torrent can be watched together too.
  $('[data-t="party"]', wrap)?.classList.toggle('hidden', !hasFiles(item) && !Object.keys(local?.by_episode || {}).length && !local?.target);
  if (changed || !wrap.st.drawnOnce) {
    wrap.st.drawnOnce = true;
    drawFolderLine(wrap);
    drawExtras(wrap);
    drawFamily(wrap);
    if (isSeries(item)) drawEpisodes(wrap);
  }
  // While a torrent is busy, keep its progress (and the episode list) current.
  const busy = (tor?.torrents || []).some((t) => !t.paused && (t.state === 'loading' || t.wanted > t.wanted_done || t.down > 0));
  if (busy) wrap.torTimer = setTimeout(() => { if (wrap.isConnected) loadLocal(wrap); }, 3000);
}

const fmtBytes = (n) => {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
};
const TORRENT_STATE = {
  loading: 'Getting the list of files…', downloading: 'Downloading', downloading_metadata: 'Getting the list of files…',
  finished: 'Ready', seeding: 'Ready (sharing)', checking_files: 'Checking files…', checking_resume_data: 'Checking files…',
  paused: 'Paused', stopped: 'Stopped',
};

function drawTorrentBox(wrap) {
  const box = $('#torrent-box', wrap);
  if (!box) return;
  const item = findItem(wrap.dataset.itemId);
  const data = wrap.st.torrents;
  if (!data) { box.innerHTML = '<p class="muted small">Couldn\'t load this title\'s torrents.</p>'; return; }
  if (!data.available) {
    box.innerHTML = `<p class="muted small">${esc(data.error || 'Torrents aren\'t available in this copy of the app.')}</p>`;
    return;
  }
  const mins = Math.round((S.status.torrents?.delete_after || 300) / 60);
  const eps = Object.keys(data.episodes || {}).length;
  const cards = data.torrents.map((t) => {
    const pct = t.wanted ? Math.round((t.wanted_done / t.wanted) * 100) : 0;
    const vids = t.files.filter((f) => f.kind === 'video').length;
    return `<div class="tor-card" data-hash="${esc(t.hash)}">
      <div class="tor-top"><b title="${esc(t.name)}">${esc(t.name || 'Torrent')}</b>
        <span class="tor-state ${esc(t.state)}">${esc(TORRENT_STATE[t.state] || t.state)}</span></div>
      <div class="tor-meta muted small">${vids} video${vids === 1 ? '' : 's'}${isSeries(item) ? ` · ${eps} episode${eps === 1 ? '' : 's'} matched` : ''}
        ${t.wanted ? ` · ${pct}% of what you chose` : ''}${t.down ? ` · ↓ ${fmtBytes(t.down)}/s` : ''}${t.peers ? ` · ${t.peers} peer${t.peers === 1 ? '' : 's'}` : ''}</div>
      ${t.error ? `<div class="bad small">${esc(t.error)}</div>` : ''}
      <div class="btns"><button class="btn btn-ghost btn-sm" data-t="tor-manage">Files & downloads…</button>
        <button class="btn btn-ghost btn-sm" data-t="tor-pause">${t.paused ? 'Resume' : 'Pause'}</button>
        <button class="btn btn-ghost btn-sm" data-t="tor-remove">Remove</button></div></div>`;
  }).join('');
  box.innerHTML = `${cards}
    <div class="magnet-row"><input class="input" id="magnet-in" placeholder="Paste a magnet link (magnet:?xt=…)" autocomplete="off" spellcheck="false">
      <button class="btn btn-white btn-sm" data-t="add-magnet">${data.torrents.length ? 'Add another' : 'Add'}</button></div>
    <p class="muted small">Only the list of files is fetched now. An episode downloads when you play it, while you watch.</p>
    ${data.torrents.length ? `<label class="check-row keep-row"><input type="checkbox" data-t="tor-keep" ${data.keep ? 'checked' : ''}>
      <span><b>Keep downloaded episodes</b><br><span class="muted small">${data.keep ? 'Episodes stay on your computer after you watch them.'
    : `Off: an episode you watch to the end credits is deleted ${mins} minutes later. One you stop part way through is kept.`}</span></span></label>` : ''}`;
}

async function deleteEpisodeFile(wrap, item, key) {
  const f = wrap.st.local?.by_episode?.[key];
  if (!f) return;
  const [s, n] = key.split(':').map(Number);
  const what = isSeries(item) ? epLabel({ season: s, episode: n }) : item.title;
  const ok = await confirmModal(f.torrent ? `Delete ${what}?` : `Move ${what} to the ${binName()}?`,
    f.torrent ? 'What\'s downloaded of it from the torrent is deleted, and it isn\'t downloaded again unless you play it.'
      : `${f.name} goes to the ${binName()}, so you can put it back if you need to.`, 'Delete');
  if (!ok) return;
  try {
    await api('POST', `/api/items/${item.id}/delete-file`, { path: f.path });
    toast(`Deleted ${what}`);
    await reload();
    loadLocal(wrap);
  } catch (err) { fail(err); }
}
const binName = () => (S.status.platform === 'win32' ? 'Recycle Bin' : 'Trash');

async function torrentClick(wrap, item, btn) {
  const t = btn.dataset.t;
  const hash = btn.closest('[data-hash]')?.dataset.hash;
  try {
    if (t === 'torrents-window') { openTorrents(); return; }
    if (t === 'add-magnet') {
      const input = $('#magnet-in', wrap);
      const magnet = input.value.trim();
      if (!magnet) { input.focus(); toast('Paste a magnet link first'); return; }
      btn.disabled = true;
      btn.textContent = 'Adding…';
      await api('POST', `/api/items/${item.id}/torrents`, { magnet });
      toast('Torrent added - getting its list of files…');
      S.status.torrents = { ...(S.status.torrents || {}), count: (S.status.torrents?.count || 0) + 1 };
      renderNavButtons();
      await loadLocal(wrap);
    } else if (t === 'tor-keep') {
      await api('POST', `/api/items/${item.id}/torrent-keep`, { keep: btn.checked });
      await loadLocal(wrap);
    } else if (t === 'tor-manage') openTorrents(hash);
    else if (t === 'tor-pause') {
      const tor = wrap.st.torrents.torrents.find((x) => x.hash === hash);
      await api('POST', `/api/torrents/${hash}/pause`, { paused: !tor.paused });
      await loadLocal(wrap);
    } else if (t === 'tor-remove') {
      if (await removeTorrent(hash)) await loadLocal(wrap);
    }
  } catch (err) { fail(err); btn.disabled = false; if (t === 'add-magnet') btn.textContent = 'Add'; }
}

async function removeTorrent(hash) {
  const ok = await confirmModal('Remove this torrent?', 'It\'s taken off this title, and what it downloaded is deleted.', 'Remove');
  if (!ok) return false;
  await api('DELETE', `/api/torrents/${hash}`);
  toast('Torrent removed');
  await reload();
  return true;
}

/* ---- the Torrents window: every torrent and file, controlled by hand */

function openTorrents(focus = '') {
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>${ICON.download || ''} Torrents</h2>
    <p class="sub">Everything your torrents are doing. Choose which files download (and how urgently), say which episode a file is, delete files, pause or remove a torrent.</p>
    <div id="tor-list"><div class="spinner"></div></div></div>`, 'torrents-modal');
  wrap.st = { focus, sig: '' };
  const tick = async () => {
    if (!wrap.isConnected) return;
    try {
      const data = await api('GET', '/api/torrents');
      if (wrap.isConnected) drawTorrents(wrap, data);
    } catch { /* try again */ }
    wrap.timer = setTimeout(tick, 1500);
  };
  tick();
  wrap.onClose = () => clearTimeout(wrap.timer);
  wrap.addEventListener('change', async (e) => {
    const sel = e.target.closest('[data-prio]');
    if (!sel) return;
    const [hash, index] = sel.dataset.prio.split('/');
    try { await api('POST', `/api/torrents/${hash}/files/${index}`, { priority: Number(sel.value) }); wrap.st.sig = ''; } catch (err) { fail(err); }
  });
  wrap.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-tw]');
    if (!b) return;
    const hash = b.closest('[data-hash]')?.dataset.hash;
    const index = b.closest('[data-file]')?.dataset.file;
    const act = b.dataset.tw;
    try {
      if (act === 'pause' || act === 'resume') await api('POST', `/api/torrents/${hash}/pause`, { paused: act === 'pause' });
      else if (act === 'folder') await api('POST', `/api/torrents/${hash}/folder`);
      else if (act === 'remove') { if (!(await removeTorrent(hash))) return; }
      else if (act === 'delete-file') {
        if (!(await confirmModal('Delete this file?', 'What\'s downloaded of it is deleted, and it stops downloading.', 'Delete'))) return;
        await api('POST', `/api/torrents/${hash}/files/${index}/delete`);
      } else if (act === 'episode') { await editTorrentEpisode(hash, Number(index), b.dataset.current || ''); }
      else if (act === 'open-title') { closeModal(wrap); openTitle(b.dataset.item); return; }
      wrap.st.sig = '';
      refreshOpenTitle();
    } catch (err) { fail(err); }
  });
}

function drawTorrents(wrap, data) {
  const list = $('#tor-list', wrap);
  if (!list) return;
  if (!data.available) { list.innerHTML = `<p class="muted">${esc(data.error)}</p>`; return; }
  if (!data.torrents.length) { list.innerHTML = '<p class="muted">No torrents yet. Paste a magnet link on a show\'s page, under "Stream from a torrent".</p>'; return; }
  const sig = JSON.stringify(data.torrents.map((t) => [t.hash, t.paused, t.files.map((f) => [f.priority, f.episode, f.override, f.complete, f.done > 0])]));
  // Only the numbers change between updates, unless something was changed.
  if (sig === wrap.st.sig) {
    for (const t of data.torrents) {
      const card = list.querySelector(`[data-hash="${t.hash}"]`);
      if (!card) continue;
      $('.tw-state', card).textContent = TORRENT_STATE[t.state] || t.state;
      $('.tw-rates', card).textContent = torrentRates(t);
      $('.tw-bar i', card).style.width = `${t.wanted ? (t.wanted_done / t.wanted) * 100 : 0}%`;
      for (const f of t.files) {
        const row = card.querySelector(`[data-file="${f.index}"]`);
        if (!row) continue;
        $('.tw-fbar i', row).style.width = `${f.size ? (f.done / f.size) * 100 : 0}%`;
        $('.tw-pct', row).textContent = `${f.size ? Math.floor((f.done / f.size) * 100) : 0}%`;
      }
    }
    return;
  }
  wrap.st.sig = sig;
  const sorted = [...data.torrents].sort((a, b) => (b.hash === wrap.st.focus) - (a.hash === wrap.st.focus) || b.added - a.added);
  list.innerHTML = sorted.map((t) => `<div class="tw-card ${t.hash === wrap.st.focus ? 'focus' : ''}" data-hash="${esc(t.hash)}">
    <div class="tw-head"><div><b>${esc(t.name || 'Torrent')}</b>
      <div class="muted small">${t.title ? `For <button class="linkish" data-tw="open-title" data-item="${esc(t.item_id)}">${esc(t.title)}</button> · ` : ''}<span class="tw-state">${esc(TORRENT_STATE[t.state] || t.state)}</span> · <span class="tw-rates">${esc(torrentRates(t))}</span>${t.keep ? ' · keeps watched episodes' : ''}</div>
      ${t.error ? `<div class="bad small">${esc(t.error)}</div>` : ''}</div>
      <div class="btns"><button class="btn btn-ghost btn-sm" data-tw="${t.paused ? 'resume' : 'pause'}">${t.paused ? 'Resume' : 'Pause'}</button>
        <button class="btn btn-ghost btn-sm" data-tw="folder">Open folder</button>
        <button class="btn btn-ghost btn-sm" data-tw="remove">Remove</button></div></div>
    <div class="tw-bar"><i style="width:${t.wanted ? (t.wanted_done / t.wanted) * 100 : 0}%"></i></div>
    ${t.files.length ? `<div class="tw-files">${t.files.map((f) => `<div class="tw-file ${f.priority ? '' : 'skipped'}" data-file="${f.index}">
        <select class="select tw-prio" data-prio="${esc(t.hash)}/${f.index}" title="Download this file?">
          ${[[0, 'Skip'], [1, 'Normal'], [4, 'High'], [7, 'Now']].map(([v, l]) => `<option value="${v}" ${f.priority === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span class="tw-kind ${f.kind}">${f.kind === 'video' ? '▶' : f.kind === 'subtitle' ? 'CC' : '•'}</span>
        <span class="tw-name" title="${esc(f.path)}">${esc(f.path.split('/').pop())}</span>
        <button class="tw-ep ${f.override ? 'set' : ''}" data-tw="episode" data-current="${esc(f.override || '')}" title="Which episode is this?">${f.kind === 'video'
    ? (f.override === 'none' ? 'Not an episode' : f.episode ? esc(epKeyLabel(f.episode)) : 'No episode') : ''}</button>
        <span class="tw-size">${fmtBytes(f.size)}</span>
        <span class="tw-fbar"><i style="width:${f.size ? (f.done / f.size) * 100 : 0}%"></i></span><span class="tw-pct">${f.size ? Math.floor((f.done / f.size) * 100) : 0}%</span>
        ${f.done > 0 ? `<button class="tw-del" data-tw="delete-file" title="Delete this file">${ICON.trash}</button>` : '<span></span>'}</div>`).join('')}</div>`
    : '<p class="muted small"><span class="spin-dot"></span> Getting the list of files from the people sharing it…</p>'}
  </div>`).join('');
  if (wrap.st.focus) { list.querySelector('.tw-card.focus')?.scrollIntoView({ block: 'nearest' }); wrap.st.focus = ''; }
}

const torrentRates = (t) => [t.down ? `↓ ${fmtBytes(t.down)}/s` : '', t.up ? `↑ ${fmtBytes(t.up)}/s` : '',
  `${t.peers} peer${t.peers === 1 ? '' : 's'}${t.seeds ? ` (${t.seeds} sharing everything)` : ''}`].filter(Boolean).join(' · ');
const epKeyLabel = (key) => { const [s, e] = key.split(':').map(Number); return s === 0 ? `Special ${e}` : `S${s} E${e}`; };

async function editTorrentEpisode(hash, index, current) {
  return new Promise((resolve) => {
    const [cs, ce] = current && current.includes(':') ? current.split(':') : ['1', ''];
    const m = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
      <h2>Which episode is this file?</h2>
      <div class="mv-row"><label class="field"><span>Season</span><input class="input" id="te-s" type="number" min="0" value="${esc(cs)}" style="width:90px"></label>
        <label class="field"><span>Episode</span><input class="input" id="te-e" type="number" min="1" value="${esc(ce)}" style="width:110px"></label></div>
      <div class="btns" style="margin-top:16px"><button class="btn btn-white" data-x="set">Save</button>
        <button class="btn btn-ghost" data-x="auto">Work it out from the name</button>
        <button class="btn btn-ghost" data-x="none">Not an episode</button></div></div>`, 'move-modal');
    m.onClose = () => resolve();
    m.addEventListener('click', async (e) => {
      const x = e.target.closest('[data-x]')?.dataset.x;
      if (!x) return;
      const episode = x === 'auto' ? null : x === 'none' ? 'none' : `${Number($('#te-s', m).value) || 0}:${Number($('#te-e', m).value) || 1}`;
      try { await api('POST', `/api/torrents/${hash}/files/${index}`, { episode }); closeModal(m); } catch (err) { fail(err); }
    });
  });
}

// "8 of 24 episodes on your PC · missing 9-24"
function drawEpisodeSummary(wrap, item, available, streamable = new Set()) {
  const box = $('#ep-summary', wrap);
  if (!box) return;
  const seasons = item.seasons.filter((x) => x.number > 0 && x.episodes);
  const total = seasons.reduce((n, x) => n + x.episodes, 0) || item.episodes_total || 0;
  const have = [...available].filter((k) => !k.startsWith('0:')).length;
  const canStream = [...streamable].filter((k) => !k.startsWith('0:')).length;
  const streamNote = canStream ? ` · <span class="stream-note">${canStream} more can stream from the torrent</span>` : '';
  if (!have && canStream) { box.innerHTML = `<div class="ep-summary">${canStream} episode${canStream === 1 ? '' : 's'} can stream from the torrent</div>`; return; }
  if (!have || !total) { box.innerHTML = ''; return; }
  if (have >= total) { box.innerHTML = `<div class="ep-summary ok">${ICON.check} All ${total} episodes are on your ${pcName()}</div>`; return; }
  const missing = [];
  for (const x of seasons.length ? seasons : [{ number: 1, episodes: total }]) {
    let start = null;
    for (let n = 1; n <= x.episodes + 1; n++) {
      const gap = n <= x.episodes && !available.has(`${x.number}:${n}`);
      if (gap && start === null) start = n;
      if (!gap && start !== null) {
        const label = start === n - 1 ? `${start}` : `${start}-${n - 1}`;
        missing.push(seasons.length > 1 ? `S${x.number} ${label}` : label);
        start = null;
      }
    }
  }
  box.innerHTML = `<div class="ep-summary"><b>${have} of ${total} episodes</b> on your ${pcName()}
    <span class="muted">· missing ${esc(missing.slice(0, 12).join(', '))}${missing.length > 12 ? '…' : ''}</span>${streamNote}</div>`;
}

// Films, OVAs, specials and extras in the same folder as this title's files.
function drawFamily(wrap) {
  const box = $('#family', wrap);
  const fam = wrap.st.local?.family;
  if (!box) return;
  const has = fam && (fam.titles.length || fam.files.length);
  $('#family-section', wrap).classList.toggle('hidden', !has);
  if (!has) return;
  box.innerHTML = (fam.titles.length ? `<div class="family-titles">${fam.titles.map((t) => `<button class="family-title" data-open-title="${esc(t.id)}">
      ${t.poster ? `<img src="${esc(t.poster)}" alt="" loading="lazy">` : '<span class="rel-ph"></span>'}
      <span><b>${esc(t.title)}</b><small>${esc(kindName(t) || t.kind)} · ${t.files} file${t.files === 1 ? '' : 's'}</small></span></button>`).join('')}</div>` : '')
    + (fam.files.length ? `<div class="extras">${fam.files.map((v, i) => `<div class="extra-tile family-file">
      <button class="extra-play" data-family-play="${i}" title="Play">${ICON.play}</button>
      <span class="extra-name">${esc(v.kind === 'extra' ? extraLabel(v) : v.kind === 'episode' ? 'Not sorted yet' : kindName({ media_kind: v.kind }) || v.kind)}</span>
      <small title="${esc(v.rel)}">${esc(v.name)} <button class="linkish" data-family-move="${i}">Sort…</button></small></div>`).join('')}</div>` : '');
}

// Every file connected to a title, each one movable.
function openTitleFiles(item, local) {
  const files = [...(local?.videos || []), ...(local?.extras || [])];
  const what = (v) => (v.kind === 'extra' ? 'Extra' : v.episodes?.length ? `S${v.season ?? 1} E${v.episodes.join(', E')}` : kindName({ media_kind: v.kind }) || 'File');
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Files for ${esc(item.title)}</h2><p class="sub">Every file connected to this title. Move any that are in the wrong place.</p>
    <div class="file-rows">${files.map((v, i) => `<div class="file-row"><span class="file-what">${esc(what(v))}</span>
      <span class="file-name" title="${esc(v.path)}">${esc(v.name)}</span>
      ${v.dl ? `<button class="btn btn-sm btn-ghost" data-file="${i}">Move…</button>` : '<span class="muted small">this title\'s own folder</span>'}</div>`).join('') || '<p class="muted">No files.</p>'}</div></div>`, 'files-modal');
  wrap.addEventListener('click', (e) => {
    const b = e.target.closest('[data-file]');
    if (b) openMoveFile(item, files[Number(b.dataset.file)], () => closeModal(wrap));
  });
}

// Move one file: to another title or episode, out of its title, or ignore it.
function openMoveFile(item, file, done) {
  const titles = [...S.items].sort((a, b) => a.title.localeCompare(b.title));
  const ep = file.episodes?.[0] || '';
  const wrap = openModal(`<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">
    <h2>Move this file</h2><p class="q-files">${esc(file.rel || file.name)}</p>
    <label class="field"><span>It belongs to</span><select class="select" id="mv-title">
      ${titles.map((t) => `<option value="${esc(t.id)}" ${item && t.id === item.id ? 'selected' : ''}>${esc(t.title)} (${TYPE_ONE[t.type]})</option>`).join('')}</select></label>
    <div class="mv-row"><label class="field"><span>It is</span><select class="select" id="mv-kind">
        ${[['episode', 'An episode'], ['ova', 'An OVA'], ['special', 'A special'], ['movie', 'A film'], ['extra', 'An extra (opening, ending…)']]
    .map(([k, l]) => `<option value="${k}" ${(file.kind || 'episode') === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="field mv-ep"><span>Season</span><input class="input q-num" id="mv-season" type="number" min="0" value="${file.season ?? 1}"></label>
      <label class="field mv-ep"><span>Episode</span><input class="input q-num" id="mv-episode" type="number" min="1" value="${ep}"></label></div>
    <div class="btns"><button class="btn btn-red" data-mv="move">Move it</button>
      <button class="btn btn-ghost" data-mv="unsorted" title="Back to Sort your downloads; Sort now leaves it to you">Take it out - I'll sort it later</button>
      <button class="btn btn-ghost" data-mv="ignore">Ignore this file</button></div></div>`, 'move-modal');
  const showEp = () => $$('.mv-ep', wrap).forEach((el) => el.classList.toggle('hidden', !['episode', 'ova', 'special'].includes($('#mv-kind', wrap).value)));
  $('#mv-kind', wrap).addEventListener('change', showEp);
  showEp();
  wrap.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-mv]')?.dataset.mv;
    if (!a) return;
    const kind = $('#mv-kind', wrap).value;
    const body = a === 'move'
      ? { rel: file.rel, item: $('#mv-title', wrap).value, kind,
        season: ['episode', 'ova', 'special'].includes(kind) ? Number($('#mv-season', wrap).value) : null,
        episode: ['episode', 'ova', 'special'].includes(kind) ? Number($('#mv-episode', wrap).value) || null : null }
      : { rel: file.rel, item: a };
    try {
      S.library = await api('POST', '/api/library/route', body);
      closeModal(wrap);
      if (done) done();
      await reload();
      $$('#modal-root .overlay').forEach((o) => { if (o.dataset.itemId && o.st) loadLocal(o); });
      toast(a === 'move' ? 'Moved' : a === 'ignore' ? 'That file will be ignored' : 'Taken out - it\'s waiting in Sort your downloads');
    } catch (err) { fail(err); }
  });
}

// Openings, endings, trailers... found with the show's files: ready to play, kept apart from episodes.
function drawExtras(wrap) {
  const box = $('#extras', wrap);
  const extras = wrap.st.local?.extras || [];
  if (!box) return;
  $('#extras-section', wrap).classList.toggle('hidden', !extras.length);
  if (!extras.length) return;
  $('#extras-count', wrap).textContent = `${extras.length} file${extras.length === 1 ? '' : 's'}`;
  box.innerHTML = extras.map((v, i) => `<button class="extra-tile" data-extra="${i}" style="--i:${i}">
      <span class="extra-play">${ICON.play}</span><span class="extra-name">${esc(extraLabel(v))}</span>
      <small>${esc(v.rel)}</small></button>`).join('');
}

function extraLabel(v) {
  const stem = v.name.replace(/\.[^.]+$/, '').replace(/\[[^\]]*\]|\([^)]*\)/g, ' ').replace(/[._]/g, ' ');
  const m = stem.match(/\b(?:nc)?(op|ed)\s*(\d*)\b/i);
  if (/creditless\s+opening|\bncop\b/i.test(stem) || (m && m[1].toLowerCase() === 'op')) return `Opening ${m && m[2] ? m[2] : ''}`.trim();
  if (/creditless\s+ending|\bnced\b/i.test(stem) || (m && m[1].toLowerCase() === 'ed')) return `Ending ${m && m[2] ? m[2] : ''}`.trim();
  if (/\bpv\b|trailer|teaser|promo/i.test(stem)) return 'Trailer';
  if (/preview/i.test(stem)) return 'Preview';
  if (/menu/i.test(stem)) return 'Menu';
  return stem.replace(/\s+/g, ' ').trim();
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
  const files0 = st.local?.by_episode || {};
  const available = new Set(Object.keys(files0).filter((k) => !files0[k].stream));
  const streamable = new Set(Object.keys(files0).filter((k) => files0[k].stream));
  if (!count) {
    // Unknown episode count (still airing): use what's on disk, or the progress.
    const onDisk = [...available, ...streamable].filter((k) => k.startsWith(`${st.season}:`)).map((k) => Number(k.split(':')[1]));
    count = Math.max(item.progress.season === st.season ? item.progress.episode : 0, ...onDisk, 12);
  }
  const PAGE = 100;
  const pages = Math.ceil(count / PAGE);
  if (st.page >= pages) st.page = 0;

  picker.innerHTML = `${seasons.length > 1 ? `<select class="select" id="season-sel">${seasons.map((s) => `<option value="${s.number}" ${s.number === st.season ? 'selected' : ''}>${seasonName(s.number)}</option>`).join('')}</select>` : ''}
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
  const files = st.local?.by_episode || {};
  const someHere = available.size > 0;
  drawEpisodeSummary(wrap, item, available, streamable);
  const rows = [];
  for (let n = st.page * PAGE + 1; n <= Math.min(count, (st.page + 1) * PAGE); n++) {
    const d = byNum[n];
    const key = `${st.season}:${n}`;
    const watched = st.season < p.season || (st.season === p.season && n < p.episode);
    const current = st.season === p.season && n === p.episode;
    const tf = files0[key]?.torrent;
    const tags = [
      current ? '<span class="tag tag-next">UP NEXT</span>' : '',
      available.has(key) ? `<span class="tag tag-pc">ON ${pcName().toUpperCase()}${tf ? ' · TORRENT' : ''}</span>`
        : streamable.has(key) ? `<span class="tag tag-stream" title="Plays from the torrent while it downloads">STREAM${tf && tf.progress > 0 ? ` · ${Math.round(tf.progress * 100)}%` : ''}</span>`
          : someHere ? '<span class="tag tag-missing">Not downloaded</span>' : '',
      watched ? '<span class="tag tag-done">✓ Watched</span>' : '',
      current ? '' : '<button class="ep-set" title="Make this the next episode">Set as next</button>',
    ].join('');
    // Where you stopped, if you're partway through it.
    const at = savedPos(item, { season: st.season, episode: n });
    const f = files[key];
    const bar = (at ? `<div class="ep-bar" title="Stopped at ${fmtTime(at.pos)}"><i style="width:${at.dur ? Math.min(100, (at.pos / at.dur) * 100).toFixed(1) : 0}%"></i></div>
      <div class="small muted">Stopped at ${fmtTime(at.pos)}${at.dur ? ` of ${fmtTime(at.dur)}` : ''}</div>` : '')
      + (f ? `<div class="ep-file" title="${f.stream ? 'In the torrent' : esc(f.path)}"><span>${esc(f.name)}</span>${f.rel ? `<button class="ep-move" data-move="${esc(key)}" title="This file is in the wrong place? Move it">Move…</button>` : ''}
        ${!f.stream || (f.torrent && f.torrent.progress > 0) ? `<button class="ep-move ep-del" data-del="${esc(key)}" title="${f.torrent ? 'Delete what\'s downloaded of this episode' : 'Move this file to the Recycle Bin / Trash'}">${ICON.trash}</button>` : ''}</div>` : '');
    if (d) {
      rows.push(`<div class="ep ${current ? 'current' : ''} ${watched ? 'watched' : ''}" data-ep="${key}">
        <div class="ep-num">${n}</div>
        ${d.still ? `<img class="ep-thumb" src="${esc(d.still)}" alt="" loading="lazy">` : `<div class="ep-thumb">${ICON.play}</div>`}
        <div><div class="ep-title">${esc(d.name || `Episode ${n}`)}${d.runtime ? `<small>${d.runtime}m</small>` : ''}</div>
          <div class="ep-desc">${esc(d.overview)}</div>${bar}</div>
        <div class="ep-tags">${tags}</div></div>`);
    } else {
      rows.push(`<div class="ep compact ${current ? 'current' : ''} ${watched ? 'watched' : ''}" data-ep="${key}">
        <div class="ep-num">${n}</div><div><div class="ep-title">Episode ${n}</div>${bar}</div><div class="ep-tags">${tags}</div></div>`);
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
// Watch now: a file on your computer plays straight away; otherwise you choose
// between the torrent, online, or finding the file.
function openWatch(item, ep, { player = '' } = {}) {
  const wrap = openModal('', 'watch-modal');
  wrap.st = { id: item.id, ep: ep ? { ...ep } : { ...item.progress }, player };
  decideWatch(wrap);
}

async function decideWatch(wrap) {
  const item = findItem(wrap.st.id);
  const what = whatLabel(wrap);
  watchBody(wrap, `<div class="center"><div class="spinner"></div><p class="muted">Getting ${esc(what)} ready…</p></div>`);
  let info = null;
  try { info = await fetchLocal(wrap); } catch { /* choose below */ }
  if (!wrap.isConnected) return;
  const t = info?.target;
  let reason = '';
  if (t && !t.stream) {
    if (!item.local_broken) {
      try { await playFile(wrap, t, info.subtitles[0], wrap.st.player); return; } catch (e) {
        reason = `The file couldn't be played (${e.message}).`;
      }
    } else reason = 'Its file is marked as not working.';
  } else reason = `${what} isn't on your ${pcName()}.`;
  if (wrap.isConnected) watchChoice(wrap, { reason });
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
    return watchChoice(wrap, { reason: `Couldn't read ${item.title}'s folder (${e.message}).` });
  }
  if (!wrap.isConnected) return;
  if (!info.exists) {
    return watchChoice(wrap, { reason: `${item.title}'s files weren't found (moved, renamed or the drive isn't plugged in).` });
  }
  if (!info.target) {
    return watchChoice(wrap, { reason: `${what} isn't in your downloads.` });
  }
  try {
    await playFile(wrap, info.target, info.subtitles[0], wrap.st.player);
  } catch (e) {
    if (wrap.isConnected) watchChoice(wrap, { reason: `The file couldn't be played (${e.message}).` });
  }
}

async function playFile(wrap, video, sub, player = '', fromStart = false) {
  const item = findItem(wrap.st.id);
  const res = await api('POST', `/api/items/${item.id}/play`, {
    path: video.path, subtitle: sub ? sub.path : '',
    season: wrap.st.ep.season, episode: wrap.st.ep.episode,
    player: player || undefined, from_start: fromStart,
  });
  await reload();
  if (wrap.isConnected) drawPlaying(wrap, res, video, sub);
}

function drawPlaying(wrap, res, video, sub) {
  const item = findItem(wrap.st.id);
  const mpv = res.player === 'mpv';
  const body = watchBody(wrap, `<div class="center">
    <div class="result-icon">▶️</div>
    <h2>Playing ${esc(whatLabel(wrap))}${mpv ? '' : ' in VLC'}</h2>
    ${res.note ? `<div class="warn" style="justify-content:center;margin-bottom:12px">${esc(res.note)}</div>` : ''}
    <p class="sub">${esc(video.name)}<br>
      <span class="small">${res.stream ? 'Streaming from the torrent while it downloads · ' : ''}${res.resumed_at ? `Resumed at ${fmtTime(res.resumed_at)} · ` : ''}Subtitles: ${res.subtitle ? esc(res.subtitle) : sub ? esc(sub.name) : 'none found'}${res.queued ? ` · the next ${res.queued} episode${res.queued === 1 ? '' : 's'} play after it, in order` : ''}</span></p>
    ${mpv ? '<div class="live muted small" id="live">Opening the player…</div>' : ''}
    <div class="btns" style="justify-content:center"><button class="btn btn-white" data-w="done">Done</button>
      ${mpv ? '<button class="btn btn-ghost" data-w="to-vlc">Open in VLC instead</button>' : ''}
      <button class="btn btn-ghost" data-w="choose">Wrong episode? Choose the file</button></div>
    <div class="btns" style="justify-content:center;margin-top:10px">
      <button class="btn btn-ghost btn-sm" data-w="broken">File not working - watch online instead</button></div>
  </div>`);
  body.addEventListener('click', (e) => localAction(wrap, e));
  if (item.format === 'movie') $('[data-w="choose"]', body).textContent = 'Wrong file? Choose another';
  if (mpv) followPlayer(wrap);
}

// While the play window is open, show where the built-in player is.
async function followPlayer(wrap) {
  clearTimeout(wrap.followTimer);
  const live = $('#live', wrap);
  if (!live || !wrap.isConnected) return;
  try {
    const st = await api('GET', '/api/player');
    if (!st.active) { live.textContent = 'The player was closed - your place is saved.'; return; }
    if (st.error) { live.textContent = st.error; return; }
    if (st.pos != null) {
      live.innerHTML = `${esc(st.label || st.file)} · ${st.paused ? 'paused at' : 'at'} <b>${fmtTime(st.pos)}</b>${st.dur ? ` of ${fmtTime(st.dur)}` : ''}`;
    }
  } catch { /* try again */ }
  wrap.followTimer = setTimeout(() => followPlayer(wrap), 2000);
}

function watchBody(wrap, html) {
  $('.modal', wrap).innerHTML = `<button class="modal-close" aria-label="Close">×</button><div class="modal-pad">${html}</div>`;
  return $('.modal-pad', wrap);
}

function watchChoice(wrap, { reason = '' } = {}) {
  const item = findItem(wrap.st.id);
  const ep = wrap.st.ep;
  const info = wrap.st.info || {};
  const seasons = item.seasons.length ? item.seasons : [{ number: 1, episodes: item.episodes_total || 0 }];
  const picker = isSeries(item) ? `<span class="ep-picker">
      ${seasons.length > 1 ? `<select class="select" id="w-season">${seasons.map((s) => `<option value="${s.number}" ${s.number === ep.season ? 'selected' : ''}>${seasonName(s.number)}</option>`).join('')}</select>` : ''}
      <span>Episode</span><input class="input" id="w-ep" type="number" min="1" value="${ep.episode}"></span>` : '';
  const target = info.target;
  const stream = target && target.stream ? target : null;
  const local = target && !target.stream ? target : null;
  const tor = S.status.torrents || {};
  const keepNote = (wrap.st.keep ? 'Kept after you watch it.' : `Deleted ${Math.round((tor.delete_after || 300) / 60)} minutes after you finish it, unless you keep downloads.`);
  const body = watchBody(wrap, `<h2>How do you want to watch?</h2>
    <p class="sub">${esc(item.title)}${picker ? ' · ' : ''}${picker}</p>
    ${reason ? `<p class="muted small" style="margin-top:-6px">${esc(reason)}</p>` : ''}
    <div class="choices">
      ${local ? `<button class="choice" data-choice="play-anyway">${ICON.play}<b>Play the file anyway</b><span>${esc(local.name)}</span></button>` : ''}
      ${stream ? `<button class="choice primary" data-choice="stream">${ICON.download}<b>Stream from the torrent</b><span>Starts in a few seconds and plays while it downloads. ${esc(keepNote)}</span></button>`
    : tor.available ? `<div class="choice choice-form"><div>${ICON.download}<b>Stream from a torrent</b>
        <span>${(wrap.st.torrentCount || 0) ? "This show's torrent doesn't have this episode. Add another magnet link:" : "Paste this show's magnet link - only the episode you play downloads:"}</span></div>
        <div class="magnet-row"><input class="input" id="w-magnet" placeholder="magnet:?xt=…" spellcheck="false"><button class="btn btn-white btn-sm" data-choice="add-magnet">Add & stream</button></div></div>` : ''}
      <button class="choice" data-choice="online">${ICON.globe}<b>Watch online</b><span>${esc(servicesLine(item))}</span></button>
      <button class="choice" data-choice="find">${ICON.pc}<b>Find the file on my ${pcName()}</b><span>Pick the file (or a folder for this title)</span></button>
    </div>`);
  const readEp = () => {
    const s = $('#w-season', body);
    const e = $('#w-ep', body);
    if (s) ep.season = Number(s.value);
    if (e) ep.episode = Math.max(1, Number(e.value) || 1);
  };
  const reEp = async () => {
    readEp();
    try { await fetchLocal(wrap); } catch { wrap.st.info = {}; }
    if (wrap.isConnected) decideWatch(wrap);
  };
  $('#w-season', body)?.addEventListener('change', reEp);
  $('#w-ep', body)?.addEventListener('change', reEp);
  body.addEventListener('click', async (e) => {
    const c = e.target.closest('[data-choice]')?.dataset.choice;
    if (!c) return;
    readEp();
    try {
      if (c === 'stream') await playFile(wrap, stream, null, wrap.st.player);
      else if (c === 'play-anyway') {
        upsert(await api('PUT', `/api/items/${item.id}`, { local_broken: false }));
        await playFile(wrap, local, info.subtitles?.[0], wrap.st.player);
      } else if (c === 'online') drawOnline(wrap);
      else if (c === 'find') watchLocal(wrap, { choose: true });
      else if (c === 'add-magnet') await addMagnetAndStream(wrap);
    } catch (err) { fail(err); }
  });
  if (tor.available && !stream) {
    api('GET', `/api/items/${item.id}/torrents`).then((d) => {
      wrap.st.keep = d.keep;
      if (wrap.st.torrentCount !== d.torrents.length) { wrap.st.torrentCount = d.torrents.length; if (wrap.isConnected && $('#w-magnet', wrap)) watchChoice(wrap, { reason }); }
    }).catch(() => {});
  }
}

function servicesLine(item) {
  const names = (S.status.services || []).filter((x) => x.on && x.types.includes(item.type)).map((x) => x.name);
  return names.length ? `${names.slice(0, 4).join(' → ')}${names.length > 4 ? ' → …' : ''} - or pick one` : 'Your streaming services and sites (set them in Settings)';
}

async function addMagnetAndStream(wrap) {
  const item = findItem(wrap.st.id);
  const magnet = $('#w-magnet', wrap)?.value.trim();
  if (!magnet) { toast('Paste the magnet link first'); return; }
  watchBody(wrap, '<div class="center"><div class="spinner"></div><h2>Getting the torrent\'s list of files…</h2><p class="muted">From the people sharing it - usually a few seconds.</p></div>');
  await api('POST', `/api/items/${item.id}/torrents`, { magnet });
  S.status.torrents = { ...(S.status.torrents || {}), count: (S.status.torrents?.count || 0) + 1 };
  renderNavButtons();
  for (let n = 0; n < 40 && wrap.isConnected; n++) {
    const info = await fetchLocal(wrap).catch(() => null);
    const t = await api('GET', `/api/items/${item.id}/torrents`).catch(() => null);
    if (info?.target?.stream) { await playFile(wrap, info.target, null, wrap.st.player); return; }
    if (t && t.torrents.some((x) => x.files.length)) {
      watchChoice(wrap, { reason: `The torrent was added, but ${whatLabel(wrap)} wasn't found in it. Check which file is which in the Torrents window.` });
      return;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  if (wrap.isConnected) watchChoice(wrap, { reason: 'The torrent was added, but nobody sharing it could be reached yet. Try again in a minute.' });
}

// Watch online: your services in order, each with whether it has the title.
async function drawOnline(wrap) {
  const item = findItem(wrap.st.id);
  watchBody(wrap, `<div class="center"><div class="spinner"></div><p class="muted">Checking where ${esc(item.title)} streams…</p></div>`);
  let data;
  try { data = await api('GET', `/api/items/${item.id}/online-options`); } catch (e) { fail(e); watchChoice(wrap); return; }
  if (!wrap.isConnected) return;
  const region = regionName(data.region);
  const best = data.options.find((o) => o.kind === 'search' || o.has_it);
  const body = watchBody(wrap, `<h2>Watch ${esc(whatLabel(wrap))} online</h2>
    <p class="sub">Opens in ${browserName()}. ${data.streaming.length ? `In ${esc(region)} it streams on ${esc(data.streaming.join(', '))}.` : ''}</p>
    ${data.message ? `<p class="note">${esc(data.message)}</p>` : ''}
    <div class="choices">${data.options.map((o) => `<button class="choice ${o === best ? 'primary' : ''}" data-svc="${esc(o.id)}">${ICON.globe}<b>${esc(o.name)}</b>
      <span>${o.kind === 'search' ? 'Search the site' : o.has_it ? `✓ Has it in ${esc(region)}` : `Not on it in ${esc(region)} (by TMDB) - search anyway`}${o === best ? ' · best match' : ''}</span></button>`).join('')
    || `<p class="muted">No streaming services or sites for ${esc(TYPE_PLURAL[item.type].toLowerCase())} yet - add them in Settings.</p>`}</div>
    <div class="btns" style="margin-top:14px"><button class="btn btn-ghost btn-sm" data-svc="">${data.options.length ? 'Best match' : 'Open the fallback site'}</button>
      <button class="btn btn-ghost btn-sm" data-w="back">Back</button></div>`);
  body.addEventListener('click', (e) => {
    if (e.target.closest('[data-w="back"]')) { decideWatch(wrap); return; }
    const b = e.target.closest('[data-svc]');
    if (b) watchOnline(wrap, { service: b.dataset.svc });
  });
}

async function startLocal(wrap) {
  let item = findItem(wrap.st.id);
  if (!hasFiles(item) || item.local_broken) {
    watchBody(wrap, '<div class="center"><div class="spinner"></div><p class="muted">Choose the folder in the window that just opened…</p></div>');
    item = await chooseFolder(item);
    if (!wrap.isConnected) return;
    if (!item) { watchChoice(wrap); return; }
  }
  autoLocal(wrap);
}

async function watchLocal(wrap, { path = '', choose = false } = {}) {
  let item = findItem(wrap.st.id);
  if (!hasFiles(item)) {
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
  const footer = `<div class="footer-line">${ICON.folder.replace('<svg', '<svg width="16" height="16"')}<code>${esc(item.local_folder || S.status.downloads || '')}</code>
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
  const resumeAt = savedPos(item, wrap.st.ep);
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
      <small>Subtitle files with the same name as the video load automatically. ${series ? 'The next episodes play after it, in order, with their matching subtitles.' : ''}</small></label>
    ${resumeAt ? `<label class="check-row"><input type="checkbox" id="w-start-over"> Start from the beginning (you stopped at ${fmtTime(resumeAt.pos)})</label>` : ''}
    <div class="btns"><button class="btn btn-red" data-w="play">${ICON.play} ${resumeAt ? `Resume at ${fmtTime(resumeAt.pos)}` : 'Play'}</button>
      <button class="btn btn-ghost" data-w="play-vlc">Play in VLC</button>
      <button class="btn btn-ghost" data-w="reveal">Show in folder</button></div>
    ${info.videos.length > 1 ? `<details style="margin-top:16px" ${choose ? 'open' : ''}><summary>All files in the folder (${info.videos.length}) - click one to pick it</summary>${videoList}</details>` : ''}
    ${footer}`);
  if (!S.status.mpv && !S.status.vlc) {
    body.insertAdjacentHTML('afterbegin', `<div class="warn" style="margin-bottom:16px">No player was found on this ${pcName()}. Install VLC from videolan.org, or set its location in Settings.</div>`);
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
    } else if (w === 'play' || w === 'play-vlc') {
      const sel = $('#w-sub', wrap).value;
      const sub = info.subtitles[Number(sel)];
      const btn = e.target.closest('[data-w]');
      btn.disabled = true;
      if (findItem(item.id).local_broken) {
        upsert(await api('PUT', `/api/items/${item.id}`, { local_broken: false }));
      }
      await playFile(wrap, info.target, sub, w === 'play-vlc' ? 'vlc' : '', !!$('#w-start-over', wrap)?.checked);
    } else if (w === 'to-vlc') {
      const res = await api('POST', '/api/player/vlc');
      toast(`Continuing ${res.file} in VLC`);
      closeModal(wrap);
    }
  } catch (err) {
    fail(err);
    const btn = e.target.closest('[data-w]');
    if (btn) btn.disabled = false;
  }
}

async function watchOnline(wrap, { reason = '', fix = false, service = '' } = {}) {
  const item = findItem(wrap.st.id);
  watchBody(wrap, `<div class="center"><div class="spinner"></div><p class="muted">Opening ${esc(item.title)}…</p></div>`);
  try {
    if (isSeries(item)) {
      upsert(await api('PUT', `/api/items/${item.id}`, { progress: wrap.st.ep }));
    }
    const res = await api('POST', `/api/items/${item.id}/online`, { service });
    if (!wrap.isConnected) return;
    const region = regionName(res.region);
    const body = watchBody(wrap, `<div class="center">
      ${reason ? `<div class="warn" style="margin-bottom:18px;justify-content:center">${esc(reason)}</div>` : ''}
      <div class="result-icon">${res.has_it ? '✅' : '🔎'}</div>
      <h2>Opened ${esc(res.service)}</h2>
      <p class="sub">${res.kind === 'check' ? (res.has_it ? `${esc(item.title)} is on ${esc(res.service)} in ${esc(region)}.` : `Searched ${esc(res.service)} for ${esc(item.title)}.`)
        : res.kind === 'fallback' ? `None of your services or sites fit, so ${esc(res.service)} opened. Add your own in Settings.`
        : `Searched ${esc(res.service)} for ${esc(item.title)}.`} It's open in ${browserName()}${isSeries(item) ? ` - you're up to ${epLabel(wrap.st.ep)}` : ''}.</p>
      ${res.services.length ? `<div class="muted small">Streaming in ${esc(region)} on:</div><div class="services" style="justify-content:center">${res.services.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : ''}
      ${res.message ? `<p class="note">${esc(res.message)}</p>` : ''}
      <div class="btns" style="justify-content:center;margin-top:16px"><button class="btn btn-white" data-w="done">Done</button>
      <button class="btn btn-ghost" data-w="again">Open the page again</button>
      <button class="btn btn-ghost" data-w="other">Somewhere else</button>
      ${fix ? `<button class="btn btn-ghost" data-w="fix">Fix the file on my ${pcName()}</button>` : ''}</div></div>`);
    body.addEventListener('click', (e) => {
      const w = e.target.closest('[data-w]')?.dataset.w;
      if (w === 'done') closeModal(wrap);
      if (w === 'again') api('POST', '/api/open', { url: res.url }).catch(fail);
      if (w === 'other') drawOnline(wrap);
      if (w === 'fix') watchLocal(wrap, { choose: true });
    });
    render(); refreshOpenTitle();
  } catch (e) {
    fail(e);
    if (wrap.isConnected) watchChoice(wrap);
  }
}

/* ============================================================ settings */

// Your streaming services and sites, in order (Settings → Watching online).
function drawServices(wrap) {
  const box = $('#svc-list', wrap);
  if (!box) return;
  const kinds = [['anime', 'Anime'], ['tv', 'TV'], ['movie', 'Movies']];
  box.innerHTML = wrap.svc.length ? wrap.svc.map((x, i) => `<div class="svc-row ${x.on ? '' : 'off'}" data-i="${i}">
      <label class="svc-on" title="Use it"><input type="checkbox" class="svc-on-box" ${x.on ? 'checked' : ''}></label>
      <div class="svc-main">
        <div class="svc-line"><input class="input svc-name" value="${esc(x.name)}" placeholder="Name">
          <input class="input svc-url" value="${esc(x.url)}" placeholder="https://site/search?q={title}" spellcheck="false"></div>
        <div class="svc-line small">${kinds.map(([k, l]) => `<label class="svc-chip"><input type="checkbox" class="svc-type" value="${k}" ${x.types.includes(k) ? 'checked' : ''}>${l}</label>`).join('')}
          <label class="svc-chip check" title="Only use it when TMDB says it has the title in your country"><input type="checkbox" class="svc-check" ${x.check.length ? 'checked' : ''}>Only when it has it</label></div>
      </div>
      <div class="svc-tools">
        <button type="button" class="svc-btn" data-s="svc-try" title="Try it with a search">Try</button>
        <button type="button" class="svc-btn" data-s="svc-up" title="Move up" ${i ? '' : 'disabled'}>↑</button>
        <button type="button" class="svc-btn" data-s="svc-down" title="Move down" ${i < wrap.svc.length - 1 ? '' : 'disabled'}>↓</button>
        <button type="button" class="svc-btn danger" data-s="svc-del" title="Remove">✕</button></div></div>`).join('')
    : '<p class="muted small">No services or sites - online, the "If none of them fit" site opens.</p>';
}

function readServices(wrap) {
  $$('.svc-row', wrap).forEach((row) => {
    const x = wrap.svc[Number(row.dataset.i)];
    if (!x) return;
    x.on = $('.svc-on-box', row).checked;
    x.name = $('.svc-name', row).value.trim();
    x.url = $('.svc-url', row).value.trim();
    x.types = $$('.svc-type', row).filter((c) => c.checked).map((c) => c.value);
    const check = $('.svc-check', row).checked;
    x.check = check ? (x.check.length ? x.check : [x.name]) : [];
  });
  return wrap.svc;
}

async function serviceAction(wrap, a, row) {
  readServices(wrap);
  const i = Number(row?.dataset.i);
  const list = wrap.svc;
  if (a === 'svc-del') list.splice(i, 1);
  else if (a === 'svc-up' && i > 0) [list[i - 1], list[i]] = [list[i], list[i - 1]];
  else if (a === 'svc-down' && i < list.length - 1) [list[i + 1], list[i]] = [list[i], list[i + 1]];
  else if (a === 'svc-try') {
    const x = list[i];
    if (!x.url) { toast('Paste its search link first', 'error'); return; }
    const sample = x.types.includes('anime') && !x.types.includes('movie') ? 'Frieren' : 'Inception';
    await api('POST', '/api/open', { url: x.url.replace('{title}', encodeURIComponent(sample)) });
    toast(`Opened a search for "${sample}" - check it shows results`);
    return;
  }
  drawServices(wrap);
}

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
    <div class="btns" style="margin:-4px 0 14px"><button class="btn btn-ghost btn-sm" type="button" data-s="import-mal">Import my MyAnimeList list…</button></div>
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
      <small>Used to check which of your streaming services have a title where you are.</small></label>
    <label class="field"><span>Browser</span>
      <div class="input-row"><input class="input" name="browser_path" value="${esc(s.browser_path)}" placeholder="${esc(data.status.browser || (mac ? '/Applications/Opera GX.app' : 'Opera GX: ...\\AppData\\Local\\Programs\\Opera GX\\launcher.exe'))}">
      <button class="btn btn-ghost btn-sm" type="button" data-s="browser">Browse</button></div>
      <small>${data.status.browser ? `<span class="ok">✓ Using:</span> ${esc(data.status.browser)}. Leave empty to keep this.`
        : `<span class="bad">Opera GX wasn't found</span> - links open in your default browser. ${mac ? 'Browse to Opera GX in Applications' : "Browse to Opera GX's launcher.exe"} to use it.`}
        Your streaming services, sites and trailers open here.</small></label>
    <div class="field"><span>Your streaming services and sites</span>
      <div id="svc-list"></div>
      <div class="svc-add"><select class="select" id="svc-preset"><option value="">+ Add a service or site…</option></select></div>
      <small>Watch now (online) goes down this list and opens the first one that fits: a service you subscribe to
        (ticked "only when it has it") only when TMDB says it has the title in your country, a site every time. You can also pick any of them
        when you watch. For a site: search for any title on it, copy the address of the results page, and put <b>{title}</b> where the title was.</small></div>
    <label class="field"><span>If none of them fit, open</span><input class="input" name="fallback_url" value="${esc(s.fallback_url)}"></label>
    <h4>Your downloads</h4>
    <label class="field"><span>Downloads folder</span>
      <div class="input-row"><input class="input" name="downloads_folder" value="${esc(s.downloads_folder)}" placeholder="The one folder all your downloads go into">
      <button class="btn btn-ghost btn-sm" type="button" data-s="dl-browse">Choose…</button></div>
      <small>${S.library.error ? `<span class="bad">${esc(S.library.error)}</span>`
        : s.downloads_folder ? `<span class="ok">✓</span> ${S.library.videos || 0} videos found, ${Object.keys(S.library.items || {}).length} titles matched${(S.library.questions || []).length ? `, ${S.library.questions.length} to sort` : ''}.`
        : 'Files are matched to your titles by their names; the app asks about any it isn\'t sure of.'}
        Each computer has its own downloads folder.</small></label>
    <h4>AI helpers <span class="tag-optional">optional</span></h4>
    <p class="muted small" style="margin-top:-4px">Everything works without them: the app's own rules and MyAnimeList / TMDB searches sort your downloads.
      With a key, the AI also sorts files those couldn't place (Sort now with AI), writes the story guide in Everything connected, suggests what to watch (I'm in the mood for…) and writes "previously on" recaps.
      They're asked in this order; when one has run out for the day or is overloaded, the next one answers, and the one that ran out is left alone for a while.
      Only names are sent (file names and the titles in your list), never your files.</p>
    ${aiKeyRow(s, data.status, 'gemini', 'Gemini (Google) - free', 'gemini_api_key',
      'Go to <b>aistudio.google.com</b>, sign in with your Google account, click <b>Get API key</b> → Create API key. Google may use what\'s sent on the free tier to improve its products.')}
    ${aiKeyRow(s, data.status, 'groq', 'Groq - free', 'groq_api_key',
      'Go to <b>console.groq.com</b>, sign up (no card needed), open <b>API Keys</b> → Create API Key. Fast open models such as gpt-oss and Llama.')}
    ${aiKeyRow(s, data.status, 'openrouter', 'OpenRouter - free models', 'openrouter_api_key',
      'Go to <b>openrouter.ai</b>, sign up, open <b>Keys</b> → Create Key. Only its free models are used (about 50 requests a day, shared between them).')}
    <h4>Watching on your ${pcName()}</h4>
    <label class="field"><span>Player</span>
      <select class="select" name="player">
        <option value="mpv" ${s.player === 'mpv' ? 'selected' : ''}>Built-in player (mpv) - remembers where you stopped</option>
        <option value="vlc" ${s.player === 'vlc' ? 'selected' : ''}>VLC</option></select>
      <small>${data.status.mpv ? '<span class="ok">✓ Built-in player ready.</span>' : '<span class="bad">The built-in player is missing</span> - VLC is used instead.'}
        You can always pick "Play in VLC" for a single episode.</small></label>
    <label class="field"><span>VLC location</span>
      <div class="input-row"><input class="input" name="vlc_path" value="${esc(s.vlc_path)}" placeholder="${esc(data.status.vlc || (mac ? '/Applications/VLC.app' : 'C:\\Program Files\\VideoLAN\\VLC\\vlc.exe'))}">
      <button class="btn btn-ghost btn-sm" type="button" data-s="vlc">Browse</button></div>
      <small>${data.status.vlc ? `<span class="ok">✓ Found VLC:</span> ${esc(data.status.vlc)}. Leave empty to use this.` : `<span class="bad">VLC not found.</span> Install it from videolan.org or browse to ${mac ? 'VLC in Applications' : 'vlc.exe'}.`}</small></label>
    <label class="check-row"><input type="checkbox" name="queue_next_episodes" ${s.queue_next_episodes ? 'checked' : ''}> Play the following episodes after the one you start</label>
    <label class="check-row"><input type="checkbox" name="skip_intro" ${s.skip_intro ? 'checked' : ''}> Skip openings and endings in the built-in player (when the file marks them with chapters; press <b>Shift+S</b> while watching to toggle)</label>
    <h4>Watch together</h4>
    <label class="field"><span>Your name in watch parties</span>
      <input class="input" name="party_name" value="${esc(s.party_name)}" placeholder="e.g. Ali"></label>
    <label class="field"><span>Watch-party server</span>
      <input class="input" name="party_server" value="${esc(s.party_server)}" placeholder="syncplay.pl:8999">
      <small>A Syncplay server: the free public one works. Only the host's setting counts - it's in the invite.</small></label>
    <div class="btns" style="margin-top:22px"><button class="btn btn-red" type="submit">Save</button>
      <button class="btn btn-ghost" type="button" data-s="cancel">Cancel</button></div>
  </form>`, 'settings-modal');
  const form = $('form', wrap);
  wrap.svc = JSON.parse(JSON.stringify(s.services || []));
  drawServices(wrap);
  api('GET', '/api/services/presets').then(({ presets }) => {
    wrap.presets = presets;
    $('#svc-preset', wrap).innerHTML = `<option value="">+ Add a service or site…</option>${presets.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join('')}<option value="site">Another site (a search link)…</option>`;
  }).catch(() => {});
  $('#svc-preset', wrap).addEventListener('change', (e) => {
    const v = e.target.value;
    if (!v) return;
    readServices(wrap);
    const p = v === 'site' ? { name: '', url: '', check: [] } : wrap.presets[Number(v)];
    wrap.svc.push({ id: '', name: p.name, url: p.url, check: [...p.check], types: ['anime', 'tv', 'movie'], on: true });
    e.target.value = '';
    drawServices(wrap);
    $$('.svc-row', wrap).pop()?.querySelector(v === 'site' ? '.svc-url' : '.svc-name')?.focus();
  });
  const values = () => {
    const f = new FormData(form);
    return {
      tmdb_api_key: f.get('tmdb_api_key'), mal_client_id: f.get('mal_client_id'), region: f.get('region'),
      fallback_url: f.get('fallback_url'), services: readServices(wrap),
      vlc_path: f.get('vlc_path'), browser_path: f.get('browser_path'), sync_folder: f.get('sync_folder'),
      downloads_folder: f.get('downloads_folder'), player: f.get('player'), queue_next_episodes: form.queue_next_episodes.checked,
      gemini_api_key: f.get('gemini_api_key'), groq_api_key: f.get('groq_api_key'), openrouter_api_key: f.get('openrouter_api_key'),
      skip_intro: form.skip_intro.checked,
      party_name: f.get('party_name'), party_server: f.get('party_server'),
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
      } else if (a.startsWith('svc-')) {
        serviceAction(wrap, a, e.target.closest('[data-i]'));
      } else if (a === 'dl-browse') {
        const { path } = await api('POST', '/api/browse', { kind: 'folder', title: 'Choose your downloads folder', initial: form.downloads_folder.value });
        if (path) form.downloads_folder.value = path;
      } else if (a === 'sync-browse') {
        const { path } = await api('POST', '/api/browse', { kind: 'folder', title: 'Choose a folder both computers sync', initial: form.sync_folder.value });
        if (path) {
          // Applied straight away: no need to remember to press Save.
          form.sync_folder.value = path;
          await save();
          toast('Syncing through that folder now');
          closeModal(wrap); openSettings();
        }
      } else if (a === 'ai-test') {
        await testAiKey(form, e.target.closest('[data-s]'));
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
