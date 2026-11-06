/* The phone's own video player (Use on your phone: webplay.py on the PC).

   Nothing plays on the PC. For each episode the PC gives an address of its own (a short-lived
   ticket): the video as it is, repackaged or converted (HLS), or a site's video passed through.
   Safari plays HLS itself; elsewhere hls.js (bundled) does. Once a second the player tells the
   PC where it is (your place is saved from that) and takes what the PC asks of it (a watch
   party: pause, play, seek; messages). Subtitles are drawn by the page, or by the phone in its
   own full screen. Loaded before app.js; uses its helpers when it runs. */
'use strict';

const WP = { el: null, s: null };
const wpCaps = () => {
  const v = document.createElement('video');
  return { hevc: !!v.canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"'), hls: !!v.canPlayType('application/vnd.apple.mpegurl') };
};
const fmtT = (t) => {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
};
const wpPref = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const wpSetPref = (k, v) => { try { localStorage.setItem(k, v); } catch { /* fine */ } };

function wpBuild() {
  if (WP.el) return WP.el;
  const el = document.createElement('div');
  el.id = 'wp';
  el.className = 'wp hidden';
  el.innerHTML = `<video class="wp-video" playsinline webkit-playsinline x-webkit-airplay="allow" preload="auto"></video>
    <div class="wp-cc" aria-live="off"></div>
    <div class="wp-tap"></div>
    <div class="wp-loading hidden"><div class="spinner"></div><p class="wp-loading-text">Getting it ready…</p></div>
    <div class="wp-osd hidden"></div>
    <div class="wp-error hidden" role="alert"><h3>It won't play</h3><p class="wp-error-why"></p><div class="btns wp-error-btns"></div></div>
    <div class="wp-upnext hidden"><div><span class="muted small">Up next</span><b class="wp-upnext-what"></b></div>
      <div class="btns"><button class="btn btn-white btn-sm" data-wp="next-now">Play now <span class="wp-count"></span></button>
      <button class="btn btn-ghost btn-sm" data-wp="next-cancel">Cancel</button></div></div>
    <div class="wp-ui">
      <div class="wp-top"><button class="wp-btn" data-wp="close" aria-label="Close the player">✕</button>
        <div class="wp-title"><b class="wp-show"></b><span class="wp-ep"></span></div>
        <button class="wp-btn wp-airplay hidden" data-wp="airplay" aria-label="AirPlay">${wpIco('airplay')}</button>
        <button class="wp-btn wp-pip" data-wp="pip" aria-label="Picture in Picture">${wpIco('pip')}</button></div>
      <div class="wp-mid"><button class="wp-btn wp-big" data-wp="prev" aria-label="Previous episode">${wpIco('prev')}</button>
        <button class="wp-btn wp-big wp-play" data-wp="play" aria-label="Play">${wpIco('play')}</button>
        <button class="wp-btn wp-big" data-wp="next" aria-label="Next episode">${wpIco('next')}</button></div>
      <div class="wp-bottom">
        <div class="wp-bar"><div class="wp-buffered"></div><div class="wp-played"></div>
          <input class="wp-seek" type="range" min="0" max="1000" step="1" value="0" aria-label="Where in the video"></div>
        <div class="wp-row"><span class="wp-time">0:00</span><span class="wp-dur">0:00</span><span class="wp-spacer"></span>
          <button class="wp-btn wp-src hidden" data-wp="source" title="Not working? Try another server">Not working?</button>
          <button class="wp-btn" data-wp="cc" aria-label="Subtitles">CC</button>
          <button class="wp-btn" data-wp="menu" aria-label="Audio, subtitles, quality, speed">${wpIco('gear')}</button>
          <button class="wp-btn" data-wp="full" aria-label="Full screen">${wpIco('full')}</button></div></div>
    </div>
    <div class="wp-sheet hidden" role="dialog" aria-label="Player settings"></div>`;
  document.body.appendChild(el);
  WP.el = el;
  wpWire(el);
  return el;
}

function wpIco(name) {
  const d = {
    play: 'M8 5v14l11-7z', pause: 'M6 5h4v14H6zm8 0h4v14h-4z', prev: 'M6 6h2v12H6zm3.5 6 8.5 6V6z',
    next: 'M16 6h2v12h-2zM6 18l8.5-6L6 6z', gear: 'M19.1 12.9a7.3 7.3 0 0 0 0-1.9l2-1.6-1.9-3.3-2.4 1a7 7 0 0 0-1.6-.9L14.8 3h-3.8l-.4 2.5c-.6.2-1.1.5-1.6.9l-2.4-1-1.9 3.3 2 1.6a7.3 7.3 0 0 0 0 1.9l-2 1.6 1.9 3.3 2.4-1c.5.4 1 .7 1.6.9l.4 2.5h3.8l.4-2.5c.6-.2 1.1-.5 1.6-.9l2.4 1 1.9-3.3zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z',
    full: 'M5 5h5v2H7v3H5zm9 0h5v5h-2V7h-3zM5 14h2v3h3v2H5zm12 3v-3h2v5h-5v-2z',
    pip: 'M19 7h-8v6h8zm2-4H3a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h18a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 16H3V5h18z',
    airplay: 'M6 22h12l-6-6zM21 3H3a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4v-2H3V5h18v12h-4v2h4a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2z',
  }[name];
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
}

const wpV = () => WP.el.querySelector('video');
const wpQ = (sel) => WP.el.querySelector(sel);

// ---- opening it

function openWebPlayer(launch) {
  if (WP.s && WP.s.sid === launch.session && !WP.s.closing) return;      // (already open)
  const el = wpBuild();
  wpTeardownStream();
  WP.s = { launch, sid: launch.session, entries: launch.entries, index: launch.index || 0, seq: 0, token: 0,
    media: null, offset: 0, duration: 0, quality: wpPref('wp-quality', ''), speed: 1, burn: null, cues: [],
    subOn: wpPref(`wp-subs:${launch.item_id}`, 'on') !== 'off', subPick: 0, upnextShown: false, closing: false };
  el.classList.remove('hidden');
  document.body.classList.add('wp-open');
  wpQ('.wp-show').textContent = launch.title || '';
  wpLoad(WP.s.index, launch.start || 0, { paused: launch.paused });
  clearInterval(WP.tick);
  WP.tick = setInterval(wpReport, 1000);
  wpListen();
  wpShowUi();
}

// What the PC asks of the player (a watch party: pause, play, seek, move on), as it happens.
function wpListen() {
  try { WP.events?.close(); } catch { /* gone */ }
  const s = WP.s;
  if (!s || typeof EventSource === 'undefined') return;
  const es = new EventSource(`/api/web/${s.sid}/events?after=${s.seq}`);
  es.onmessage = (e) => { if (WP.s === s) { try { wpCommands(JSON.parse(e.data).commands); } catch { /* fine */ } } };
  es.addEventListener('closed', () => es.close());
  WP.events = es;
}

// Each command once, in order (it may come both ways: the events and the report's answer).
function wpCommands(list) {
  const s = WP.s;
  for (const c of list || []) {
    if (!s || c.n <= s.seq) continue;
    s.seq = c.n;
    wpCommand(c);
  }
}

async function wpLoad(index, at = 0, { paused = false, reload = false } = {}) {
  const s = WP.s;
  if (!s) return;
  const token = ++s.token;
  s.index = index;
  s.media = null;                                    // (no position told for it until it's open)
  s.pendingSeek = null;
  s.upnextShown = false;
  wpQ('.wp-upnext').classList.add('hidden');
  wpHideError();
  const e = s.entries[index] || {};
  const label = wpEpLabel(e);
  wpQ('.wp-ep').textContent = label ? ` · ${label}` : '';
  wpLoading(reload ? 'Getting it again…' : `Getting ${label || 'it'} ready…`);
  wpReport(true);
  let media;
  try {
    media = await api('POST', `/api/web/${s.sid}/open`, { index, at, caps: wpCaps(), quality: s.quality || undefined,
      burn: s.burn ?? undefined });
  } catch (err) {
    if (token !== s.token) return;                    // (moved on meanwhile: Next pressed again)
    if (err.code === 'closed') { wpClose(true); return; }
    wpError(err.message, true);
    return;
  }
  if (token !== s.token || WP.s !== s) return;
  wpAttach(media, at, paused);
}

function wpTeardownStream(empty = true) {
  if (WP.hls) { try { WP.hls.destroy(); } catch { /* gone */ } WP.hls = null; }
  if (WP.el) {
    const v = wpV();
    v.pause();
    // (when another video follows, its address simply takes this one's place: an empty one
    // in between would leave an error behind it)
    if (empty) { v.removeAttribute('src'); v.load(); }
    [...v.textTracks].forEach((t) => { t.mode = 'disabled'; });
  }
}

async function wpAttach(media, at, paused) {
  const s = WP.s;
  const v = wpV();
  wpTeardownStream(false);
  s.media = media;
  s.ready = 0;
  s.offset = media.offset || 0;
  s.duration = media.duration || 0;
  s.cues = [];
  s.subPick = Math.min(s.subPick, Math.max(0, (media.subs || []).length - 1));
  wpQ('.wp-src').classList.toggle('hidden', media.how !== 'site');
  // Apple's Safari plays HLS best itself (and lists its audio tracks); elsewhere hls.js does,
  // where it can (its audio tracks can be switched), else the browser's own.
  const apple = /Apple/.test(navigator.vendor || '');        // (Safari and every iPhone browser: Apple's WebKit)
  const native = v.canPlayType('application/vnd.apple.mpegurl');
  if (media.type === 'hls' && !(apple && native)) {
    if (!window.Hls) await wpScript('/vendor/hls-1.5.17.min.js');
  }
  if (media.type === 'hls' && !(apple && native) && window.Hls && window.Hls.isSupported()) {
    WP.hls = new window.Hls({ maxBufferLength: 60, backBufferLength: 30 });
    WP.hls.on(window.Hls.Events.ERROR, (_, d) => {
      if (d.fatal) {
        if (d.type === window.Hls.ErrorTypes.NETWORK_ERROR && (d.response?.code === 503 || d.response?.code === 0)) WP.hls.startLoad();
        else wpError(wpWhy(d.response?.code, d.details), true);
      }
    });
    WP.hls.loadSource(media.src);
    WP.hls.attachMedia(v);
  } else if (media.type === 'hls' && !native) {
    wpError("This browser can't play this kind of video.", false);
    return;
  } else {
    v.src = media.src;
  }
  v.playbackRate = s.speed;
  const target = Math.max(0, (at || 0) - s.offset);
  const begin = async () => {
    if (target > 1) await wpSeekRel(target);
    s.pendingSeek = null;
    wpLoading(false);
    wpSubsLoad();
    wpAudioPick();
    wpMediaSession();
    if (!paused) v.play().catch(() => wpShowUi(true));      // (no tap yet on this page: the play button shows)
  };
  v.addEventListener('loadedmetadata', begin, { once: true });
}

function wpScript(src) {
  return new Promise((resolve) => {
    const tag = document.createElement('script');
    tag.src = src;
    tag.onload = resolve;
    tag.onerror = resolve;
    document.head.appendChild(tag);
  });
}

// An episode's name without the show's in front of it (the show's is there already).
function wpEpLabel(e) {
  const title = WP.s?.launch?.title || '';
  let label = e.label || '';
  if (title && label.toLowerCase().startsWith(title.toLowerCase())) label = label.slice(title.length).replace(/^[\s:·–-]+/, '');
  return label;
}

// ---- where it is (the episode's own time = where this video starts + the video's time)

const wpNow = () => (WP.s ? WP.s.offset + (wpV().currentTime || 0) : 0);
const wpTotal = () => {
  const s = WP.s;
  const d = wpV().duration;
  return s.duration || (Number.isFinite(d) ? s.offset + d : 0);
};
function wpSeekableEnd() {
  // How far the video can be played: what the browser says, or what the PC has made of it.
  const v = wpV();
  const r = v.seekable;
  const end = r && r.length ? r.end(r.length - 1) : 0;
  return Math.max(Number.isFinite(end) ? end : 0, WP.s?.ready || 0);
}

async function wpSeekRel(rel) {
  const v = wpV();
  // A video still being made: wait for it to get there (a few seconds for a file on the PC).
  const until = Date.now() + 45000;
  while (rel > wpSeekableEnd() + 0.5 && Date.now() < until && WP.s) {
    wpLoading('Getting there…');
    await new Promise((r) => setTimeout(r, 700));
  }
  wpLoading(false);
  v.currentTime = Math.min(rel, Math.max(0, wpSeekableEnd() - 0.2) || rel);
}

async function wpSeek(t) {
  const s = WP.s;
  if (!s || !s.media) return;
  t = Math.max(0, Math.min(t, (wpTotal() || t) - 1));
  const rel = t - s.offset;
  if (rel >= 0 && rel <= wpSeekableEnd() + 2) { wpV().currentTime = rel; wpReport(true); return; }
  if (s.media.how === 'site' || s.media.how === 'direct') { wpV().currentTime = Math.max(0, rel); return; }
  // While it gets there, that's where it is (a watch party mustn't take the old place for a seek back).
  s.pendingSeek = t;
  wpLoading('Getting there…');
  let r;
  try { r = await api('POST', `/api/web/${s.sid}/seek`, { t, caps: wpCaps(), quality: s.quality || undefined, burn: s.burn ?? undefined }); } catch (err) { s.pendingSeek = null; wpError(err.message, true); return; }
  if (r.restart) { wpAttach(r, t, wpV().paused); return; }
  await wpSeekRel(rel);
  s.pendingSeek = null;
}

// ---- telling the PC (and hearing from it)

async function wpReport(force) {
  const s = WP.s;
  if (!s || (s.busy && !force)) return;
  const v = wpV();
  s.busy = true;
  try {
    const r = await api('POST', `/api/web/${s.sid}/state`, {
      index: s.index, pos: s.pendingSeek ?? (s.media ? wpNow() : null), dur: s.media ? wpTotal() || null : null,
      paused: v.paused, after: s.seq,
    });
    if (WP.s !== s) return;
    if (r.ready != null) s.ready = r.ready;
    wpCommands(r.commands);
    if (r.ended && !s.closing) wpClose(true);
  } catch (err) {
    if (err.code === 'closed') wpClose(true);
  } finally { s.busy = false; }
}

function wpCommand(c) {
  const v = wpV();
  if (c.do === 'pause') v.pause();
  else if (c.do === 'play') v.play().catch(() => wpShowUi(true));
  else if (c.do === 'seek') wpSeek(c.to);
  else if (c.do === 'speed') { v.playbackRate = c.to; }
  else if (c.do === 'text') wpOsd(c.text, c.ms);
  else if (c.do === 'index') wpLoad(c.to, 0);
  else if (c.do === 'reload') wpLoad(WP.s.index, c.at || wpNow(), { reload: true });
  else if (c.do === 'entries') WP.s.entries = c.entries;
  else if (c.do === 'replace') { WP.s.entries = c.launch.entries; WP.s.launch = { ...WP.s.launch, title: c.launch.title, item_id: c.launch.item_id }; wpQ('.wp-show').textContent = c.launch.title || ''; wpLoad(c.launch.index || 0, c.launch.start || 0, { paused: c.launch.paused }); }
  else if (c.do === 'close') wpClose(true);
}

// ---- subtitles: drawn by the page (and given to the phone for its own full screen)

function wpParseVtt(text) {
  const out = [];
  const time = (x) => { const p = x.trim().split(':').map(Number); return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1]; };
  for (const block of text.replace(/\r/g, '').split(/\n\n+/)) {
    const lines = block.split('\n');
    const i = lines.findIndex((l) => l.includes('-->'));
    if (i < 0) continue;
    const [a, b] = lines[i].split('-->');
    const start = time(a), end = time(b.trim().split(/\s+/)[0]);
    const body = lines.slice(i + 1).join('\n').trim();
    if (Number.isFinite(start) && Number.isFinite(end) && body) out.push({ start, end, text: body });
  }
  return out;
}

function wpCueHtml(text) {
  // Only <b>, <i>, <u> kept; everything else shown as the words it holds.
  const keep = text.replace(/<(\/?)(b|i|u)>/gi, '\u0001$1$2\u0002').replace(/<[^>]*>/g, '');
  return esc(keep).replace(/\u0001(\/?)(b|i|u)\u0002/gi, '<$1$2>').replace(/\n/g, '<br>');
}

async function wpSubsLoad(again = false) {
  const s = WP.s;
  const sub = s?.media?.subs?.[s.subPick];
  clearTimeout(s?.subTimer);
  wpQ('.wp-cc').innerHTML = '';
  if (!s || !sub || !s.subOn) { wpTrackFill([]); return; }
  try {
    const res = await fetch(sub.url, { cache: 'no-store' });
    const text = await res.text();
    if (WP.s !== s || s.media.subs[s.subPick] !== sub) return;
    // Times from the start of the episode (a file of its own) or of this video (made as it goes).
    const shift = sub.absolute ? 0 : s.offset;
    s.cues = wpParseVtt(text).map((c) => ({ ...c, start: c.start + shift, end: c.end + shift }));
    wpTrackFill(s.cues);
  } catch { /* none this time */ }
  if (sub.live && WP.s === s) s.subTimer = setTimeout(() => wpSubsLoad(true), again ? 10000 : 6000);
}

function wpTrackFill(cues) {
  const v = wpV();
  let track = [...v.textTracks].find((t) => t.label === 'Watch Anything');
  if (!track) track = v.addTextTrack('subtitles', 'Watch Anything', 'en');
  try { [...(track.cues || [])].forEach((c) => track.removeCue(c)); } catch { /* fine */ }
  const off = WP.s ? WP.s.offset : 0;
  for (const c of cues) {
    if (c.end - off <= 0) continue;
    try { track.addCue(new VTTCue(Math.max(0, c.start - off), c.end - off, c.text.replace(/<[^>]*>/g, ''))); } catch { /* fine */ }
  }
  track.mode = WP.native ? 'showing' : 'hidden';
}

function wpSubsDraw() {
  const s = WP.s;
  const box = wpQ('.wp-cc');
  if (!s || !s.subOn || WP.native) { if (box.innerHTML) box.innerHTML = ''; return; }
  const t = wpNow();
  const now = s.cues.filter((c) => c.start <= t && c.end > t);
  const html = now.map((c) => `<span>${wpCueHtml(c.text)}</span>`).join('<br>');
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
}

// ---- audio: Sub / Dub and other tracks (HLS renditions)

function wpAudioTracks() {
  const v = wpV();
  if (WP.hls) return WP.hls.audioTracks.map((t, i) => ({ i, label: t.name || t.lang || `Audio ${i + 1}`, on: WP.hls.audioTrack === i }));
  const at = v.audioTracks;
  if (!at || !at.length) return [];
  return [...Array(at.length).keys()].map((i) => ({ i, label: at[i].label || at[i].language || `Audio ${i + 1}`, on: at[i].enabled }));
}

function wpAudioPick() {
  const s = WP.s;
  const want = wpPref(`wp-audio:${s.launch.item_id}`, '');
  if (!want) return;
  const tracks = wpAudioTracks();
  const t = tracks.find((x) => x.label === want);
  if (t && !t.on) wpAudioSet(t.i);
}

function wpAudioSet(i) {
  const v = wpV();
  if (WP.hls) WP.hls.audioTrack = i;
  else if (v.audioTracks) for (let k = 0; k < v.audioTracks.length; k += 1) v.audioTracks[k].enabled = k === i;
}

// ---- the screen

function wpLoading(text) {
  const box = wpQ('.wp-loading');
  if (text === false) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  wpQ('.wp-loading-text').textContent = text;
}

let wpOsdTimer = 0;
function wpOsd(text, ms = 3000) {
  const box = wpQ('.wp-osd');
  box.textContent = text;
  box.classList.remove('hidden');
  clearTimeout(wpOsdTimer);
  wpOsdTimer = setTimeout(() => box.classList.add('hidden'), Math.min(ms, 15000));
}

function wpWhy(code, detail) {
  if (code === 403) return 'The site turned the video down (it may have run out). Try another server.';
  if (code === 404) return "The video isn't there any more. Try another server.";
  if (code === 502 || code === 504) return "The site didn't answer. Try another server, or another site.";
  if (/manifestParsing|manifestIncompatible/i.test(detail || '')) return "The site's video couldn't be read. Try another server.";
  return `It stopped (${detail || code || 'a playback error'}).`;
}

function wpError(why, canRetry) {
  const s = WP.s;
  wpLoading(false);
  const box = wpQ('.wp-error');
  box.classList.remove('hidden');
  wpQ('.wp-error-why').textContent = why;
  const site = s?.media?.how === 'site' || (s?.entries?.[s.index] || {}).web;
  wpQ('.wp-error-btns').innerHTML = `${site ? '<button class="btn btn-red" data-wp="source">Try another server or site</button>' : ''}
    ${canRetry ? '<button class="btn btn-white" data-wp="retry">Try again</button>' : ''}
    <button class="btn btn-ghost" data-wp="close">Close</button>`;
}
const wpHideError = () => wpQ('.wp-error').classList.add('hidden');

let wpHideTimer = 0;
function wpShowUi(stay) {
  const el = WP.el;
  el.classList.add('wp-ui-on');
  clearTimeout(wpHideTimer);
  if (!stay) wpHideTimer = setTimeout(() => { if (!wpV().paused && wpQ('.wp-sheet').classList.contains('hidden')) el.classList.remove('wp-ui-on'); }, 5000);
}

function wpTick() {
  if (!WP.s || !WP.el || WP.el.classList.contains('hidden')) return;
  const s = WP.s;
  const v = wpV();
  const now = wpNow(), total = wpTotal();
  if (!WP.dragging) {
    wpQ('.wp-seek').value = total ? Math.round((now / total) * 1000) : 0;
    wpQ('.wp-played').style.width = `${total ? (now / total) * 100 : 0}%`;
    wpQ('.wp-time').textContent = fmtT(now);
  }
  wpQ('.wp-dur').textContent = total ? `-${fmtT(total - now)}` : '';
  const b = v.buffered;
  if (b && b.length && total) {
    let end = 0;
    for (let i = 0; i < b.length; i += 1) if (b.start(i) <= v.currentTime + 1) end = Math.max(end, b.end(i));
    wpQ('.wp-buffered').style.cssText = `left:${(s.offset / total) * 100}%;width:${Math.max(0, ((end) / total) * 100)}%`;
  }
  wpQ('.wp-play').innerHTML = wpIco(v.paused ? 'play' : 'pause');
  wpQ('.wp-play').setAttribute('aria-label', v.paused ? 'Play' : 'Pause');
  wpQ('[data-wp="prev"]').disabled = s.index <= 0;
  wpQ('[data-wp="next"]').disabled = s.index >= s.entries.length - 1;
  wpQ('[data-wp="cc"]').classList.toggle('on', s.subOn && !!(s.media?.subs || []).length);
  wpSubsDraw();
  // The last 20 seconds: what comes next, played by itself when this ends.
  if (total && total - now < 20 && total - now > 0.5 && s.index < s.entries.length - 1 && !s.upnextShown && !v.paused) {
    s.upnextShown = true;
    const nxt = s.entries[s.index + 1];
    wpQ('.wp-upnext-what').textContent = wpEpLabel(nxt) || 'Next episode';
    wpQ('.wp-upnext').classList.remove('hidden');
  }
  if (!wpQ('.wp-upnext').classList.contains('hidden')) wpQ('.wp-count').textContent = `(${Math.max(0, Math.ceil(total - now))})`;
}

function wpMediaSession() {
  if (!('mediaSession' in navigator)) return;
  const s = WP.s;
  const item = typeof findItem === 'function' ? findItem(s.launch.item_id) : null;
  const e = s.entries[s.index] || {};
  try {
    navigator.mediaSession.metadata = new MediaMetadata({ title: e.label || s.launch.title, artist: s.launch.title, album: 'Watch Anything',
      artwork: item?.poster ? [{ src: item.poster, sizes: '512x768', type: 'image/jpeg' }] : [] });
    const v = wpV();
    const set = (a, f) => { try { navigator.mediaSession.setActionHandler(a, f); } catch { /* not this one */ } };
    set('play', () => v.play());
    set('pause', () => v.pause());
    set('seekbackward', () => wpSeek(wpNow() - 10));
    set('seekforward', () => wpSeek(wpNow() + 10));
    set('seekto', (d) => wpSeek(d.seekTime));
    set('previoustrack', s.index > 0 ? () => wpLoad(WP.s.index - 1) : null);
    set('nexttrack', s.index < s.entries.length - 1 ? () => wpLoad(WP.s.index + 1) : null);
  } catch { /* fine */ }
}

// ---- the menu: audio, subtitles, quality, speed

function wpMenu() {
  const s = WP.s;
  const sheet = wpQ('.wp-sheet');
  if (!sheet.classList.contains('hidden')) { sheet.classList.add('hidden'); return; }
  const audio = wpAudioTracks();
  const subs = s.media?.subs || [];
  const styled = subs.find((x) => x.styled && x.n !== undefined);
  const file = s.media && s.media.how !== 'site';
  const q = s.quality || '';
  sheet.innerHTML = `<div class="wp-sheet-head"><b>Player</b><button class="wp-btn" data-wp="menu" aria-label="Close">✕</button></div>
    ${audio.length > 1 ? `<h5>Audio</h5><div class="wp-opts">${audio.map((a) => `<button class="${a.on ? 'on' : ''}" data-audio="${a.i}">${esc(a.label)}</button>`).join('')}</div>` : ''}
    ${s.media?.how === 'site' ? '<h5>Sub / Dub</h5><div class="wp-opts"><button data-wp="version">Switch Sub ⇄ Dub</button></div>' : ''}
    <h5>Subtitles</h5><div class="wp-opts"><button class="${!s.subOn ? 'on' : ''}" data-sub="off">Off</button>
      ${subs.map((x, i) => `<button class="${s.subOn && s.subPick === i ? 'on' : ''}" data-sub="${i}">${esc(x.label)}</button>`).join('') || '<span class="muted small">None for this one</span>'}</div>
    ${file && styled ? `<label class="check-row"><input type="checkbox" data-wp="burn" ${s.burn != null ? 'checked' : ''}> Burn in styled subtitles <span class="muted small">(their own look, drawn into the picture)</span></label>` : ''}
    ${file ? `<h5>Quality</h5><div class="wp-opts">${[['', `As in Settings`], ['original', 'Original'], ['1080', '1080p'], ['720', '720p'], ['480', '480p']]
    .map(([v, l]) => `<button class="${q === v ? 'on' : ''}" data-q="${v}">${l}</button>`).join('')}</div>` : ''}
    <h5>Speed</h5><div class="wp-opts">${[0.5, 0.75, 1, 1.25, 1.5, 2].map((r) => `<button class="${s.speed === r ? 'on' : ''}" data-speed="${r}">${r}×</button>`).join('')}</div>
    ${s.media ? `<p class="muted small wp-how">${{ direct: 'Played as it is', copy: 'Repackaged for the phone (picture untouched)', encode: `Converted for the phone${s.media.encoder ? ` (${esc(s.media.encoder.replace('h264_', '').toUpperCase())})` : ''}`, site: `From ${esc(s.media.site || 'your site')}` }[s.media.how] || ''}</p>` : ''}`;
  sheet.classList.remove('hidden');
  wpShowUi(true);
}

// ---- closing

async function wpClose(already) {
  const s = WP.s;
  if (!s || s.closing) return;
  s.closing = true;
  const v = wpV();
  if (!already) {
    try { await api('POST', `/api/web/${s.sid}/state`, { index: s.index, pos: s.media ? wpNow() : null, dur: wpTotal() || null, paused: true, closed: true }); } catch { /* it's closing anyway */ }
  }
  clearInterval(WP.tick);
  clearTimeout(s.subTimer);
  try { WP.events?.close(); } catch { /* gone */ }
  wpTeardownStream();
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  WP.el.classList.add('hidden');
  wpQ('.wp-sheet').classList.add('hidden');
  document.body.classList.remove('wp-open');
  WP.s = null;
  v.removeAttribute('poster');
  if (typeof reload === 'function') reload().catch(() => {});
}

// ---- touch and buttons

function wpWire(el) {
  const v = el.querySelector('video');
  setInterval(wpTick, 250);
  v.addEventListener('ended', async () => {
    const s = WP.s;
    if (!s) return;
    try { await api('POST', `/api/web/${s.sid}/state`, { index: s.index, pos: wpTotal(), dur: wpTotal(), paused: true, ended: true, after: s.seq }); } catch { /* fine */ }
    if (s.index < s.entries.length - 1 && wpQ('.wp-upnext').dataset.cancel !== '1') wpLoad(s.index + 1, 0);
    else { wpShowUi(true); wpOsd('The end', 4000); }
    wpQ('.wp-upnext').dataset.cancel = '';
  });
  v.addEventListener('error', () => {
    if (!WP.s || !WP.s.media || !v.getAttribute('src') || WP.hls) return;
    if (!v.currentSrc.endsWith(WP.s.media.src.split('/').pop())) return;      // (the one before)
    const code = v.error?.code;
    wpError(code === 4 ? "The phone can't play this video as it is. Try again (it's converted then), or another server." : wpWhy(0, `error ${code}`), true);
  });
  v.addEventListener('waiting', () => { if (WP.s) wpLoading('Loading…'); });
  v.addEventListener('playing', () => { wpLoading(false); wpHideError(); wpShowUi(); });
  v.addEventListener('pause', () => { wpShowUi(true); wpReport(true); });
  v.addEventListener('play', () => wpReport(true));
  // The phone's own full screen (iPhone): subtitles go to it, since the page can't draw there.
  v.addEventListener('webkitbeginfullscreen', () => { WP.native = true; if (WP.s) wpTrackFill(WP.s.cues); });
  v.addEventListener('webkitendfullscreen', () => { WP.native = false; if (WP.s) wpTrackFill(WP.s.cues); });
  if (window.WebKitPlaybackTargetAvailabilityEvent) {
    v.addEventListener('webkitplaybacktargetavailabilitychanged', (e) => {
      el.querySelector('.wp-airplay').classList.toggle('hidden', e.availability !== 'available');
    });
  }
  // Taps: one shows or hides the controls; two on a side jump 10 seconds.
  let lastTap = 0, tapTimer = 0;
  el.querySelector('.wp-tap').addEventListener('click', (e) => {
    const now = Date.now();
    const r = e.currentTarget.getBoundingClientRect();
    const side = e.clientX < r.left + r.width / 3 ? -1 : e.clientX > r.right - r.width / 3 ? 1 : 0;
    if (now - lastTap < 300 && side) {
      clearTimeout(tapTimer);
      wpSeek(wpNow() + side * 10);
      wpOsd(side < 0 ? '⟲ 10 s' : '10 s ⟳', 700);
      lastTap = 0;
      return;
    }
    lastTap = now;
    tapTimer = setTimeout(() => {
      if (el.classList.contains('wp-ui-on')) el.classList.remove('wp-ui-on'); else wpShowUi();
    }, 260);
  });
  const seek = el.querySelector('.wp-seek');
  seek.addEventListener('input', () => {
    WP.dragging = true;
    const t = (seek.value / 1000) * wpTotal();
    el.querySelector('.wp-time').textContent = fmtT(t);
    el.querySelector('.wp-played').style.width = `${seek.value / 10}%`;
    wpShowUi(true);
  });
  seek.addEventListener('change', () => { WP.dragging = false; wpSeek((seek.value / 1000) * wpTotal()); wpShowUi(); });
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-wp], [data-audio], [data-sub], [data-q], [data-speed]');
    if (!b || !WP.s) return;
    const s = WP.s;
    wpShowUi();
    if (b.dataset.audio !== undefined) {
      wpAudioSet(Number(b.dataset.audio));
      wpSetPref(`wp-audio:${s.launch.item_id}`, wpAudioTracks()[Number(b.dataset.audio)]?.label || '');
      wpMenu(); wpMenu();
      return;
    }
    if (b.dataset.sub !== undefined) {
      s.subOn = b.dataset.sub !== 'off';
      if (s.subOn) s.subPick = Number(b.dataset.sub);
      wpSetPref(`wp-subs:${s.launch.item_id}`, s.subOn ? 'on' : 'off');
      wpSubsLoad();
      wpMenu(); wpMenu();
      return;
    }
    if (b.dataset.q !== undefined) {
      s.quality = b.dataset.q;
      wpSetPref('wp-quality', s.quality);
      wpQ('.wp-sheet').classList.add('hidden');
      wpLoad(s.index, wpNow(), { reload: true });
      return;
    }
    if (b.dataset.speed !== undefined) {
      s.speed = Number(b.dataset.speed);
      wpV().playbackRate = s.speed;
      wpMenu(); wpMenu();
      return;
    }
    const a = b.dataset.wp;
    if (a === 'play') { if (v.paused) v.play().catch(() => {}); else v.pause(); }
    else if (a === 'close') wpClose();
    else if (a === 'prev' && s.index > 0) wpLoad(s.index - 1, 0);
    else if (a === 'next' && s.index < s.entries.length - 1) wpLoad(s.index + 1, 0);
    else if (a === 'next-now') wpLoad(s.index + 1, 0);
    else if (a === 'next-cancel') { wpQ('.wp-upnext').classList.add('hidden'); wpQ('.wp-upnext').dataset.cancel = '1'; }
    else if (a === 'menu') wpMenu();
    else if (a === 'cc') {
      if (!(s.media?.subs || []).length) { wpOsd('No subtitles for this one'); return; }
      s.subOn = !s.subOn;
      wpSetPref(`wp-subs:${s.launch.item_id}`, s.subOn ? 'on' : 'off');
      wpSubsLoad();
      wpOsd(s.subOn ? `Subtitles: ${s.media.subs[s.subPick].label}` : 'Subtitles off', 1500);
    } else if (a === 'retry') wpLoad(s.index, wpNow() || 0, { reload: true });
    else if (a === 'source') {
      wpHideError();
      wpLoading('Trying another source…');
      try { await api('POST', `/api/web/${s.sid}/source`, {}); } catch (err) { wpError(err.message, true); }
    } else if (a === 'version') {
      wpQ('.wp-sheet').classList.add('hidden');
      try { await api('POST', `/api/web/${s.sid}/version`, {}); } catch (err) { fail(err); }
    } else if (a === 'burn') {
      const styled = (s.media?.subs || []).find((x) => x.styled && x.n !== undefined);
      s.burn = b.checked && styled ? styled.n : null;
      if (s.burn != null) s.subOn = false;
      wpQ('.wp-sheet').classList.add('hidden');
      wpLoad(s.index, wpNow(), { reload: true });
    } else if (a === 'full') {
      if (v.webkitEnterFullscreen && !document.fullscreenEnabled && !el.webkitRequestFullscreen) v.webkitEnterFullscreen();
      else if (document.fullscreenElement || document.webkitFullscreenElement) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      else if (el.requestFullscreen) el.requestFullscreen().catch(() => v.webkitEnterFullscreen?.());
      else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
      else v.webkitEnterFullscreen?.();
    } else if (a === 'pip') {
      try {
        if (document.pictureInPictureElement) await document.exitPictureInPicture();
        else if (v.requestPictureInPicture) await v.requestPictureInPicture();
        else if (v.webkitSetPresentationMode) v.webkitSetPresentationMode(v.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture');
      } catch { wpOsd("Picture in Picture isn't available here"); }
    } else if (a === 'airplay') v.webkitShowPlaybackTargetPicker?.();
  });
  document.addEventListener('keydown', (e) => {
    if (!WP.s || WP.el.classList.contains('hidden') || e.target.closest('input, textarea')) return;
    if (e.key === ' ' || e.key === 'k') { e.preventDefault(); if (v.paused) v.play(); else v.pause(); }
    else if (e.key === 'ArrowLeft') wpSeek(wpNow() - 10);
    else if (e.key === 'ArrowRight') wpSeek(wpNow() + 10);
    else if (e.key === 'Escape') wpClose();
  });
}

// A page opened again (the app reloaded) while its player was open: back to it, paused.
if (typeof REMOTE !== 'undefined' && REMOTE) {
  window.addEventListener('load', () => setTimeout(async () => {
    try {
      const r = await api('GET', '/api/web/current');
      if (r.launch && !WP.s) openWebPlayer({ ...r.launch, paused: true });
    } catch { /* not connected yet */ }
  }, 1500));
}
