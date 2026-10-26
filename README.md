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

1. Install **Python 3.10 or newer** from <https://www.python.org/downloads/>. On the first installer screen, tick **"Add python.exe to PATH"**.
2. Install **VLC** from <https://www.videolan.org/>. The app finds it automatically.
3. Download this project: on GitHub, use **Code → Download ZIP**, then unzip it anywhere.
4. Optional, but gives the app its own window: open a terminal in the folder and run
   ```
   py -m pip install -r requirements.txt
   ```
   Without it, the app opens in an Opera GX window instead, which works just as well.
5. Double-click **`Watchlist.bat`**.
   - A small black window stays open while the app runs; close it to quit.
6. In the app, click the **gear icon (Settings)** and paste:
   - your **TMDB token**: the long "API Read Access Token" (or the short "API Key") from themoviedb.org → Settings → API;
   - your **MyAnimeList Client ID**: from myanimelist.net/apiconfig.

   Click **Save & test keys** to check both.

## Browser

Netflix, HBO Max, everythingmoe.com and YouTube trailers open in **Opera GX**.
The app finds Opera GX automatically.
If it isn't found, Settings says so: click **Browse** next to "Browser" and pick Opera GX's `launcher.exe`, usually in `C:\Users\<you>\AppData\Local\Programs\Opera GX\`.
If no Opera GX is set, links open in your Windows default browser.

## Your data

Your list, collections and settings, including your keys, are saved in the `data` folder next to the app.
That folder is git-ignored, so it never goes into the repository.
Back it up if you reinstall.

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
```

The app is plain Python (standard library only) plus a vanilla HTML/CSS/JS front end in `watchlist/web/`.
It serves the UI on `http://127.0.0.1:8765` and only accepts requests from this computer.
