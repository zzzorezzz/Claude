package app.watchanything;

import android.app.Activity;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowManager;
import android.widget.Toast;

import androidx.media3.common.C;
import androidx.media3.common.MediaItem;
import androidx.media3.common.MediaMetadata;
import androidx.media3.common.MimeTypes;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The phone's video player: plays an episode (and the ones after it) with its
 * subtitles, and tells the app where you are, so it resumes there next time
 * and moves you on to the next episode once you've watched one to the end.
 */
public class PlayerActivity extends Activity {
    private ExoPlayer player;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService net = Executors.newSingleThreadExecutor();
    private final List<String> keys = new ArrayList<>();
    private final Set<String> finished = new HashSet<>();
    private String itemId = "";
    private int port;

    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            reportNow(false);
            handler.postDelayed(this, 5000);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        port = getIntent().getIntExtra("port", 0);
        PlayerView view = new PlayerView(this);
        view.setShowSubtitleButton(true);
        view.setShowNextButton(true);
        view.setShowPreviousButton(true);
        view.setControllerShowTimeoutMs(3500);
        view.setKeepScreenOn(true);
        setContentView(view);
        hideBars(view);

        player = new ExoPlayer.Builder(this).build();
        view.setPlayer(player);
        try {
            JSONObject play = new JSONObject(getIntent().getStringExtra("json"));
            itemId = play.optString("item_id");
            JSONArray entries = play.getJSONArray("entries");
            List<MediaItem> items = new ArrayList<>();
            for (int i = 0; i < entries.length(); i++) {
                JSONObject e = entries.getJSONObject(i);
                keys.add(e.optString("key", "movie"));
                MediaItem.Builder b = new MediaItem.Builder()
                        .setUri(toUri(e.getString("path")))
                        .setMediaId(e.optString("key", "movie"))
                        .setMediaMetadata(new MediaMetadata.Builder().setTitle(e.optString("label")).build());
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
                    .setPreferredAudioLanguages(play.optBoolean("anime") ? new String[]{"ja", "jpn"} : new String[]{})
                    .setPreferredTextLanguages("en", "eng")
                    .setSelectUndeterminedTextLanguage(true)
                    .build());
            long start = (long) (play.optDouble("start", 0) * 1000);
            player.setMediaItems(items, 0, start);
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
            }

            @Override
            public void onPlaybackStateChanged(int st) {
                if (st == Player.STATE_ENDED) {
                    reportNow(true);
                    finish();
                }
            }

            @Override
            public void onPlayerError(PlaybackException error) {
                Toast.makeText(PlayerActivity.this, "This video can't play on this phone ("
                        + error.getErrorCodeName() + ")", Toast.LENGTH_LONG).show();
            }
        });
        player.prepare();
        player.setPlayWhenReady(true);
        handler.postDelayed(tick, 5000);
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

    private void hideBars(View view) {
        view.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
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
        if (player != null) player.pause();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(tick);
        if (player != null) {
            player.release();
            player = null;
        }
        net.shutdown();
        super.onDestroy();
    }
}
