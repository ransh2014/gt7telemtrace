plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}
android {
    namespace = "app.trace"
    compileSdk = 34
    defaultConfig {
        applicationId = "app.trace.gt7"
        minSdk = 26
        targetSdk = 34
        versionCode = 1500
        versionName = "1.5.0"
    }
    val ks = System.getenv("TRACE_KEYSTORE")
    signingConfigs {
        if (ks != null) create("release") {
            storeFile = file(ks)
            storePassword = System.getenv("TRACE_KEYSTORE_PASSWORD")
            keyAlias = System.getenv("TRACE_KEY_ALIAS")
            keyPassword = System.getenv("TRACE_KEY_PASSWORD")
        }
    }
    buildTypes {
        release {
            // R8: drops unused code (most of Compose/Material3/coroutines we never call) and shortens names;
            // the resource shrinker then drops resources nothing references. Rules: proguard-rules.pro.
            // One universal APK stays the only download: the app has no native libraries, so there is
            // nothing per-ABI to split.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (ks != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
}
dependencies {
    implementation(project(":telemetry"))
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
