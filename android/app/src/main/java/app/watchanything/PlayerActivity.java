package app.watchanything;

import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.datasource.DefaultDataSource;
import androidx.media3.datasource.DefaultHttpDataSource;
import androidx.media3.exoplayer.DefaultLoadControl;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory;
import androidx.media3.ui.PlayerView;

import com.chaquo.python.Python;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The phone's video player: plays an episode (and the ones after it) with its
 * subtitles, and tells the app where you are, so it resumes there next time
 * and moves you on to the next episode once you've watched one to the end.
 *
 * In a watch party it is also the app's player for the party: it tells the app
 * what it's doing many times a second, and the app tells it to pause, play,
 * skip, catch up, show a message or play the next thing (command()).
 */
public class PlayerActivity extends Activity {
    private static volatile PlayerActivity current;

    private ExoPlayer player;
    private DefaultHttpDataSource.Factory http;
    private PlayerView view;
    private TextView message;
    private TextView otherSource;
    private android.widget.ProgressBar loadingBar;

    /** Every episode of the list (the whole season or show), to play any of them. */
    private void chooseEpisode() {
        int n = player.getMediaItemCount();
        if (n < 2) return;
        String[] names = new String[n];
        for (int i = 0; i < n; i++) {
            CharSequence title = player.getMediaItemAt(i).mediaMetadata.title;
            names[i] = (i == player.getCurrentMediaItemIndex() ? "▶  " : "") + (title != null ? title : "Episode " + (i + 1));
        }
        new android.app.AlertDialog.Builder(this)
                .setTitle("Episodes")
                .setItems(names, (d, which) -> {
                    if (player.getPlaybackState() == Player.STATE_BUFFERING && which != player.getCurrentMediaItemIndex()) {
                        show("Still loading - one moment", 1500);
                        return;
                    }
                    player.seekTo(which, 0);
                })
                .show();
    }
    private final Runnable offerOtherSource = () -> {
        if (otherSource != null && !session.isEmpty()) otherSource.setVisibility(View.VISIBLE);
    };
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService net = Executors.newSingleThreadExecutor();
    private final ExecutorService events = Executors.newSingleThreadExecutor();
    private final List<String> keys = new ArrayList<>();
    private final Set<String> finished = new HashSet<>();
    private String itemId = "";
    private String session = "";
    private boolean keepOpen;
    private boolean inParty;
    private boolean anime;
    private int port;
    private long lastReport;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (System.currentTimeMillis() - lastReport >= 5000) {
                lastReport = System.currentTimeMillis();
                reportNow(false);
            }
            tellApp(false);
            handler.postDelayed(this, 500);
        }
    };

    private final Runnable hideMessage = () -> message.setVisibility(View.GONE);

    /** From the app (a watch party): do these things, if this is still the player it means. */
    static void command(String sessionId, String json) {
        PlayerActivity p = current;
        if (p == null || !sessionId.equals(p.session)) return;
        p.runOnUiThread(() -> p.run(json));
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        PlayerActivity old = current;
        if (old != null && old != this) old.finish();     // one player at a time
        current = this;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        port = getIntent().getIntExtra("port", 0);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        view = new PlayerView(this);
        view.setShowSubtitleButton(true);
        view.setShowNextButton(true);
        view.setShowPreviousButton(true);
        view.setControllerShowTimeoutMs(3500);
        view.setKeepScreenOn(true);
        root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        message = new TextView(this);
        message.setTextColor(Color.WHITE);
        message.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        message.setBackgroundColor(0xB3141826);
        int pad = (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, 10, getResources().getDisplayMetrics());
        message.setPadding(pad * 2, pad, pad * 2, pad);
        message.setVisibility(View.GONE);
        FrameLayout.LayoutParams at = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        at.topMargin = pad * 3;
        root.addView(message, at);
        // Loading for 5 seconds: a small offer at the side to try the episode's next source.
        otherSource = new TextView(this);
        otherSource.setText("Not working?\nTry another source");
        otherSource.setTextColor(Color.WHITE);
        otherSource.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        otherSource.setBackgroundColor(0xD9221B3A);
        otherSource.setPadding(pad * 2, pad, pad * 2, pad);
        otherSource.setVisibility(View.GONE);
        otherSource.setOnClickListener(v -> {
            otherSource.setVisibility(View.GONE);
            show("Trying another source…", 4000);
            final String sid = session;
            new Thread(() -> {
                try {
                    Python.getInstance().getModule("watchlist.android").callAttr("other_source", sid);
                } catch (Throwable ignored) {
                }
            }).start();
        });
        FrameLayout.LayoutParams side = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.END | Gravity.CENTER_VERTICAL);
        side.rightMargin = pad * 2;
        root.addView(otherSource, side);
        // Anything loading: a thin bar moving along the top (and the player's own spinner).
        loadingBar = new android.widget.ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        loadingBar.setIndeterminate(true);
        loadingBar.setVisibility(View.GONE);
        FrameLayout.LayoutParams top = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, pad / 2,
                Gravity.TOP);
        root.addView(loadingBar, top);
        view.setShowBuffering(PlayerView.SHOW_BUFFERING_ALWAYS);
        // Every episode of the list, to jump to any of them.
        TextView episodes = new TextView(this);
        episodes.setText("☰  Episodes");
        episodes.setTextColor(Color.WHITE);
        episodes.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        episodes.setBackgroundColor(0xB3141826);
        episodes.setPadding(pad * 2, pad, pad * 2, pad);
        episodes.setOnClickListener(v -> chooseEpisode());
        FrameLayout.LayoutParams corner = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.END);
        corner.topMargin = pad * 2;
        corner.rightMargin = pad * 2;
        root.addView(episodes, corner);
        view.setControllerVisibilityListener((PlayerView.ControllerVisibilityListener) visibility ->
                episodes.setVisibility(visibility == View.VISIBLE && player != null && player.getMediaItemCount() > 1
                        ? View.VISIBLE : View.GONE));
        episodes.setVisibility(View.GONE);
        setContentView(root);
        hideBars(view);

        // Torrent episodes stream from the app while they download: wait patiently for data.
        http = new DefaultHttpDataSource.Factory()
                .setConnectTimeoutMs(30_000).setReadTimeoutMs(180_000).setAllowCrossProtocolRedirects(true);
        player = new ExoPlayer.Builder(this)
                .setMediaSourceFactory(new DefaultMediaSourceFactory(new DefaultDataSource.Factory(this, http)))
                .setLoadControl(new DefaultLoadControl.Builder().setBufferDurationsMs(15_000, 90_000, 1_500, 3_000).build())
                .build();
        // Next and Previous wait while something loads (pressed again and again they'd only pile
        // more on): they say so instead.
        view.setPlayer(new androidx.media3.common.ForwardingPlayer(player) {
            private boolean loading() {
                if (getPlaybackState() != Player.STATE_BUFFERING) return false;
                show("Still loading - one moment", 1500);
                return true;
            }

            @Override
            public void seekToNext() {
                if (!loading()) super.seekToNext();
            }

            @Override
            public void seekToNextMediaItem() {
                if (!loading()) super.seekToNextMediaItem();
            }

            @Override
            public void seekToPrevious() {
                if (!loading()) super.seekToPrevious();
            }

            @Override
            public void seekToPreviousMediaItem() {
                if (!loading()) super.seekToPreviousMediaItem();
            }
        });
        try {
            JSONObject play = new JSONObject(getIntent().getStringExtra("json"));
            load(play);
        } catch (Exception e) {
            Toast.makeText(this, "Couldn't open that: " + e.getMessage(), Toast.LENGTH_LONG).show();
            finish();
            return;
        }
        player.addListener(new Player.Listener() {
            @Override
            public void onMediaItemTransition(MediaItem item, int reason) {
                // The one before ended by itself: it counts as watched.
                if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) {
                    int prev = player.getCurrentMediaItemIndex() - 1;
                    if (prev >= 0 && prev < keys.size()) markFinished(keys.get(prev));
                }
                tellApp(false);
            }

            @Override
            public void onPlaybackStateChanged(int st) {
                handler.removeCallbacks(offerOtherSource);
                loadingBar.setVisibility(st == Player.STATE_BUFFERING ? View.VISIBLE : View.GONE);
                if (st == Player.STATE_BUFFERING) handler.postDelayed(offerOtherSource, 5000);
                else otherSource.setVisibility(View.GONE);
                if (st == Player.STATE_ENDED) {
                    reportNow(true);
                    if (!keepOpen) finish();
                }
                tellApp(false);
            }

            @Override
            public void onPlayWhenReadyChanged(boolean playWhenReady, int reason) {
                tellApp(false);
            }

            @Override
            public void onPositionDiscontinuity(Player.PositionInfo oldPos, Player.PositionInfo newPos, int reason) {
                tellApp(false);
            }

            @Override
            public void onPlayerError(PlaybackException error) {
                Toast.makeText(PlayerActivity.this, "This video can't play on this phone ("
                        + error.getErrorCodeName() + ")", Toast.LENGTH_LONG).show();
            }
        });
        handler.postDelayed(tick, 500);
    }

    /** What to play: the list of entries, where to start, playing or paused. */
    private void load(JSONObject play) throws Exception {
        itemId = play.optString("item_id");
        session = play.optString("session", "");
        keepOpen = play.optBoolean("keep_open", false);
        inParty = play.optBoolean("party", false);
        anime = play.optBoolean("anime", anime);
        JSONArray entries = play.getJSONArray("entries");
        // A video from a site: asked for as its page asked (the same Referer, Origin, browser
        // and cookies: sites check).
        for (int i = 0; i < entries.length(); i++) {
            JSONObject e = entries.getJSONObject(i);
            String referer = e.optString("referer", "");
            if (!referer.isEmpty() && !"null".equals(referer)) {
                Map<String, String> headers = new HashMap<>();
                headers.put("Referer", referer);
                String agent = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36";
                JSONObject more = e.optJSONObject("headers");
                if (more != null) {
                    for (java.util.Iterator<String> it = more.keys(); it.hasNext(); ) {
                        String k = it.next();
                        if ("User-Agent".equalsIgnoreCase(k)) agent = more.optString(k, agent);
                        else if ("Origin".equalsIgnoreCase(k) || "Cookie".equalsIgnoreCase(k)) headers.put(k, more.optString(k));
                    }
                }
                http.setDefaultRequestProperties(headers);
                http.setUserAgent(agent);
                break;
            }
        }
        // The season's other episodes are the app's own links (found as they open): the same
        // kind of video as the one found already, which the player can't tell from the link.
        String hint = MimeTypes.APPLICATION_M3U8;
        for (int i = 0; i < entries.length(); i++) {
            String p = entries.getJSONObject(i).optString("path", "").toLowerCase();
            if (p.contains("/online/")) continue;
            String kind = entries.getJSONObject(i).optString("mime", "");
            if ("dash".equals(kind) || p.contains(".mpd")) hint = MimeTypes.APPLICATION_MPD;
            else if (p.contains(".mp4") || p.contains(".mkv") || p.contains(".webm")) hint = null;
            if (p.startsWith("http")) break;
        }
        List<MediaItem> items = new ArrayList<>();
        keys.clear();
        finished.clear();
        for (int i = 0; i < entries.length(); i++) {
            JSONObject e = entries.getJSONObject(i);
            keys.add(e.optString("key", "movie"));
            MediaItem.Builder b = new MediaItem.Builder()
                    .setUri(toUri(e.getString("path")))
                    .setMediaId(e.optString("key", "movie"))
                    .setMediaMetadata(new MediaMetadata.Builder().setTitle(e.optString("label")).build());
            String kind = e.optString("mime", "");
            if ("hls".equals(kind)) b.setMimeType(MimeTypes.APPLICATION_M3U8);
            else if ("dash".equals(kind)) b.setMimeType(MimeTypes.APPLICATION_MPD);
            else if (e.getString("path").contains("/online/") && hint != null) b.setMimeType(hint);
            String sub = e.optString("sub", "");
            if (!sub.isEmpty() && !"null".equals(sub)) {
                b.setSubtitleConfigurations(Collections.singletonList(
                        new MediaItem.SubtitleConfiguration.Builder(toUri(sub))
                                .setMimeType(subtitleType(sub))
                                .setLanguage("en")
                                .setSelectionFlags(C.SELECTION_FLAG_DEFAULT)
                                .build()));
            }
            items.add(b.build());
        }
        // Anime: the original Japanese audio with subtitles, as on a computer.
        player.setTrackSelectionParameters(player.getTrackSelectionParameters().buildUpon()
                .setPreferredAudioLanguages(anime ? new String[]{"ja", "jpn"} : new String[]{})
                .setPreferredTextLanguages("en", "eng")
                .setSelectUndeterminedTextLanguage(true)
                .build());
        long start = (long) (play.optDouble("start", 0) * 1000);
        int first = Math.max(0, Math.min(items.size() - 1, play.optInt("index", 0)));
        player.setMediaItems(items, first, start);
        player.prepare();
        player.setPlayWhenReady(!play.optBoolean("paused", false));
    }

    private void run(String json) {
        if (player == null) return;
        try {
            JSONArray list = new JSONArray(json);
            for (int i = 0; i < list.length(); i++) {
                JSONObject c = list.getJSONObject(i);
                switch (c.optString("do")) {
                    case "seek":
                        player.seekTo((long) (c.optDouble("to", 0) * 1000));
                        break;
                    case "pause":
                        player.setPlayWhenReady(false);
                        break;
                    case "play":
                        player.setPlayWhenReady(true);
                        break;
                    case "speed":
                        player.setPlaybackSpeed((float) c.optDouble("to", 1.0));
                        break;
                    case "text":
                        show(c.optString("text"), c.optInt("ms", 3000));
                        break;
                    case "replace":
                        load(c.getJSONObject("launch"));
                        break;
                    case "index":
                        if (c.optInt("to", 0) < player.getMediaItemCount()) player.seekTo(c.optInt("to", 0), 0);
                        break;
                    case "close":
                        session = "";            // the app knows already
                        finish();
                        return;
                    default:
                        break;
                }
            }
        } catch (Exception ignored) {
            // A command we couldn't follow: the next one will put things right.
        }
        tellApp(false);
    }

    private void show(String text, int ms) {
        message.setText(text);
        message.setVisibility(View.VISIBLE);
        handler.removeCallbacks(hideMessage);
        handler.postDelayed(hideMessage, Math.max(800, ms));
    }

    /** What this player is doing, for a watch party. */
    private void tellApp(boolean ended) {
        if (session.isEmpty() || player == null && !ended) return;
        final String sid = session;
        final String json;
        try {
            JSONObject o = new JSONObject().put("ended", ended);
            if (player != null) {
                long dur = player.getDuration();
                o.put("index", player.getCurrentMediaItemIndex())
                        .put("pos", player.getCurrentPosition() / 1000.0)
                        .put("dur", dur == C.TIME_UNSET ? 0 : dur / 1000.0)
                        .put("paused", !player.getPlayWhenReady());
            }
            json = o.toString();
        } catch (Exception e) {
            return;
        }
        events.execute(() -> {
            try {
                Python.getInstance().getModule("watchlist.android").callAttr("player_event", sid, json);
            } catch (Throwable ignored) {
                // The app will hear the next one.
            }
        });
    }

    private static Uri toUri(String path) {
        if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("content://")) return Uri.parse(path);
        return Uri.fromFile(new File(path));
    }

    private static String subtitleType(String path) {
        String p = path.toLowerCase();
        if (p.endsWith(".ass") || p.endsWith(".ssa")) return MimeTypes.TEXT_SSA;
        if (p.endsWith(".vtt")) return MimeTypes.TEXT_VTT;
        return MimeTypes.APPLICATION_SUBRIP;
    }

    private void hideBars(View v) {
        v.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
    }

    /** Where you are now; and, once into the end credits (90% or the last 2 minutes), that it's watched. */
    private void reportNow(boolean ended) {
        if (player == null) return;
        int index = player.getCurrentMediaItemIndex();
        if (index < 0 || index >= keys.size()) return;
        String key = keys.get(index);
        long pos = player.getCurrentPosition();
        long dur = player.getDuration();
        if (dur == C.TIME_UNSET || dur <= 0) return;
        if (ended || pos >= dur * 0.9 || dur - pos <= 120_000) {
            markFinished(key);
        } else if (!finished.contains(key)) {
            send(key, pos / 1000.0, dur / 1000.0, false);
        }
    }

    private void markFinished(String key) {
        if (finished.add(key)) send(key, 0, 0, true);
    }

    private void send(String key, double pos, double dur, boolean done) {
        final String body;
        try {
            body = new JSONObject().put("item_id", itemId).put("key", key).put("pos", pos).put("dur", dur)
                    .put("finished", done).toString();
        } catch (Exception e) {
            return;
        }
        net.execute(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:" + port + "/api/android/progress").openConnection();
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setConnectTimeout(3000);
                c.setReadTimeout(5000);
                c.setRequestProperty("Content-Type", "application/json");
                c.setRequestProperty("X-Watchlist", "1");
                try (OutputStream o = c.getOutputStream()) {
                    o.write(body.getBytes(StandardCharsets.UTF_8));
                }
                c.getResponseCode();
                c.disconnect();
            } catch (Exception ignored) {
                // The app will hear about the next one.
            }
        });
    }

    @Override
    protected void onPause() {
        super.onPause();
        reportNow(false);
        // In a watch party the others keep watching: leaving the player for a moment doesn't pause them.
        if (player != null && !inParty) player.pause();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(tick);
        if (player != null) {
            reportNow(false);
            player.release();
            player = null;
        }
        tellApp(true);
        if (current == this) current = null;
        net.shutdown();
        events.shutdown();
        super.onDestroy();
    }
}
