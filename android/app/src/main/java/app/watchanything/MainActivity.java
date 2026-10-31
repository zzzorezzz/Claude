package app.watchanything;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * Watch Anything on a phone: the app's own engine (its Python code) runs on the
 * phone and serves its screens to this WebView, the same as on a computer.
 * This side does what only the phone can: open links in their apps, play
 * videos in the phone player, reach your files, share, and the back button.
 */
public class MainActivity extends Activity {
    private static final int PICK_FILE = 41;
    private static final int ASK_FILES = 42;

    private WebView web;
    private FrameLayout root;
    private View fullscreen;
    private ValueCallback<Uri[]> pickCallback;
    private WebChromeClient chrome;
    private Pages pages;
    static int port = 0;

    /** What the app's engine calls to open a link (Python → Java). */
    public class Bridge {
        public void openUrl(String url) {
            runOnUiThread(() -> openLink(url));
        }

        /** A watch party opening the player (a guest following the host, or the host starting one). */
        public void openPlayer(String json) {
            runOnUiThread(() -> startPlayer(json));
        }

        /** A watch party telling the open player what to do. */
        public void playerCommand(String session, String json) {
            PlayerActivity.command(session, json);
        }

        // The video finder's hidden pages (Pages.java, droidweb.py).
        public String pageOpen(String scripts, String blocked) {
            return pages.open(scripts, blocked);
        }

        public String pageUserAgent() {
            return pages.userAgent();
        }

        public void pageGo(String id, String url) {
            pages.go(id, url);
        }

        public String pageRun(String id, String js) {
            return pages.run(id, js);
        }

        public String pageEvents(String id) {
            return pages.events(id);
        }

        public void pageClose(String id) {
            pages.close(id);
        }

        public void pageShow(String id) {
            pages.show(id);
        }
    }

    /** What the screens call (JavaScript → Java). */
    public class Phone {
        @JavascriptInterface
        public void play(String json) {
            runOnUiThread(() -> startPlayer(json));
        }

        @JavascriptInterface
        public void openUrl(String url) {
            runOnUiThread(() -> openLink(url));
        }

        @JavascriptInterface
        public void share(String text) {
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType("text/plain");
            send.putExtra(Intent.EXTRA_TEXT, text);
            startActivity(Intent.createChooser(send, "Share"));
        }

        @JavascriptInterface
        public boolean hasFiles() {
            return canReadFiles();
        }

        @JavascriptInterface
        public void askForFiles() {
            runOnUiThread(MainActivity.this::askForFileAccess);
        }
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0d0f1a"));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0d0f1a"));
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        pages = new Pages(this, root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        web.addJavascriptInterface(new Phone(), "Android");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("127.0.0.1".equals(uri.getHost()) || "localhost".equals(uri.getHost())) return false;
                openLink(uri.toString());        // anything else: its own app or the browser
                return true;
            }
        });
        chrome = new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (pickCallback != null) pickCallback.onReceiveValue(null);
                pickCallback = callback;
                try {
                    startActivityForResult(params.createIntent(), PICK_FILE);
                } catch (ActivityNotFoundException e) {
                    pickCallback = null;
                    return false;
                }
                return true;
            }

            // A trailer in full screen.
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                fullscreen = view;
                root.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                web.setVisibility(View.GONE);
            }

            @Override
            public void onHideCustomView() {
                if (fullscreen != null) root.removeView(fullscreen);
                fullscreen = null;
                web.setVisibility(View.VISIBLE);
            }
        };
        web.setWebChromeClient(chrome);

        web.loadDataWithBaseURL(null, "<html><body style='background:#0d0f1a;color:#aab2ca;font-family:sans-serif;"
                + "display:flex;align-items:center;justify-content:center;height:90vh'>Starting Watch Anything…</body></html>",
                "text/html", "utf-8", null);
        startEngine();
    }

    private void startEngine() {
        new Thread(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                if (port == 0) {
                    PyObject android = Python.getInstance().getModule("watchlist.android");
                    port = android.callAttr("start", new Bridge(), getFilesDir().getAbsolutePath()).toInt();
                }
                runOnUiThread(() -> web.loadUrl("http://127.0.0.1:" + port + "/"));
                // The build server's live check of the recommended sites (adb: --es live "names or all").
                String live = getIntent().getStringExtra("live");
                if (live != null) {
                    String only = "all".equals(live) ? "" : live;
                    Python.getInstance().getModule("watchlist.livecheck").callAttr("run_phone",
                            getFilesDir().getAbsolutePath() + "/live-report.json",
                            Python.getInstance().getBuiltins().callAttr("str", only).callAttr("split"));
                }
            } catch (Throwable e) {
                String msg = String.valueOf(e.getMessage()).replace("<", "&lt;");
                runOnUiThread(() -> web.loadDataWithBaseURL(null,
                        "<html><body style='background:#0d0f1a;color:#fff;font-family:sans-serif;padding:24px'>"
                                + "<h2>Watch Anything couldn't start</h2><pre style='white-space:pre-wrap;color:#ff9aa8'>" + msg
                                + "</pre></body></html>", "text/html", "utf-8", null));
            }
        }).start();
    }

    private void startPlayer(String json) {
        Intent i = new Intent(MainActivity.this, PlayerActivity.class);
        i.putExtra("json", json);
        i.putExtra("port", port);
        startActivity(i);
    }

    private void openLink(String url) {
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "No app can open that link", Toast.LENGTH_SHORT).show();
        }
    }

    private boolean canReadFiles() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) return Environment.isExternalStorageManager();
        return checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
    }

    private void askForFileAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + getPackageName())));
            } catch (ActivityNotFoundException e) {
                startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            }
        } else {
            requestPermissions(new String[]{Manifest.permission.READ_EXTERNAL_STORAGE}, ASK_FILES);
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_FILE && pickCallback != null) {
            pickCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            pickCallback = null;
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Back from the player or another app: the page catches up (progress, files).
        if (web != null && port != 0) web.evaluateJavascript("window.reload && reload().catch(()=>{})", null);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (fullscreen != null) {
            chrome.onHideCustomView();
            return;
        }
        if (pages.back()) return;
        web.evaluateJavascript("window.androidBack ? androidBack() : false", value -> {
            if (!"true".equals(value)) moveTaskToBack(true);
        });
    }
}
