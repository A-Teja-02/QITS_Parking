package com.quadrantitservices.parking

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Receives Firebase Cloud Messaging pushes for the parking APK.
 */
class ParkingFirebaseMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.d(TAG, "FCM token refreshed")
        getSharedPreferences(PREFS, MODE_PRIVATE)
            .edit()
            .putString(KEY_TOKEN, token)
            .apply()
        // WebView will pick this up via ParkingApp.getFcmToken() after login
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)

        val title = message.notification?.title
            ?: message.data["title"]
            ?: "Quadrant Parking"

        val body = message.notification?.body
            ?: message.data["body"]
            ?: message.data["message"]
            ?: "You have a parking update."

        NotificationHelper.show(this, title, body)
    }

    companion object {
        private const val TAG = "ParkingFCM"
        const val PREFS = "parking_fcm"
        const val KEY_TOKEN = "fcm_token"
    }
}
