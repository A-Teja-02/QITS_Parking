package com.quadrantitservices.parking

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.util.Log
import android.view.View
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import java.io.File
import com.quadrantitservices.parking.databinding.ActivityMainBinding

/**
 * Fullscreen WebView shell for the Quadrant Parking web app.
 * Package: com.quadrantitservices.parking
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar

    private val requestPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        Log.d(TAG, "Notification permission granted=$granted")
        fetchFcmToken()
    }

    // <input type="file"> support (Photo test "Upload photo" / "Take photo")
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var cameraPhotoUri: Uri? = null

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = filePathCallback ?: return@registerForActivityResult
        filePathCallback = null
        var uris: Array<Uri>? = null
        if (result.resultCode == RESULT_OK) {
            val data = result.data
            val clip = data?.clipData
            val single = data?.data
            val photo = cameraPhotoUri
            uris = when {
                clip != null && clip.itemCount > 0 -> Array(clip.itemCount) { clip.getItemAt(it).uri }
                single != null -> arrayOf(single)
                photo != null -> arrayOf(photo) // photo taken with the camera app
                else -> null
            }
        }
        callback.onReceiveValue(uris)
        cameraPhotoUri = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        webView = binding.webView
        progressBar = binding.progressBar

        NotificationHelper.createChannels(this)
        askNotificationPermission()
        fetchFcmToken()

        setupWebView()
        webView.loadUrl(BuildConfig.WEB_APP_URL)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                requestPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    private fun fetchFcmToken() {
        // Built without google-services.json -> Firebase is not set up; skip push instead of crashing
        if (FirebaseApp.getApps(this).isEmpty()) {
            Log.w(TAG, "Firebase not configured - push notifications disabled")
            return
        }
        try {
            FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                getSharedPreferences(ParkingFirebaseMessagingService.PREFS, MODE_PRIVATE)
                    .edit()
                    .putString(ParkingFirebaseMessagingService.KEY_TOKEN, token)
                    .apply()
                Log.d(TAG, "FCM token ready (${token.take(12)}...)")
                injectFcmTokenIntoPage()
            }
            .addOnFailureListener { e ->
                Log.e(TAG, "Failed to get FCM token", e)
            }
        } catch (e: Exception) {
            Log.e(TAG, "FCM unavailable", e)
        }
    }

    private fun createCameraIntent(): Intent? {
        return try {
            val dir = File(cacheDir, "camera").apply { mkdirs() }
            val photo = File.createTempFile("photo_", ".jpg", dir)
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", photo)
            cameraPhotoUri = uri
            Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                putExtra(MediaStore.EXTRA_OUTPUT, uri)
                addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Camera intent unavailable", e)
            cameraPhotoUri = null
            null
        }
    }

    private fun currentFcmToken(): String {
        return getSharedPreferences(ParkingFirebaseMessagingService.PREFS, MODE_PRIVATE)
            .getString(ParkingFirebaseMessagingService.KEY_TOKEN, "")
            .orEmpty()
    }

    private fun injectFcmTokenIntoPage() {
        val token = currentFcmToken()
        if (token.isBlank() || !::webView.isInitialized) return
        val safe = token.replace("\\", "\\\\").replace("'", "\\'")
        webView.post {
            webView.evaluateJavascript(
                "window.__FCM_TOKEN__='$safe';window.dispatchEvent(new Event('fcm-token'));",
                null
            )
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        webView.addJavascriptInterface(ParkingJsBridge(), "ParkingApp")

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = false
            displayZoomControls = false
            setSupportZoom(false)
            mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            cacheMode = WebSettings.LOAD_DEFAULT
            mediaPlaybackRequiresUserGesture = false
            userAgentString = userAgentString.replace("; wv", "")
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView?,
                request: WebResourceRequest?
            ): Boolean = false

            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                progressBar.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                progressBar.visibility = View.GONE
                injectFcmTokenIntoPage()
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                // Without this the shell just shows a blank white screen, which
                // is indistinguishable from the app failing to launch.
                if (request?.isForMainFrame != true) return
                val reason = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    error?.description?.toString().orEmpty()
                } else {
                    ""
                }
                Log.e(TAG, "Load failed for ${request.url}: $reason")
                showLoadError(reason)
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                progressBar.progress = newProgress
                progressBar.visibility = if (newProgress in 1..99) View.VISIBLE else View.GONE
            }

            // WebView ignores <input type="file"> unless the app opens a picker itself.
            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>?,
                params: FileChooserParams?
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                val cameraIntent = createCameraIntent()
                val intent = if (params?.isCaptureEnabled == true && cameraIntent != null) {
                    cameraIntent // "Take photo" button: open the camera directly
                } else {
                    val pick = params?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                        addCategory(Intent.CATEGORY_OPENABLE)
                        type = "image/*"
                    }
                    Intent.createChooser(pick, "Choose photo").apply {
                        if (cameraIntent != null) putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(cameraIntent))
                    }
                }
                return try {
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: ActivityNotFoundException) {
                    filePathCallback = null
                    callback?.onReceiveValue(null)
                    false
                }
            }
        }
    }

    private fun showLoadError(reason: String) {
        progressBar.visibility = View.GONE
        val detail = reason.ifBlank { "The server did not respond." }
        val html = """
            <html><head><meta name="viewport"
              content="width=device-width,initial-scale=1"></head>
            <body style="margin:0;font-family:sans-serif;background:#F8F9FA;
                         color:#1E3A5F;display:flex;align-items:center;
                         justify-content:center;height:100vh">
              <div style="text-align:center;padding:28px;max-width:420px">
                <h2 style="margin:0 0 12px">Can't reach the server</h2>
                <p style="margin:0 0 8px;line-height:1.5">$detail</p>
                <p style="margin:0 0 20px;font-size:13px;color:#5A6B7F;
                          word-break:break-all">${BuildConfig.WEB_APP_URL}</p>
                <p style="margin:0 0 20px;font-size:13px;color:#5A6B7F;
                          line-height:1.5">Check that this phone is on the same
                   Wi-Fi as the server PC.</p>
                <button onclick="ParkingApp.reload()"
                        style="background:#1E3A5F;color:#fff;border:0;
                               border-radius:8px;padding:13px 26px;
                               font-size:15px">Retry</button>
              </div>
            </body></html>
        """.trimIndent()
        webView.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    inner class ParkingJsBridge {
        @JavascriptInterface
        fun getFcmToken(): String = currentFcmToken()

        @JavascriptInterface
        fun isNativeApp(): Boolean = true

        @JavascriptInterface
        fun reload() {
            webView.post { webView.loadUrl(BuildConfig.WEB_APP_URL) }
        }
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onPause() {
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "MainActivity"
    }
}
