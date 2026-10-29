# Watch Anything

Your own watch list, as a desktop app for Windows and Mac: the TV shows, anime and movies you plan to watch, all in one place.
Connect your streaming services and open any title on the one that has it, watch what you've downloaded straight from your computer, or stream and download legal torrents right in the app.
(It was called **Watchlist** before; your list, settings and data folder carry over as they are.)

Two things are optional, and everything works without them:

- **A sync folder.** Without one, your list is saved on this computer only. With one (Google Drive, OneDrive...), your list stays the same on your Windows PC and your Mac.
- **An AI key** (Gemini, Groq or OpenRouter, all free). Without one, the app's own rules and MyAnimeList / TMDB searches sort your downloads and find connected titles. With one, the AI helps with the files those can't place, finds everything connected to a title, suggests what to watch and writes recaps.

- **Home screen:** three rows (TV Shows, Anime, Movies), each showing your 5–6 most popular titles and a **Show more** tile for the full list.
  - Movies are ordered by box office, TV shows by TMDB popularity, and anime by MyAnimeList members.
- **Setup guide:** when the app opens it asks whether you'd like help setting it up (tick **Don't show this again** to stop it asking). The guide goes through each step one at a time (TMDB token, MyAnimeList Client ID, a few titles, your downloads folder, AI helpers, your country, name and picture, a sync folder, sites for watching online, a torrent site, VLC), with the links, Settings opened at the right place, and **Skip** on the optional steps. It moves on by itself when a step is done. **Settings → Setup guide** brings it back any time.
- **Colour theme:** **Settings → Look**: Violet, Crimson, Ocean, Emerald, Sunset, Rose or Mono. It changes the whole app and the built-in player's controls.
- **Sounds:** small, quiet sounds for small things (adding a title, closing a window, changing section). Turn them off in **Settings → Look**.
- **About:** at the bottom of the home screen, what the app does in a few lines (**Hide this** to put it away).
- **Top bar:** the sections (folded into **Browse ▾** when the window is narrow, or while you search), the search (click the magnifier), **Sort ▾**, a **Genre** filter, **🎲 Surprise me**, **Torrents** (once you have one), **Watch together** and **Settings**.
- **Sort ▾:** most popular, newest or oldest release, **date added** (newest or oldest first), **recently opened**, **recently played**, rating, **your review**, or title A–Z / Z–A. Your choice is remembered.
- **🎲 Surprise me:** picks something from your list for you. Narrow it down by type, the time you have (under 30 minutes, about an hour, a whole evening), something new or something to carry on with, genre, or only what's on your computer. **Pick another** rolls again.
- **Adding titles:** use **+** in any row. It's in your list the moment you click: the summary, ratings, cast, seasons and trailer are filled in right after, in the background (from TMDB for movies and TV and from MyAnimeList for anime), and that carries on even if you close the window or the app (it finishes the next time it opens).
  - Anime always come from MyAnimeList: adding an anime from TMDB (say from TV Shows) adds its MyAnimeList entry instead.
  - An anime added from TMDB before this version shows **Use MyAnimeList instead** on its page (also in **⋯ → Use MyAnimeList info**). Pick the right entry: its files, where you're up to, folders and collections are kept, episode numbers are converted (TMDB's season 2 episode 3 becomes episode 10 when season 1 had 7), and it moves to Anime. If that entry is already in your list, the two become one.
- **Select:** on the TV Shows, Anime, Movies, Watched and collection pages, **Select** lets you click several posters, then **Mark watched**, **Move to folder**, add them to a **Collection** or **Remove** them all at once.
- **Folders:** on the TV Shows, Anime and Movies pages, click **New folder** to make your own folders. Drag a poster onto a folder to move it there, or right-click a poster (or use **⋯**) → **Move to folder**. A title is in one folder at a time.
- **Collections:** these can mix TV shows, anime and movies, and a title can be in several. Find them on the **Collections** page.
  - **Share** (next to each collection) gives a share code to paste into any chat, or **Save as a file instead** (saved to your Downloads folder). Only the titles are shared: no files, progress or keys.
  - **Open a shared collection…** (top of the Collections page): paste the code or choose the file to see every title, then **Add the collection** adds it to your Collections with every title in it, straight away. Tick **Also add them to my watch list** to have the titles you don't have in your everyday rows (TV Shows, Anime, Movies) too; unticked, they're only in that collection, and **+ Add to my list** on a title's page adds one later.
- **Watched:** **✓ Watched it all** on a title's page (or **⋯ → Mark as watched**) on any title moves it out of your everyday rows (Home, TV Shows, Anime, Movies, Continue watching) into the **Watched** page, kept as your collection. **⋯ → Move back to my list** undoes it. Watched titles still show in collections and search, marked WATCHED.
- **Continue watching:** the first row on the home screen. Click a poster to carry on where you stopped.
- **Title page:** backdrop, summary, ratings and an episode list. The trailer only plays when you click it.
  - **Personal Review:** give it 1 to 5 stars and, if you like, a few words. Only for you: it stays in the app (and syncs with your list). Sort by **Your review** to see your favourites first.
  - **See how the episodes are connected:** every episode and the file it plays from (your downloads, the title's own folder or a torrent's file). **Change…** next to a file sets which episode it is (or that it isn't part of this title, or to go by its name again); **Choose a file…** on an episode without one picks its file. **Sort again** forgets how the title's files were matched and works them out from scratch (with your AI helper, if you have one).
  - **Everything connected**, on the right: press **Find everything connected** to find every season, sequel, prequel, OVA, ONA, special, film and spin-off. A progress bar shows how far along it is, what's found shows as soon as it's found (MyAnimeList's links come in a few seconds; your AI helper's finds follow), and **Cancel** stops it, keeping what was found.
    - **The same story** comes first, by kind. A spin-off finds the show it comes from, even a brand-new one, from its summary ("a spin-off of Rick and Morty").
    - **Other versions & related series** come after: reboots, other continuities and other series or films of the franchise (for Spider-Man: Brand New Day, the earlier Spider-Man films, Spider-Verse and the animated series).
    - When it's done, the **story guide** opens (and **Open the story guide** brings it back):
    - At the top, how the whole franchise fits together and the best way into it, and how many titles are complete on your computer, partly downloaded or missing.
    - Every title in **Release order** or **Story order** (the order things happen in the story), numbered. Each one says how it connects to the main story, when to watch it, whether it's canon, anime-original, non-canon or a recap, and whether it's essential, recommended, optional or skippable, plus what it's about.
    - Everything is spoiler-free; switch on **Show spoilers** to also see how each one ties into the story.
    - Each title shows what's on your computer ("8 of 12 episodes", "All 12 episodes", waiting in your downloads, or not on your computer), with **+ Add to my list** or **Open**.
    - The facts (kind, year, episodes, how it's linked) come from MyAnimeList for anime (the whole franchise, following its relations) and TMDB for film series. The explanations are written by your AI helper; without an AI key you get the facts and the release order. With one, the AI also finds connected titles with other names (each checked on MyAnimeList / TMDB first). Without one, TV shows list titles that share their name.
    - Beside the page there's just the button (**Everything connected (n)** opens the guide again once it's found), and below it **Related**: the title's name searched on MyAnimeList (anime) or TMDB (films and TV shows), up to 5 titles with a similar name, each with **+** to add it.
  - **Episodes:** each episode says whether it's on your PC (with its file name) or **Not downloaded**, and a line above the list says how many you have and which are missing.
    - **Move…** next to a file moves just that file: to another title, season or episode, out of the title (to sort later), or ignored. **Files…** in **On your PC** lists every file of the title, each with **Move…**.
  - **From the same folder:** films, OVAs, specials and extras that sit in the same folder as a series' files are shown on every page of that series, and on their own page. Click one to open it, or play it.
  - **Extra stuff** (folded away; click to open): what the title's torrents downloaded that isn't one of its episodes, in its folders (each folder opens), and for your own downloads the openings, endings, trailers, films, OVAs and other files in the same folder as the title's files.
- **Find Media** (in the top bar): new things to watch from the internet.
  - **Top by year:** pick TV shows, movies or anime, a year, and **Most popular** or **Highest rated**, for that year's top 100. TV shows come from TMDB (anime left out, they have their own list), anime from MyAnimeList's seasons.
  - **Search & browse:** every filter is optional, and they all work together:
    - **TV shows / Movies / Anime:** pick one or more; none picked searches all three, mixed.
    - **Keywords**, or none at all to browse by the filters alone.
    - **Genres:** every genre TMDB and MyAnimeList have, adult anime genres included, plus over 100 detailed ones for TV shows and films, grouped: action & adventure (superhero, heist, spy…), comedy (sitcom, rom-com, dark comedy…), crime & mystery, drama, horror (slasher, zombie, found footage…), thriller, sci-fi (time travel, dystopia, cyberpunk…), fantasy, romance, family, real life, reality TV, music, history & war, and format & source (miniseries, anthology, based on a novel…). With one type picked you get that type's own genres (for anime grouped into genres, themes, audience and adult); with several, the genres they share plus the rest, and a detailed genre MyAnimeList also has (vampire, time travel, samurai…) finds anime too. Pick as many as you like.
      - Genres marked **•** are TMDB tags: labels TMDB's users add, so popular titles have them and obscure ones less often.
    - **Country & language:** K-drama and K-movie (Korean), J-drama (Japanese live action), C-drama (Chinese), Thai, Indian (Bollywood and more) and Turkish: everything made in that language, combinable with any genre.
    - **Year**, **anime format** (TV, film, OVA, ONA, special…) and **sort**: most popular, highest rated, newest, oldest, title A–Z, or box office for films.
    - Results come 50 at a time, with **Show 50 more**. For example: Movies, Horror, 2025, Highest rated lists every horror film of 2025.
    - TMDB has no Horror, Romance, Thriller, Fantasy or Science fiction genre of their own for TV shows, so those use TMDB's tags.
    - Anime come only from MyAnimeList's official API, with your Client ID. Its API can't search by genre, so the first time you open Find Media the app collects every anime from MyAnimeList's popularity ranking, with their genres (about 30,000, a minute or two in the background), keeps them in your data folder and refreshes them weekly. Anime results fill in while that runs. Like MyAnimeList's genre pages, every anime of a genre can then be sorted by popularity, score or date.
  - **I'm in the mood for…:** first pick **Films**, **TV shows**, **Anime** or **Anything**, then type what you feel like: a title (typos are fine: "indterstella" is Interstellar), a single word ("cozy"), or a sentence.
    - When it's a title, **Like it** shows titles like it in a couple of seconds (from TMDB's and MyAnimeList's recommendations).
    - Your AI helper then adds **Picked for you**, knowing what you meant and what's new and popular right now (so a new film with the same feel can come up), and **From your list**. Each pick is checked on MyAnimeList / TMDB and shows the moment it is. It never stops to ask questions.
    - **Cancel** (or Esc) stops it. Without an AI key you still get the titles like the one you typed.
  - Click any poster for its summary; **+ Add** puts it in your list, and titles already in it say so.
- **Import from MyAnimeList:** **Settings → Import my MyAnimeList list…** brings in your list by user name (if it's public) or from MyAnimeList's export file. Your watched episodes set where you're up to.
- **Downloads:** choose the one folder your downloads go into (**Settings → Your downloads**).
  - The app reads each video's name, and its folder's name if needed, and connects it to the right show, season and episode or movie in your list.
  - **Long anime numbered from the start** ("Season 12/Naruto Shippuden - 250"): an episode number of 40 or more in a "Season N" folder counts from the show's first episode, as on MyAnimeList, so the folder doesn't need renaming. For a TMDB show split into seasons, it's placed in the right season.
  - It tells episodes, OVAs, specials, films and extras apart, using the file and folder names, the size, and what MyAnimeList says each title is.
  - Season folders are read however they're written: "Season 2", "(Season 2)", "S04", "4th Season", "Second Season - 05", "S2 - 05". A folder holding several seasons ("S01+02+03+04", "Complete", "Batch") doesn't count as season 1.
  - **Nothing is sorted until you press Sort now.** New downloads wait, and the banner above your downloads says how many. Files you've sorted, moved or taken out of a title, and files sorted earlier, are never changed.
  - **Sort now** (in the banner, and in **Sort your downloads**) sorts only what's still waiting:
    1. **Packs first:** a folder holding a whole anime franchise (several seasons, or seasons with films and OVAs) is worked out group by group, as described under **Packs** below, so Season 2's files go to the Second Season, not to the first one. A pack is worked out once; files you placed yourself are left alone.
    2. Files that clearly match a title in your list, and extras in the same folder as a show.
    3. Shows that aren't in your list are searched for, **MyAnimeList first**: anything that's an anime goes to Anime, and TMDB is only used for what isn't on MyAnimeList. Even when an anime is found on TMDB (or named by the AI as a TV show), its MyAnimeList entry is used, never TMDB's copy. An OVA, special, film or second season of an anime you have is found through that anime's MyAnimeList relations. A title already in your list is never added twice.
    4. **✨ with AI**, the small button above **Sort now**, also asks your AI helper about what's left.

    It then says how many it sorted. Everything it did is listed under **Sorted for you**, where **Undo** puts it back (and removes the title it added); undone files are left for you.
  - **Sort them myself** opens **Sort your downloads**: what's ready to sort, what it isn't sure of (a typo, an unnumbered file, a subtitle without a video), and what isn't in your list, each with what was found. Your answers are kept.
  - The home screen starts with **Downloaded**: rows for anime, TV shows and movies you have files for. **Your list** below it still shows everything.
- **Watch now:** a file on your computer plays straight away. When there isn't one, the title's torrent streams straight away. When there's neither, and you have both your own video sites and streaming services, you choose **Play online (my sites)** or **Watch online** (with only one of them set up, that one is used). Nothing set up: you choose between pasting a magnet link, **Watch online** and **Find the file on my PC**.
  - **Play this with** (next to Watch now on a title's page): **Use anything** (the automatic order above), **The file on my computer**, **The torrent**, **My video sites** or **Watch online**. What you pick is what Watch now uses for that title from then on, remembered even after the app is closed.
  - **On your computer:** it plays the episode you're up to in the **built-in player** (mpv), followed by the next episodes in order, with matching `.srt`/`.ass` subtitles.
    - The player handles MKV and HEVC files, several audio and subtitle tracks, and fully styled anime subtitles.
    - It remembers where you stopped, even on your other computer, and resumes from there. An episode counts as watched once you reach the end credits, and you move on to the next one.
    - Its controls are modern and light: play/pause, previous/next, a slim timeline that shows chapters (openings and endings highlighted) and buffering, with subtitle, audio, chapter, speed, playlist and fullscreen buttons, in the app's colour theme.
    - The controls (and the volume) hide after **5 seconds** without the mouse moving or a key pressed, even with the pointer resting over them, and come back when you move the mouse or use a key.
    - **Play in VLC** on the title page, or **Open in VLC instead** while something is playing, switches to VLC at the same spot.
    - **Settings → Player** can also be **Another player of your choice**: pick one the app found (MPC-HC, PotPlayer, IINA, Infuse…) or **Browse** to any player. The file (or a torrent's stream) is handed to it; the built-in player is still needed for watch parties, skipping openings and remembering where you stopped.
    - Click any episode in the episode list to play it, or use **Choose file…** to pick any file.
    - **Openings and endings are skipped** when the file marks them as chapters (most anime releases do). Press **Shift+S** while watching to turn this on or off, or change it in Settings.
  - **Online:** your own list of streaming services and sites (**Settings → Watching online**). **+ Add…** shows the big ones (Netflix, Prime Video, Disney+, HBO Max, Hulu, Apple TV+, Crunchyroll, Paramount+, Peacock, HIDIVE, Viki, YouTube, Tubi, Pluto TV, Plex, JustWatch): click each one you use to add it. **Your own site…** adds any site with a search link. Remove them, switch them off, put them in order, and choose which are for anime, TV and movies.
    - Each has a **Check**: **TMDB** (it's only used when TMDB says it has the title in your country), **AI** (the same, by your AI helper's knowledge, remembered for a day) or **No check** (searched every time, for your own sites).
    - **Watch online** shows them all with ✓ or ✗ for whether each has it, and opens the one you pick; **Best match** opens the first in your order that fits.
    - Nothing is opened that you didn't add. When none of them fits, it says so, and you can pick one anyway or add a site. (Earlier versions opened everythingmoe.com then; add it as your own site if you'd like that back.)
    - They open in your browser (see **Browser** below). The app only opens the search; it doesn't read or play anything from the site.
  - **Deleting an episode:** the bin button next to an episode's file. Your own files go to the Recycle Bin / Trash (so you can put them back); a torrent's file is deleted for good (not to the Recycle Bin), the same as the ones deleted after you watch them. Your own files are never deleted by the app itself.
- **Play online (my sites):** your own website where your videos are uploaded. Add it in **Settings → Your video sites** with its search link (search for any title on it, copy the results page's address and put **{title}** where the name was), tick what it's for, and **Test** it. Then:
  1. The site is searched for the title (its name and other names; for a TV show's later season, "name season N" too).
  2. The right entry is picked by name: the season, the kind (a film, an OVA or a special isn't the TV series) and words the title doesn't have ("To the Top") all count, so another season isn't opened by mistake. When nothing matches exactly, the closest one is used, and **Not the right title? Pick another** shows the other results (your choice is remembered).
  3. The entry's episodes are found on its page (following its **Watch** / **Episodes** link if needed), then the episode's video: from the page's video player, its settings and scripts, and the players it embeds.
  4. It plays straight away in the built-in player: it streams, nothing is downloaded first, and seeking, resuming and watch parties work. When a page's video can only play in a browser (or you set the site to **Opens in: a browser window**), the episode's page opens in the browser instead.

  What was found is remembered, so the next episode starts quickly.
- **When a file doesn't work:** press **Not working**. It's in the play window, and in the **On your PC** section of the title page.
  - Watch now then goes online for that title, and its poster shows **FILE ISSUE**.
  - Fix it later from the title page with **Choose file…** or **Use a different folder…**, or press **It works now**.

### Streaming from a torrent

On a show's page, under **Stream from a torrent**, paste its **magnet link**.

- Only the torrent's list of files is fetched (from the people sharing it, usually in seconds). Each file is matched to its episode the same way your downloads are, and the episode list marks them **STREAM**.
- Playing an episode downloads **just that file**, in order, and the built-in player starts within seconds and plays it while it downloads. First a progress bar shows it getting ready (the start of the episode, or where you left off, and the end of the file, where an MKV keeps its index), with the real amount, speed, people sharing it and time left; the player opens as soon as that's there (or press **Play now anyway**). Everything else in the torrent waits, so the episode you're watching gets all the speed. Skipping ahead downloads that part first. Its subtitle, if the torrent has one, comes first. Once the episode you're watching is all downloaded, the next one starts downloading, so moving on doesn't stop.
- **Keep downloaded episodes** (on the show's page): off, an episode you watch to the end credits is deleted **5 minutes** later. On, they stay. The bin button next to an episode deletes it any time.
- **Old torrent files are cleaned up:** every half hour, in the background, any file in the torrents folder that arrived there at least **48 hours** ago (counted from when it was downloaded into the folder, not the file's own dates) and hasn't been played since is deleted, unless it's a show's with **Keep downloaded episodes** on.
- **Packs before they're worked out:** while the app is still getting the franchise's facts from MyAnimeList, a pack's files already go by their names, so another season's episode 1 (or "To the Top") never plays as this title's episode 1.
- **Torrents window** (the download icon at the top, or **Files & downloads…**): every torrent and file, live: progress, speed, peers. Each torrent folds open and closed (**Expand all / Collapse all**), its files are grouped in their folders (each folder folds too), and full file names are shown. **Filter** finds files by name, episode or folder, and **Videos only** hides the rest. Choose which files download and how urgently (**Skip / Normal / High / Now**, for subtitles or extras too), say which episode a file is if it was matched wrong, delete files, pause, resume, open the folder, or remove a torrent.
- Files go in **Watchlist torrents** inside your downloads folder (download sorting skips that folder). Episodes you've finished downloading count as on your computer.
- **A torrent stops when it's done:** once everything you want from it is downloaded, it stops completely (no more uploading in the background) and shows **Done**. It starts again by itself when you play or ask for something it doesn't have yet.
- It's as fast as the torrent: with few people sharing it, or a download slower than the video, it pauses to buffer. While a torrent downloads it also uploads to others in it, as torrents do. The first time, Windows may ask whether Watch Anything may accept connections.
- **Packs** (a whole franchise in one torrent, like "Haikyuu!! S01+02+03+04+Movies+OVAs"): see below. Adding the same pack on another of its titles just shows it there.
- **Watch parties** work with streamed episodes: see **Watch together** below.

### Get magnet: your own torrent sites

In **Settings → Your torrent sites**, add your own sites, as many as you like: a name and its search link (search for any title on it, copy the results page's address, and put **{title}** where the name was), and tick what each one is for: **Anime**, **TV shows** and/or **Movies**. The list starts empty; no site comes with the app. **Test** searches it and shows what the app can read: the results, their sizes and seeders.

Then **Get magnet** on any anime, TV show or movie page (or **Get magnet from my site** in Watch now) searches the sites ticked for that kind:

- **Anime:** the torrent with every episode of the title (a pack with other seasons, films or OVAs counts).
- **TV shows:** every episode of the season you're on (a pack with every season is fine).
- **Movies:** the film itself (one big video file), in the best quality.

1. Your sites are searched for the title's name, its other names, and the series' name (which finds packs of every season).
2. From the results page it reads each result's name, size and seeders (from the column headed Seeders). Results about another anime, single episodes and results named after another title of the series ("To the Top" when you're on Second Season) are left out or ranked down.
3. The best ones' own pages are opened for their magnet link. When a page only shows the **info hash** as text (its magnet button needing JavaScript), the magnet is made from the hash, with the trackers the page lists. A .torrent file on the page works too.
4. The best few are checked for real: each one's list of files is fetched from the people sharing it (nothing is downloaded) and worked out the way any torrent is, so the app knows how many of this title's episodes each one really has, not just what its name says. A pack with other seasons, films or OVAs counts if it has all of this one.
5. Every episode first, then quality (1080p, Blu-ray) and seeders. With an AI key, your AI helper also compares them (it's given their names, sizes, seeders and what was found in their files).

It's quick: your sites are searched for every name at once, the results' pages are read together, and each torrent's files are checked the moment its magnet is known. Results appear one by one as they're found, best first, each showing what's being checked, and you can pick one before it's finished.

One that clearly wins is added straight away. When two or more are about as good, you choose: each shows its quality, size, seeders, how many episodes it has, and **See its files**. After that it's a torrent like any other: an episode downloads when you play it and is deleted after you watch it (unless kept), packs go to the right titles, and everything in the Torrents window works.

### Packs: a whole franchise in one torrent or folder

On MyAnimeList every season, part, film and OVA is its own title (Haikyuu!! has Haikyuu!!, Second Season, Karasuno vs. Shiratorizawa, To the Top, To the Top Part 2, four films and OVAs), but a pack has them all, often as "Season 1-4". So a pack isn't matched to one title:

1. Its files are put in groups: each season, each film, the OVAs/OADs, and extras (openings, endings), from the folder and file names.
2. The franchise's entries come from MyAnimeList (the title you added it to and everything connected to it), with each one's kind, number of episodes and date.
3. Each group is matched to its entry: Season N is the franchise's Nth TV series; a group whose name says which one it is ("To the Top") goes there; a season with more episodes than its entry carries on into the next one (To the Top's episodes 14-25 are To the Top Part 2's 1-12), and episodes numbered from the very start of the show are counted through the entries; films and OVAs go to the ones with the same name, or in release order.
4. **With an AI key**, your AI helper looks at the same groups and MyAnimeList's facts and matches them too. Its answer is used wherever it checks out against MyAnimeList (an episode number that doesn't exist is thrown out, and the rules' answer is kept for that group).

Each file then shows up on its own title, numbered the way MyAnimeList numbers it. Titles that aren't in your list yet are listed under the torrent: **What's matched to what…** shows every group, where it went and why (rules, AI or you), lets you change any group, **Add them all** to your list, or **Work it all out again**. In the **Torrents window** each file shows its title and episode, and you can point one file anywhere (any title, any episode). Your own downloads go through the same steps when you press **Sort now**, and anything it did can be undone under **Sorted for you**.


### Watch together

Watch an episode or a film in step with people on other computers. Each of you plays your own copy (a download or the same torrent) in the built-in player; when anyone pauses, presses play or skips, everyone's player does the same. Nothing else needs installing: the app talks to Syncplay's free public server itself.

**The party lives in its own panel**, not in the player, above everything else in the app (title pages, the episode list): the **Watch together** button at the top opens it, and on Windows and Mac **⧉** pops it out into its own small window that stays above every other window (the player too); **📌** lets it go behind, and it can be minimised like any window.

1. **Host:** click **Watch together** at the top (or on a title's page), and **Start a watch party**, with or without something playing yet. Copy the invite code (it starts with `WLP1.`) and send it. When you're streaming a torrent, the invite carries it (the exact file), so your guests' apps start downloading it the moment they join.
2. **Guests:** click **Watch together** at the top, paste the code and click **Join**.
3. **While a party is on, everything the host plays is for the party**, from any title (the play window says so at the top), always in the built-in player. Each guest's app gets the same thing ready by itself, **at the same time as the host's**, not after:
   - **The host streams a torrent:** the guest's app adds the same torrent and downloads that exact file (without working out the whole torrent first), straight from the host too when it can, with a load bar.
   - **The host plays a downloaded file:** the guest's app finds the same episode in their own downloads (even if it's named differently or not sorted yet), and adds the show to their list if needed. If it isn't there, they're asked to choose the file.
4. The host's player waits, paused, until everyone has it ready, then plays for all of you (or press **Space** to start anyway). Everyone sees everyone in the panel, with their picture: **Watching**, **On the way** (with how far along, and the time left) or **Needs the file**.

While you watch:

- When the host moves on to the next episode (or another title), everyone's app gets it ready the same way.
- **When the host closes the player**, everyone's player closes too; the party stays on for whatever's next.
- **Watch online** while hosting opens the same page on your guests' computers too (in their own browser).
- **A guest can leave** any time (**Leave the party**): the host and everyone else carry on. **When the host ends the party**, it ends for everyone.
- Messages appear in the player when someone joins, leaves, pauses or skips.
- **Your name and picture:** **Settings → Watch together** (**Choose a picture…**): move and zoom it inside the circle, then **Save**. It's shown round, made small, to everyone in the parties you're in.
- Someone using the Syncplay app can join too, with the server and room name shown under the invite.

The free public server (`syncplay.pl:8999`) is used unless you change it in **Settings → Watch together**. The connection is encrypted when the server allows it.

### AI helpers (optional)

The app recommends three free AI helpers: **Gemini**, **Groq** and **OpenRouter**. Add a key for any of them in **Settings → AI helpers** and click **Test**.

You can also **add your own**: **+ Add your own AI…** has OpenAI, Claude (Anthropic), DeepSeek, Mistral, xAI (Grok), Cerebras, Together AI, and Ollama or LM Studio running on your computer (no key needed), or **Another** for any OpenAI-style API. Fill in its key (and a model, if you like; otherwise the app picks a quick one) and **Test** it.

- **Gemini:** go to <https://aistudio.google.com/>, sign in with your Google account and click **Get API key → Create API key**. Google may use what's sent on the free tier to improve its products.
- **Groq:** go to <https://console.groq.com/>, sign up (no card needed), open **API Keys → Create API Key**.
- **OpenRouter:** go to <https://openrouter.ai/>, sign up, open **Keys → Create Key**. Only its free models are used: about 50 requests a day, shared between them.

Use one or several. They're asked in the order of the list; **↑ ↓** changes it:

- If a model is overloaded, slow or over its limit, the helper tries its next model. Gemini also falls back to its Lite and Gemma models, which are free on the same key and usually less busy.
- If a whole helper has run out for the day, is overloaded or can't be reached, the next helper answers instead. The one that failed is left alone for a while (from 5 minutes up to a few hours, depending on why), and Settings shows that.
- **Test** asks several models at once, keeps the one that answers first and uses it from then on. It shows what it tried, and the key is saved either way.

With a key:

- Titles it names are checked on MyAnimeList / TMDB before anything is added or shown, and its sorting decisions are listed under **Sorted for you** like the others.
- **✨ with AI** next to **Sort now** asks it about the downloads the rules and searches couldn't place.
- **Find everything connected** on a title page writes the story guide (how each title connects, when to watch it, canon or not) and also finds spin-offs, films and specials with other names.
- **Find Media → I'm in the mood for…** suggests what to watch, from your list and new titles.
- **Previously on…** on a title page gives a short, spoiler-free recap up to the episode you're on.

Only names are sent, never the files: file and folder names, the titles in your list that could match, for **Get magnet** the torrents' names, sizes and seeders, and for **I'm in the mood for…** your list's titles with how far you've got.

### Adding your own site

In **Settings → Watching online**, click **+ Add… → Your own site…**, then:

1. Search for any title on the site, for example "Frieren".
2. Copy the address of the results page, for example `https://example-site/search?keyword=Frieren`.
3. Replace the title with `{title}`, for example `https://example-site/search?keyword={title}`, and paste it in.
4. Tick which kinds of titles it's for (Anime, TV, Movies), set its **Check** to **No check** (or TMDB / AI if it's a service those know), and click **Try** to check it opens a search.

## Setup (Windows)

1. Optionally, install **VLC** from <https://www.videolan.org/> for the **Play in VLC** button. The built-in player is part of the app.
2. Download **Watch Anything.exe**:
   1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watch Anything** run.
   2. At the bottom, under **Artifacts**, click **WatchAnything-windows**. A zip downloads.
   3. Unzip it and put `Watch Anything.exe` wherever you like, such as your Desktop or a `C:\Apps` folder.
3. Double-click **Watch Anything.exe**. It opens in its own window; close the window to quit.
   - The first time, Windows may say "Windows protected your PC", because the app isn't signed by a paid certificate. Click **More info → Run anyway**.
4. In the app, click the **gear icon (Settings)** and paste:
   - your **TMDB token**: the long "API Read Access Token" (or the short "API Key") from themoviedb.org → Settings → API;
   - your **MyAnimeList Client ID**: from myanimelist.net/apiconfig.

   Click **Save & test keys** to check both.

To get a newer version later, download it the same way and replace the old `Watch Anything.exe` (or an old `Watchlist.exe`). Your list is kept separately, so it isn't lost.

To put it on the taskbar or Start menu, right-click `Watch Anything.exe` and choose **Pin to taskbar** or **Pin to Start**.

## Setup (Mac)

1. Optionally, install **VLC** from <https://www.videolan.org/> into **Applications** for the **Play in VLC** button. The built-in player is part of the app.
2. Download **WatchAnything.dmg**:
   1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watch Anything** run that has a Mac build.
   2. At the bottom, under **Artifacts**, click **WatchAnything-mac**. Open the zip that downloads, if your Mac doesn't open it for you.
3. Open **WatchAnything.dmg** and drag **Watch Anything** onto the **Applications** folder next to it.
4. The first time, macOS blocks it because the app isn't from a paid Apple developer account:
   1. Open Watch Anything from Applications. When macOS says it can't be opened, click **Done** (or **OK**).
   2. Open **System Settings → Privacy & Security**, scroll down to the message about Watch Anything, and click **Open Anyway**. Confirm with your password.

   After that it opens normally. This works on Apple Silicon Macs (M1 and newer).
5. In the app, open **Settings** and paste your TMDB token and MyAnimeList Client ID, as on Windows.

Watch Anything never makes itself the default app for your videos: its player is only used for what you play from the app.
Earlier versions could take over as the default video player.
To give that back to VLC, select a video in Finder, press **Cmd+I** (Get Info), choose **VLC** under **Open with** and click **Change All…**.
Do this once for each kind of video you have (for example once for an `.mkv` and once for an `.mp4`).

## Setup (Android phone)

1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watch Anything** run that has an Android build. Under **Artifacts**, download **WatchAnything-android** and unzip it to get **WatchAnything.apk**.
2. Copy the APK to your phone (or download it there) and open it. Android asks you to allow installing apps from that source (your browser or Files app): allow it, then tap **Install**. If Play Protect warns about an unknown developer, tap **More details → Install anyway**.
3. Open Watch Anything and paste your TMDB token and MyAnimeList Client ID in **Settings**, as on a computer.
4. To play your own video files, tap **Choose a folder** on a title. The app asks for **All files access** once: turn it on for Watch Anything and come back.

The phone app has everything the computer app has, with a phone layout (a tab bar at the bottom, full-screen panels):

- Videos play in the phone's player, with subtitles, the next episode after it, and Japanese audio for anime. Where you stopped is remembered.
- **Torrents** work the same way: paste a magnet, use **Get magnet**, and an episode streams while it downloads. The **Torrents** button opens the same list of files, priorities and deletes.
- **Watch together** works between phones and computers in any mix. The phone player pauses, plays and skips with everyone else, shows who did what at the top of the video, and opens by itself when the host plays something. Tap **Send…** to send the invite through any app.
- Links (streaming services, trailers, your sites) open in their phone apps or your browser.

The Windows/Mac-only settings (VLC, the player choice, the built-in browser and syncing through a shared folder) are hidden on a phone.

## Syncing between Windows and Mac

Your list can stay in sync between your computers through a folder that both of them sync.
That can be **Google Drive**, **OneDrive**, **Dropbox** or **iCloud Drive**; install that app on both computers if you haven't already.

This is optional: without a sync folder, your list is simply saved on that computer.

1. Make a folder for it in the cloud drive, for example `Google Drive/Watchlist`.
2. On the first computer, open **Settings → Sync between computers**, click **Choose…** and pick that folder. It's used straight away.
3. On the other computer, do the same, picking the same folder. If both computers already had titles, the two lists are merged.

From then on, anything you change on one computer shows up on the other within a few seconds of the cloud drive syncing it, even while the app is open.

- **What syncs:** titles and their info, progress and **Mark watched**, where you stopped in each episode, folders, collections and removed titles.
- **What stays on each computer:** the downloads folder and each show's own folder (Mac and Windows paths are different), your answers in **Sort your downloads**, "Not working" marks, your keys (including the AI keys), your own site links, and the VLC and browser locations.

  The video files themselves aren't copied. If they're in your cloud drive too, just choose the folder once on each computer.
- **Editing on both at once is fine.** Changes are merged title by title, including any "conflicted copy" files the cloud app makes.
- **If the sync folder isn't available** (the cloud drive isn't running yet), the app uses that computer's own copy of the list and shows a notice. It switches back once the folder is there.

### Running from source instead

If you'd rather run the Python code directly:

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>, ticking **"Add python.exe to PATH"**.
2. Download the code with **Code → Download ZIP** and unzip it.
3. Optionally run `py -m pip install -r requirements.txt` for the app window.
4. Double-click **`Watchlist.bat`**.

## Browser

Your streaming services, sites and YouTube trailers open in the browser you choose in **Settings → Browser**:

- **Built-in browser** (the default): a simple window of the app itself. Sites you sign in to there (HBO Max, Crunchyroll…) remember you, even after you close the app. **Sign out of every site in the built-in browser** (under Browser in Settings) forgets every login.
- **Recommended:** Opera GX when it's installed, otherwise your default browser.
- **Any browser found on your computer** (Chrome, Edge, Firefox, Brave, Opera, Safari…), listed by name.
- **Another… (Browse):** pick any browser's program (`.exe` on Windows, the app in Applications on a Mac).

Netflix and most paid services use copy protection that only full browsers support, so pick a real browser for those.

## Your data

Your list, collections and settings, including your keys, are saved only on your PC:

- **Watch Anything.exe:** in `%APPDATA%\Watchlist`. Paste that into File Explorer's address bar to open it. (The folder keeps its old name, so nothing is lost.)
- **Watch Anything.app:** in `~/Library/Application Support/Watchlist`. In Finder, press Shift+Cmd+G and paste that to open it.
- **With sync on,** the list itself (`watchlist.json`) is in your sync folder instead.
- **Running from source:** in the `data` folder next to the code. That folder is git-ignored, so it never goes into the repository.

Back that folder up if you reinstall your computer.

## Good to know

- **Episode files:** most naming styles work, for example:
  - `Show.S01E05.mkv`
  - `1x05`
  - `Season 1/05 - Title.mkv`
  - `[Group] Anime - 05 (1080p).mkv`

  If an episode isn't recognised, pick the file from the list in the play window.
- **Subtitles:** VLC loads a subtitle automatically when it has the same name as the video, like `Episode.srt` or `Episode.en.srt`. The app also checks `Subs` folders.
- **Which services have a title** is what TMDB reports for your country (**Settings → Your country**). If you use a VPN, change your country there too.
- **Streaming services play in your browser:** they use copy protection that only full browsers support, so they can't play inside this app.

## For developers

```
python -m unittest discover -s tests -t .   # run the tests
python main.py --browser                     # run in your normal browser
python main.py --self-test out.json          # check pywebview and the UI files are available
```

The app is plain Python (standard library only, plus optional `pywebview` and `libtorrent`) with a vanilla HTML/CSS/JS front end in `watchlist/web/`.
`Watch Anything.exe` is built with PyInstaller by the GitHub Actions workflow in `.github/workflows/build-exe.yml` on every push; `Watch Anything.app` is built when the workflow is run by hand or a commit message contains `[mac]`.
That workflow also runs the tests and smoke-tests the builds.
The built-in player's controls are [uosc](https://github.com/tomasklaen/uosc) (LGPL-2.1), fetched by the build; `watchlist/mpv_config/script-opts/uosc.conf` holds the app's settings for it. Running from source without it, mpv's own controls are used.
It serves the UI on `http://127.0.0.1:8765` and only accepts requests from this computer.
