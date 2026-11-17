import java.util.Properties
import java.io.FileInputStream

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Firebase push needs app/google-services.json (download from the Firebase console).
// Without it the app still builds and runs - just without push notifications.
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}

// Website the app opens. Set it once in android/gradle.properties (parkingWebUrl=...),
// e.g. http://192.168.1.20:5173 for testing on the office Wi-Fi.
val webAppUrl: String = (project.findProperty("parkingWebUrl") as String?)?.trim()
    ?.takeIf { it.isNotEmpty() } ?: "https://parkingapp.quadrantitservices.com"

val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties()
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}

android {
    namespace = "com.quadrantitservices.parking"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.quadrantitservices.parking"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        // Local LAN URL for phone testing (same Wi‑Fi as this PC).
        // Change to your live HTTPS URL when you deploy.
        buildConfigField(
            "String",
            "WEB_APP_URL",
            "\"$webAppUrl\""
        )
    }

    signingConfigs {
        create("release") {
            if (keystorePropertiesFile.exists()) {
                storeFile = file(keystoreProperties["storeFile"] as String)
                storePassword = keystoreProperties["storePassword"] as String
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
            }
        }
    }

    buildTypes {
        debug {
            // Physical phone on same Wi‑Fi → PC LAN IP
            // Emulator only: use http://10.0.2.2:5173
            buildConfigField(
                "String",
                "WEB_APP_URL",
                "\"$webAppUrl\""
            )
        }
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            buildConfigField(
                "String",
                "WEB_APP_URL",
                "\"$webAppUrl\""
            )
            if (keystorePropertiesFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.activity:activity-ktx:1.9.3")

    implementation(platform("com.google.firebase:firebase-bom:34.10.0"))
    implementation("com.google.firebase:firebase-analytics")
    implementation("com.google.firebase:firebase-messaging")
}
