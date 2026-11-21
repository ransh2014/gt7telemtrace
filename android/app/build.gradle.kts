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
        versionCode = 1600
        versionName = "1.6.0"
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
            // One universal APK per edition: Lite has no native libraries, and AI carries only arm64-v8a.
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

    // Two builds from one codebase. TRACE Lite is what shipped before (applicationId unchanged, "TRACE").
    // TRACE AI adds the local AI wording: it installs next to Lite (applicationId ".ai"), is "TRACE AI", ships only
    // arm64-v8a, and bundles the model (src/ai/assets) and a llama-server built for arm64 (src/ai/jniLibs). CI fills
    // those two folders (tools/fetch_ai_assets.py and the llama.cpp step in .github/workflows/build-android.yml).
    flavorDimensions += "edition"
    productFlavors {
        create("lite") { dimension = "edition" }
        create("ai") {
            dimension = "edition"
            applicationIdSuffix = ".ai"
            ndk { abiFilters += "arm64-v8a" }
        }
    }
    // the model is a 0.8 GB file the server memory-maps after a straight copy: never compress it
    androidResources { noCompress += "gguf" }
    // libllama_server.so is an executable run from the app's native library folder, so it must be extracted there
    packaging { jniLibs { useLegacyPackaging = true } }
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
