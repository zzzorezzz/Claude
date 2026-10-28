# Watchlist

A Netflix-style desktop app for Windows and Mac, for the TV shows, anime and movies you plan to watch.

Two things are optional, and everything works without them:

- **A sync folder.** Without one, your list is saved on this computer only. With one (Google Drive, OneDrive...), your list stays the same on your Windows PC and your Mac.
- **An AI key** (Gemini, Groq or OpenRouter, all free). Without one, the app's own rules and MyAnimeList / TMDB searches sort your downloads and find connected titles. With one, the AI helps with the files those can't place, finds everything connected to a title, suggests what to watch and writes recaps.

- **Home screen:** three rows (TV Shows, Anime, Movies), each showing your 5–6 most popular titles and a **Show more** tile for the full list.
  - Movies are ordered by box office, TV shows by TMDB popularity, and anime by MyAnimeList members.
- **Top bar:** the sections (folded into **Browse ▾** when the window is narrow, or while you search), the search (click the magnifier), **Popular / Newest / Oldest**, a **Genre** filter, **🎲 Surprise me**, **Torrents** (once you have one), **Watch together** and **Settings**.
- **🎲 Surprise me:** picks something from your list for you. Narrow it down by type, the time you have (under 30 minutes, about an hour, a whole evening), something new or something to carry on with, genre, or only what's on your computer. **Pick another** rolls again.
- **Adding titles:** use **+** in any row. The summary, ratings, cast, seasons and trailer are filled in automatically, from TMDB for movies and TV and from MyAnimeList for anime.
  - Anime always come from MyAnimeList: adding an anime from TMDB (say from TV Shows) adds its MyAnimeList entry instead.
  - An anime added from TMDB before this version shows **Use MyAnimeList instead** on its page (also in **⋯ → Use MyAnimeList info**). Pick the right entry: its files, where you're up to, folders and collections are kept, episode numbers are converted (TMDB's season 2 episode 3 becomes episode 10 when season 1 had 7), and it moves to Anime. If that entry is already in your list, the two become one.
- **Folders:** on the TV Shows, Anime and Movies pages, click **New folder** to make your own folders. Drag a poster onto a folder to move it there, or right-click a poster (or use **⋯**) → **Move to folder**. A title is in one folder at a time.
- **Collections:** these can mix TV shows, anime and movies, and a title can be in several. Find them on the **Collections** page.
  - **Share** (next to each collection) gives a share code to paste into any chat, or **Save as a file instead** (saved to your Downloads folder). Only the titles are shared: no files, progress or keys.
  - **Open a shared collection…** (top of the Collections page): paste the code or choose the file to see every title, then **Add it to my Watchlist** adds the collection and the titles you don't have yet.
- **Watched:** **⋯ → Mark as watched** on any title moves it out of your everyday rows (Home, TV Shows, Anime, Movies, Continue watching) into the **Watched** page, kept as your collection. **⋯ → Move back to my list** undoes it. Watched titles still show in collections and search, marked WATCHED.
- **Continue watching:** the first row on the home screen. Click a poster to carry on where you stopped.
- **Title page:** backdrop, summary, ratings and an episode list. The trailer only plays when you click it.
  - **Everything connected**, on the right: press **Find everything connected** to find every season, sequel, prequel, OVA, ONA, special, film and spin-off. When it's done, the **story guide** opens (and **Open the story guide** brings it back):
    - At the top, how the whole franchise fits together and the best way into it, and how many titles are complete on your computer, partly downloaded or missing.
    - Every title in **Release order** or **Story order** (the order things happen in the story), numbered. Each one says how it connects to the main story, when to watch it, whether it's canon, anime-original, non-canon or a recap, and whether it's essential, recommended, optional or skippable, plus what it's about.
    - Everything is spoiler-free; switch on **Show spoilers** to also see how each one ties into the story.
    - Each title shows what's on your computer ("8 of 12 episodes", "All 12 episodes", waiting in your downloads, or not on your computer), with **+ Add to my list** or **Open**.
    - The facts (kind, year, episodes, how it's linked) come from MyAnimeList for anime (the whole franchise, following its relations) and TMDB for film series. The explanations are written by your AI helper; without an AI key you get the facts and the release order. With one, the AI also finds connected titles with other names (each checked on MyAnimeList / TMDB first). Without one, TV shows list titles that share their name.
    - The panel itself lists everything grouped by kind, with **Add all** for what's missing.
  - **Episodes:** each episode says whether it's on your PC (with its file name) or **Not downloaded**, and a line above the list says how many you have and which are missing.
    - **Move…** next to a file moves just that file: to another title, season or episode, out of the title (to sort later), or ignored. **Files…** in **On your PC** lists every file of the title, each with **Move…**.
  - **From the same folder:** films, OVAs, specials and extras that sit in the same folder as a series' files are shown on every page of that series, and on their own page. Click one to open it, or play it.
  - **Extras:** openings, endings, trailers and other bonus videos found with the show's files, kept apart from the episodes.
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
  - **I'm in the mood for…:** tell your AI helper what you feel like watching. It may ask one or two questions, then picks a few titles from your list and about a dozen new ones, each with a reason. New titles are checked on MyAnimeList / TMDB, so made-up ones don't show.
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
- **Watch now:** a file on your computer plays straight away. When there isn't one, you choose: **Stream from the torrent** (or paste a magnet link right there), **Watch online**, or **Find the file on my PC**.
  - **On your computer:** it plays the episode you're up to in the **built-in player** (mpv), followed by the next episodes in order, with matching `.srt`/`.ass` subtitles.
    - The player handles MKV and HEVC files, several audio and subtitle tracks, and fully styled anime subtitles.
    - It remembers where you stopped, even on your other computer, and resumes from there. An episode counts as watched once you reach the end credits, and you move on to the next one.
    - **Play in VLC** on the title page, or **Open in VLC instead** while something is playing, switches to VLC at the same spot.
    - Click any episode in the episode list to play it, or use **Choose file…** to pick any file.
    - **Openings and endings are skipped** when the file marks them as chapters (most anime releases do). Press **Shift+S** while watching to turn this on or off, or change it in Settings.
  - **Online:** your own list of streaming services and sites (**Settings → Watching online**): add as many as you like (Netflix, HBO Max, Disney+, Prime Video, Crunchyroll, Hulu, Apple TV+ are ready-made, or any site with a search link), remove them, switch them off, put them in order, and choose which are for anime, TV and movies.
    - A service you subscribe to can be ticked **Only when it has it**: it's then only used when TMDB says it has the title in your country. A site is searched every time.
    - **Watch online** shows them all with whether each has it, and opens the one you pick; **Best match** opens the first in your order that fits.
    - They open in your browser (Opera GX or Chrome). The app only opens the search; it doesn't read or play anything from the site.
  - **Deleting an episode:** the bin button next to an episode's file. Your own files go to the Recycle Bin / Trash (so you can put them back); a torrent's is deleted. Your own files are never deleted by the app itself.
- **When a file doesn't work:** press **Not working**. It's in the play window, and in the **On your PC** section of the title page.
  - Watch now then goes online for that title, and its poster shows **FILE ISSUE**.
  - Fix it later from the title page with **Choose file…** or **Use a different folder…**, or press **It works now**.

### Streaming from a torrent

On a show's page, under **Stream from a torrent**, paste its **magnet link**.

- Only the torrent's list of files is fetched (from the people sharing it, usually in seconds). Each file is matched to its episode the same way your downloads are, and the episode list marks them **STREAM**.
- Playing an episode downloads **just that file**, in order, and the built-in player starts within seconds and plays it while it downloads. First a progress bar shows it getting ready (the start of the episode, or where you left off, and the end of the file, where an MKV keeps its index), with the real amount, speed, people sharing it and time left; the player opens as soon as that's there (or press **Play now anyway**). Everything else in the torrent waits, so the episode you're watching gets all the speed. Skipping ahead downloads that part first. Its subtitle, if the torrent has one, comes first. Once the episode you're watching is all downloaded, the next one starts downloading, so moving on doesn't stop.
- **Keep downloaded episodes** (on the show's page): off, an episode you watch to the end credits is deleted **5 minutes** later; one you stop part way through is kept. On, they stay. The bin button next to an episode deletes it any time.
- **Torrents window** (the download icon at the top, or **Files & downloads…**): every torrent and file, live: progress, speed, peers. Choose which files download and how urgently (**Skip / Normal / High / Now**, for subtitles or extras too), say which episode a file is if it was matched wrong, delete files, pause, resume, open the folder, or remove a torrent.
- Files go in **Watchlist torrents** inside your downloads folder (download sorting skips that folder). Episodes you've finished downloading count as on your computer.
- It's as fast as the torrent: with few people sharing it, or a download slower than the video, it pauses to buffer. While a torrent downloads it also uploads to others in it, as torrents do. The first time, Windows may ask whether Watchlist may accept connections.
- **Packs** (a whole franchise in one torrent, like "Haikyuu!! S01+02+03+04+Movies+OVAs"): see below. Adding the same pack on another of its titles just shows it there.
- **Watch parties** work with streamed episodes: if the other person doesn't have the episode, their app adds the same torrent to the show and streams it, and moving on to the next episode loads it on both sides without leaving the party.

### Get magnet: your own torrent site (anime)

In **Settings → Your torrent site**, add your own site: a name and its search link (search for any anime on it, copy the results page's address, and put **{title}** where the name was). The list starts empty; no site comes with the app. **Test** searches it and shows what the app can read: the results, their sizes and seeders.

Then **Get magnet** on an anime's page (or **Get magnet from my site** in Watch now):

1. Your site is searched for the anime's MyAnimeList name, its English name, and the series' name (which finds packs of every season).
2. From the results page it reads each result's name, size and seeders (from the column headed Seeders). Results about another anime, single episodes and results named after another title of the series ("To the Top" when you're on Second Season) are left out or ranked down.
3. The best ones' own pages are opened for their magnet link. When a page only shows the **info hash** as text (its magnet button needing JavaScript), the magnet is made from the hash, with the trackers the page lists. A .torrent file on the page works too.
4. The best few are checked for real: each one's list of files is fetched from the people sharing it (nothing is downloaded) and worked out the way any torrent is, so the app knows how many of this title's episodes each one really has, not just what its name says. A pack with other seasons, films or OVAs counts if it has all of this one.
5. Every episode first, then quality (1080p, Blu-ray) and seeders. With an AI key, your AI helper also compares them (it's given their names, sizes, seeders and what was found in their files).

It's quick: your site is searched for every name at once, the results' pages are read together, and each torrent's files are checked the moment its magnet is known. Results appear one by one as they're found, best first, each showing what's being checked, and you can pick one before it's finished.

One that clearly wins is added straight away. When two or more are about as good, you choose: each shows its quality, size, seeders, how many episodes it has, and **See its files**. After that it's a torrent like any other: an episode downloads when you play it and is deleted after you watch it (unless kept), packs go to the right titles, and everything in the Torrents window works.

### Packs: a whole franchise in one torrent or folder

On MyAnimeList every season, part, film and OVA is its own title (Haikyuu!! has Haikyuu!!, Second Season, Karasuno vs. Shiratorizawa, To the Top, To the Top Part 2, four films and OVAs), but a pack has them all, often as "Season 1-4". So a pack isn't matched to one title:

1. Its files are put in groups: each season, each film, the OVAs/OADs, and extras (openings, endings), from the folder and file names.
2. The franchise's entries come from MyAnimeList (the title you added it to and everything connected to it), with each one's kind, number of episodes and date.
3. Each group is matched to its entry: Season N is the franchise's Nth TV series; a group whose name says which one it is ("To the Top") goes there; a season with more episodes than its entry carries on into the next one (To the Top's episodes 14-25 are To the Top Part 2's 1-12), and episodes numbered from the very start of the show are counted through the entries; films and OVAs go to the ones with the same name, or in release order.
4. **With an AI key**, your AI helper looks at the same groups and MyAnimeList's facts and matches them too. Its answer is used wherever it checks out against MyAnimeList (an episode number that doesn't exist is thrown out, and the rules' answer is kept for that group).

Each file then shows up on its own title, numbered the way MyAnimeList numbers it. Titles that aren't in your list yet are listed under the torrent: **What's matched to what…** shows every group, where it went and why (rules, AI or you), lets you change any group, **Add them all** to your list, or **Work it all out again**. In the **Torrents window** each file shows its title and episode, and you can point one file anywhere (any title, any episode). Your own downloads go through the same steps when you press **Sort now**, and anything it did can be undone under **Sorted for you**.


### Watch together

**Watch together** on a title page plays an episode in step with someone on another computer. Each of you plays your own copy in the built-in player. When either of you pauses, presses play or skips, the other player does the same. Nothing else needs installing: the app talks to Syncplay's free public server itself.

1. You: open the title, click **Watch together**, pick the episode and click **Start the watch party**. The player opens, paused.
2. Copy the invite code it shows (it starts with `WLP1.`) and send it to the other person.
3. Them: click the **people** button at the top of Watchlist, paste the code and click **Join**.
   - Their app finds the same episode in their own downloads, even if it's named differently or not sorted yet, and adds the show to their list if needed.
   - If it isn't found, they're asked to choose the file.
4. Once they've joined, press **Space** in the player to start for both of you.

While you watch:

- When the host moves on to the next episode, the other player loads its own copy of it.
- When the host closes the player (or ends the party), the other player closes too.
- Messages appear in the player when someone joins, leaves, pauses or skips.
- Someone using the Syncplay app can join too, with the server and room name shown under the invite.

The free public server (`syncplay.pl:8999`) is used unless you change it in **Settings → Watch together**. The connection is encrypted when the server allows it.

### AI helpers (optional)

The app can use three free AI helpers: **Gemini**, **Groq** and **OpenRouter**. Add a key for any of them in **Settings → AI helpers** and click **Test**.

- **Gemini:** go to <https://aistudio.google.com/>, sign in with your Google account and click **Get API key → Create API key**. Google may use what's sent on the free tier to improve its products.
- **Groq:** go to <https://console.groq.com/>, sign up (no card needed), open **API Keys → Create API Key**.
- **OpenRouter:** go to <https://openrouter.ai/>, sign up, open **Keys → Create Key**. Only its free models are used: about 50 requests a day, shared between them.

You can add one, two or all three. They're asked in that order:

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

In **Settings → Watching online**, choose **+ Add a service or site… → Another site**, then:

1. Search for any title on the site, for example "Frieren".
2. Copy the address of the results page, for example `https://example-site/search?keyword=Frieren`.
3. Replace the title with `{title}`, for example `https://example-site/search?keyword={title}`, and paste it in.
4. Tick which kinds of titles it's for (Anime, TV, Movies), and click **Try** to check it opens a search.

## Setup (Windows)

1. Optionally, install **VLC** from <https://www.videolan.org/> for the **Play in VLC** button. The built-in player is part of the app.
2. Download **Watchlist.exe**:
   1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watchlist (Windows + Mac)** run.
   2. At the bottom, under **Artifacts**, click **Watchlist-exe**. A zip downloads.
   3. Unzip it and put `Watchlist.exe` wherever you like, such as your Desktop or a `C:\Apps` folder.
3. Double-click **Watchlist.exe**. It opens in its own window; close the window to quit.
   - The first time, Windows may say "Windows protected your PC", because the app isn't signed by a paid certificate. Click **More info → Run anyway**.
4. In the app, click the **gear icon (Settings)** and paste:
   - your **TMDB token**: the long "API Read Access Token" (or the short "API Key") from themoviedb.org → Settings → API;
   - your **MyAnimeList Client ID**: from myanimelist.net/apiconfig.

   Click **Save & test keys** to check both.

To get a newer version later, download it the same way and replace the old `Watchlist.exe`. Your list is kept separately, so it isn't lost.

To put it on the taskbar or Start menu, right-click `Watchlist.exe` and choose **Pin to taskbar** or **Pin to Start**.

## Setup (Mac)

1. Optionally, install **VLC** from <https://www.videolan.org/> into **Applications** for the **Play in VLC** button. The built-in player is part of the app.
2. Download **Watchlist.dmg**:
   1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watchlist (Windows + Mac)** run.
   2. At the bottom, under **Artifacts**, click **Watchlist-mac**. Open the zip that downloads, if your Mac doesn't open it for you.
3. Open **Watchlist.dmg** and drag **Watchlist** onto the **Applications** folder next to it.
4. The first time, macOS blocks it because the app isn't from a paid Apple developer account:
   1. Open Watchlist from Applications. When macOS says it can't be opened, click **Done** (or **OK**).
   2. Open **System Settings → Privacy & Security**, scroll down to the message about Watchlist, and click **Open Anyway**. Confirm with your password.

   After that it opens normally. This works on Apple Silicon Macs (M1 and newer).
5. In the app, open **Settings** and paste your TMDB token and MyAnimeList Client ID, as on Windows.

Watchlist never makes itself the default app for your videos: its player is only used for what you play from the app.
Earlier versions could take over as the default video player.
To give that back to VLC, select a video in Finder, press **Cmd+I** (Get Info), choose **VLC** under **Open with** and click **Change All…**.
Do this once for each kind of video you have (for example once for an `.mkv` and once for an `.mp4`).

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

Your streaming services, sites and YouTube trailers open in **Opera GX** (or the browser you pick in Settings).
The app finds Opera GX automatically.
If it isn't found, Settings says so: click **Browse** next to "Browser" and pick Opera GX.
On Windows that's `launcher.exe`, usually in `C:\Users\<you>\AppData\Local\Programs\Opera GX\`; on a Mac it's **Opera GX** in Applications.
If no Opera GX is set, links open in your default browser.

## Your data

Your list, collections and settings, including your keys, are saved only on your PC:

- **Watchlist.exe:** in `%APPDATA%\Watchlist`. Paste that into File Explorer's address bar to open it.
- **Watchlist.app:** in `~/Library/Application Support/Watchlist`. In Finder, press Shift+Cmd+G and paste that to open it.
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

The app is plain Python (standard library only, plus optional `pywebview`) with a vanilla HTML/CSS/JS front end in `watchlist/web/`.
`Watchlist.exe` and `Watchlist.app` are built with PyInstaller by the GitHub Actions workflow in `.github/workflows/build-exe.yml` on every push.
That workflow also runs the tests on Windows and macOS and smoke-tests both builds.
It serves the UI on `http://127.0.0.1:8765` and only accepts requests from this computer.
