package app.watchanything;

import android.app.Activity;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Message;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The video finder's hidden pages (droidweb.py): a video site's page opened in a
 * WebView behind the app's own screen, every request any of its frames makes
 * told to the app with its headers, its ads not loaded, its pop-ups not opened.
 * Called from the app's engine (Python threads); the WebViews live on the UI thread.
 */
final class Pages {
    private final Activity activity;
    private final FrameLayout root;
    private final Map<String, Hidden> pages = new ConcurrentHashMap<>();
    private final AtomicInteger next = new AtomicInteger();
    private String ua = "";

    private static final class Hidden {
        WebView web;
        final JSONArray events = new JSONArray();
        Set<String> blocked = Collections.emptySet();
        String[] scripts = new String[0];
        boolean shown;

        synchronized void add(JSONObject ev) {
            if (events.length() < 5000) events.put(ev);
        }

        synchronized String drain() {
            String out = events.toString();
            for (int i = events.length() - 1; i >= 0; i--) events.remove(i);
            return out;
        }
    }

    Pages(Activity activity, FrameLayout root) {
        this.activity = activity;
        this.root = root;
        // A page's background workers ask for things too (a player's playlist, sometimes):
        // told to every page being watched.
        try {
            if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_BASIC_USAGE)
                    && WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_SHOULD_INTERCEPT_REQUEST)) {
                androidx.webkit.ServiceWorkerControllerCompat.getInstance().setServiceWorkerClient(
                        new androidx.webkit.ServiceWorkerClientCompat() {
                            @Override
                            public WebResourceResponse shouldInterceptRequest(WebResourceRequest req) {
                                String url = req.getUrl().toString();
                                if (url.startsWith("http")) for (Hidden h : pages.values()) tell(h, url, req);
                                return null;
                            }
                        });
            }
        } catch (Exception ignored) {
        }
    }

    /** On the UI thread, waiting for it (up to `seconds`). */
    private <T> T onUi(int seconds, java.util.concurrent.Callable<T> work) {
        AtomicReference<T> out = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        activity.runOnUiThread(() -> {
            try {
                out.set(work.call());
            } catch (Exception ignored) {
            } finally {
                done.countDown();
            }
        });
        try {
            done.await(seconds, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
        return out.get();
    }

    String userAgent() {
        if (ua.isEmpty()) {
            // A computer's Chrome (the same version as the phone's): sites then give the same
            // player as on a computer, whose video requests the app sees. (A phone's player hands
            // its video to Android's own playback, which the app isn't told about.)
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("Chrome/([\\d.]+)")
                    .matcher(WebSettings.getDefaultUserAgent(activity));
            String version = m.find() ? m.group(1) : "124.0.0.0";
            ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/"
                    + version + " Safari/537.36";
        }
        return ua;
    }

    String open(String scriptsJson, String blockedJson) {
        Hidden h = new Hidden();
        try {
            JSONArray s = new JSONArray(scriptsJson);
            h.scripts = new String[s.length()];
            for (int i = 0; i < s.length(); i++) h.scripts[i] = s.getString(i);
            JSONArray b = new JSONArray(blockedJson);
            Set<String> hosts = new HashSet<>();
            for (int i = 0; i < b.length(); i++) hosts.add(b.getString(i).toLowerCase());
            h.blocked = hosts;
        } catch (Exception ignored) {
        }
        String id = "p" + next.incrementAndGet();
        String agent = userAgent();
        WebView web = onUi(10, () -> make(h, agent));
        if (web == null) return "";
        h.web = web;
        pages.put(id, h);
        return id;
    }

    private WebView make(Hidden h, String agent) {
        WebView web = new WebView(activity);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setSupportMultipleWindows(true);          // (and none opened: pop-ups go nowhere)
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setUserAgentString(agent);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);
        boolean early = WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT);
        if (early) {
            // In every frame, before the page's own scripts (the players from other sites too).
            for (String js : h.scripts) WebViewCompat.addDocumentStartJavaScript(web, js, Collections.singleton("*"));
        }
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView view, boolean dialog, boolean user, Message msg) {
                return false;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                Uri uri = req.getUrl();
                String url = uri.toString();
                String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase();
                if (host.endsWith(".invalid")) {             // what the app's own scripts tell it
                    tell(h, url, req);
                    return empty(204);
                }
                if (isBlocked(h.blocked, host)) return empty(200);
                if (url.startsWith("http")) tell(h, url, req);
                return null;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                String scheme = req.getUrl().getScheme();
                // An app link (intent:, market:...) opens nothing.
                return !("http".equals(scheme) || "https".equals(scheme));
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap icon) {
                if (!early) for (String js : h.scripts) view.evaluateJavascript(js, null);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                try {
                    h.add(new JSONObject().put("t", "load").put("u", url));
                } catch (Exception ignored) {
                }
            }
        });
        // Behind the app's own screen (full size, so its player lays out and plays as it would).
        web.setVisibility(View.VISIBLE);
        root.addView(web, 0, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        return web;
    }

    private static boolean isBlocked(Set<String> blocked, String host) {
        if (blocked.isEmpty() || host.isEmpty()) return false;
        String h = host;
        while (true) {
            if (blocked.contains(h)) return true;
            int dot = h.indexOf('.');
            if (dot < 0) return false;
            h = h.substring(dot + 1);
        }
    }

    private static WebResourceResponse empty(int status) {
        return new WebResourceResponse("text/plain", "utf-8", status, status == 204 ? "No Content" : "OK",
                new HashMap<>(), new ByteArrayInputStream(new byte[0]));
    }

    private static void tell(Hidden h, String url, WebResourceRequest req) {
        try {
            JSONObject headers = new JSONObject();
            Map<String, String> got = req.getRequestHeaders();
            if (got != null) for (Map.Entry<String, String> e : got.entrySet()) headers.put(e.getKey(), e.getValue());
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null && !cookie.isEmpty()) headers.put("Cookie", cookie);
            h.add(new JSONObject().put("t", "req").put("u", url).put("h", headers)
                    .put("k", req.isForMainFrame() ? "Document" : ""));
        } catch (Exception ignored) {
        }
    }

    void go(String id, String url) {
        Hidden h = pages.get(id);
        if (h != null) activity.runOnUiThread(() -> h.web.loadUrl(url));
    }

    String run(String id, String js) {
        Hidden h = pages.get(id);
        if (h == null) return "";
        AtomicReference<String> out = new AtomicReference<>("");
        CountDownLatch done = new CountDownLatch(1);
        activity.runOnUiThread(() -> h.web.evaluateJavascript(js, value -> {
            out.set(value == null ? "" : value);
            done.countDown();
        }));
        try {
            done.await(20, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
        return out.get();
    }

    String events(String id) {
        Hidden h = pages.get(id);
        if (h == null) throw new IllegalStateException("closed");
        return h.drain();
    }

    /** A page you use yourself (finding a video, picker.py): in front of the app's screen. */
    void show(String id) {
        Hidden h = pages.get(id);
        if (h == null) return;
        h.shown = true;
        activity.runOnUiThread(() -> {
            h.web.bringToFront();
            h.web.requestFocus();
            android.widget.Toast.makeText(activity, "Play the video here, then press Back to choose it",
                    android.widget.Toast.LENGTH_LONG).show();
        });
    }

    /** Back pressed: a page you were using goes back behind the app (and the app is told). */
    boolean back() {
        for (Hidden h : pages.values()) {
            if (!h.shown) continue;
            if (h.web.canGoBack()) {
                h.web.goBack();
                return true;
            }
            h.shown = false;
            root.removeView(h.web);
            root.addView(h.web, 0);
            try {
                h.add(new JSONObject().put("t", "closed"));
            } catch (Exception ignored) {
            }
            return true;
        }
        return false;
    }

    void close(String id) {
        Hidden h = pages.remove(id);
        if (h == null) return;
        activity.runOnUiThread(() -> {
            root.removeView(h.web);
            h.web.stopLoading();
            h.web.destroy();
        });
    }
}
