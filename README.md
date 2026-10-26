# Watchlist

A Netflix-style desktop app for Windows and Mac, for the TV shows, anime and movies you plan to watch.
Your list can sync between your computers.

- **Home screen:** three rows (TV Shows, Anime, Movies), each showing your 5–6 most popular titles and a **Show more** tile for the full list.
  - Movies are ordered by box office, TV shows by TMDB popularity, and anime by MyAnimeList members.
- **Top bar:** search, **Sort by year** (newest first, oldest first, then back to popular) and a **Genre** filter.
- **Adding titles:** use **+** in any row. The summary, ratings, cast, seasons and trailer are filled in automatically, from TMDB for movies and TV and from MyAnimeList for anime.
- **Folders:** on the TV Shows, Anime and Movies pages, click **New folder** to make your own folders. Drag a poster onto a folder to move it there, or right-click a poster (or use **⋯**) → **Move to folder**. A title is in one folder at a time.
- **Collections:** these can mix TV shows, anime and movies, and a title can be in several. Find them on the **Collections** page.
- **Title page:** backdrop, summary, ratings and an episode list. The trailer only plays when you click it.
- **Watch now:** your file comes first, then online.
  - **The first time,** it asks **On my PC** or **Online**. Pick On my PC, choose the folder with the episodes, and it's remembered.
  - **After that,** Watch now just plays the episode you're up to in VLC (episode 1 to start), followed by the next episodes in order. It loads the matching `.srt`/`.ass` subtitles too.
  - **If the file is missing or won't start,** the app goes online instead:
    1. **Netflix**, then **HBO Max**, if either has it in your country (Pakistan by default), opened in **Opera GX**.
    2. Otherwise, **everythingmoe.com**.
  - **To play a different episode,** click it in the title page's episode list. After pressing Watch now, you can also click **Wrong episode? Choose the file** to see every file in the folder.
- **When a file doesn't work:** if the app thinks a file is fine but it isn't, press **Not working**. It's in the play window, and in the **On your PC** section of the title page.
  - Watch now then goes online for that title, and its poster shows **FILE ISSUE**.
  - Fix it later from the title page with **Choose file…** or **Change folder**, or press **It works now**.

## Setup (Windows)

1. Install **VLC** from <https://www.videolan.org/>. The app finds it automatically.
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

1. Install **VLC** from <https://www.videolan.org/> and drag it into **Applications**. The app finds it there.
2. Download **Watchlist.dmg**:
   1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watchlist (Windows + Mac)** run.
   2. At the bottom, under **Artifacts**, click **Watchlist-mac**. Open the zip that downloads, if your Mac doesn't open it for you.
3. Open **Watchlist.dmg** and drag **Watchlist** onto the **Applications** folder next to it.
4. The first time, macOS blocks it because the app isn't from a paid Apple developer account:
   1. Open Watchlist from Applications. When macOS says it can't be opened, click **Done** (or **OK**).
   2. Open **System Settings → Privacy & Security**, scroll down to the message about Watchlist, and click **Open Anyway**. Confirm with your password.

   After that it opens normally. This works on Apple Silicon Macs (M1 and newer).
5. In the app, open **Settings** and paste your TMDB token and MyAnimeList Client ID, as on Windows.

## Syncing between Windows and Mac

Your list can stay in sync between your computers through a folder that both of them sync.
That can be **Google Drive**, **OneDrive**, **Dropbox** or **iCloud Drive**; install that app on both computers if you haven't already.

1. Make a folder for it in the cloud drive, for example `Google Drive/Watchlist`.
2. On the first computer, open **Settings → Sync between computers**, click **Choose…**, pick that folder and click **Save**.
3. On the other computer, do the same, picking the same folder. If both computers already had titles, the two lists are merged.

From then on, anything you change on one computer shows up on the other within a few seconds of the cloud drive syncing it, even while the app is open.

- **What syncs:** titles and their info, progress and **Mark watched**, folders, collections and removed titles.
- **What stays on each computer:** each show's video folder (Mac and Windows paths are different), "Not working" marks, your keys and the VLC/Opera GX locations.

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

Netflix, HBO Max, everythingmoe.com and YouTube trailers open in **Opera GX**.
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
- **HBO Max in Pakistan:** HBO Max may not be available in Pakistan. The app only uses what TMDB reports for your country.
  - The HBO Max and Netflix links can be changed in Settings.
  - If you use a VPN, you can change your country there too.
- **Netflix and HBO Max play in your browser:** they use copy protection that only full browsers support, so they can't play inside this app.

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
