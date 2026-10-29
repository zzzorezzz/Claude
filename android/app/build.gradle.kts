// The phone app: the same Watch Anything (its Python, its screens) running on
// the phone, in a WebView, with a phone video player (Media3 ExoPlayer).
plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "app.watchanything"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.watchanything"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        getByName("debug") {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        // The app reads its screens (web/) and settings files next to its code.
        extractPackages("watchlist")
        pip {
            install("certifi")
        }
    }
}

dependencies {
    implementation("androidx.media3:media3-exoplayer:1.2.1")
    implementation("androidx.media3:media3-ui:1.2.1")
    // Torrents: the same libtorrent as on a computer, for the phone's processors.
    val jlibtorrent = "2.0.12.9"
    implementation("com.frostwire:jlibtorrent:$jlibtorrent")
    implementation("com.frostwire:jlibtorrent-android-arm64:$jlibtorrent")
    implementation("com.frostwire:jlibtorrent-android-x86_64:$jlibtorrent")
}
