# Watchlist

A Netflix-style desktop app for the TV shows, anime and movies you plan to watch.

- **Home screen:** three rows (TV Shows, Anime, Movies), each showing your 5–6 most popular titles and a **Show more** tile for the full list.
  - Movies are ordered by box office, TV shows by TMDB popularity, and anime by MyAnimeList members.
- **Top bar:** search, **Sort by year** (newest first, oldest first, then back to popular) and a **Genre** filter.
- **Adding titles:** use **+** in any row. The summary, ratings, cast, seasons and trailer are filled in automatically, from TMDB for movies and TV and from MyAnimeList for anime.
- **Collections:** your own folders. A title can be in several; right-click a poster (or use **⋯**) to add it to one.
- **Title page:** backdrop, summary, ratings and an episode list. The trailer only plays when you click it.
- **Watch now → On my PC:**
  - The first time, you pick the folder with the episodes, and it's remembered.
  - The right episode plays in VLC, with the following episodes queued.
  - Matching `.srt`/`.ass` subtitle files are found and loaded. You can switch subtitles, pick another subtitle file, or open its folder.
- **Watch now → Online:**
  1. The app checks whether the title is on **Netflix**, then **HBO Max**, in your country (Pakistan by default), and opens that service in **Opera GX**.
  2. If neither has it, **everythingmoe.com** opens.

## Setup (Windows)

1. Install **VLC** from <https://www.videolan.org/>. The app finds it automatically.
2. Download **Watchlist.exe**:
   1. On GitHub, open the repo's **Actions** tab and click the latest green **Build Watchlist.exe** run.
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

### Running from source instead

If you'd rather run the Python code directly:

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>, ticking **"Add python.exe to PATH"**.
2. Download the code with **Code → Download ZIP** and unzip it.
3. Optionally run `py -m pip install -r requirements.txt` for the app window.
4. Double-click **`Watchlist.bat`**.

## Browser

Netflix, HBO Max, everythingmoe.com and YouTube trailers open in **Opera GX**.
The app finds Opera GX automatically.
If it isn't found, Settings says so: click **Browse** next to "Browser" and pick Opera GX's `launcher.exe`, usually in `C:\Users\<you>\AppData\Local\Programs\Opera GX\`.
If no Opera GX is set, links open in your Windows default browser.

## Your data

Your list, collections and settings, including your keys, are saved only on your PC:

- **Watchlist.exe:** in `%APPDATA%\Watchlist`. Paste that into File Explorer's address bar to open it.
- **Running from source:** in the `data` folder next to the code. That folder is git-ignored, so it never goes into the repository.

Back that folder up if you reinstall Windows.

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
`Watchlist.exe` is built with PyInstaller by the GitHub Actions workflow in `.github/workflows/build-exe.yml` on every push.
That workflow also runs the tests on Windows and smoke-tests the built `.exe`.
It serves the UI on `http://127.0.0.1:8765` and only accepts requests from this computer.
