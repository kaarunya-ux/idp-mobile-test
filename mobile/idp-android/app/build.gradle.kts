plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jlleitschuh.gradle.ktlint")
    id("io.gitlab.arturbosch.detekt")
}

// Lint and format gates (ADR 0003). ktlint checks every Kotlin file except MainActivity.kt (switched
// off in .editorconfig), which joins as it is split up. detekt checks the same files plus the tests.
ktlint {
    version.set("1.3.1")
    android.set(true)
}

detekt {
    config.setFrom(file("$rootDir/config/detekt/detekt.yml"))
    buildUponDefaultConfig = true
    source.setFrom(
        fileTree("src/main/java") { exclude("**/MainActivity.kt") },
        "src/test/java"
    )
}

android {
    namespace = "com.modeltesting.vlmtester"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.modeltesting.vlmtester"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        debug {
            // JaCoCo coverage of the unit tests: ./gradlew :app:createDebugUnitTestCoverageReport (ADR 0006).
            enableUnitTestCoverage = true
        }
        release {
            // R8 shrinks and obfuscates the app's own code (rules in proguard-rules.pro). Check a release
            // build on a phone before shipping it; unsigned until a signing config is agreed.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    // Native binaries are shipped as renamed .so files under jniLibs so the
    // installer extracts them into the app's executable nativeLibraryDir
    // (a plain app-writable directory cannot exec() binaries on Android 10+).
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }

    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    // On-device OCR (bundled Latin model, no Play Services download). Used by the OCR-first path.
    implementation("com.google.mlkit:text-recognition:16.0.1")

    // JVM unit tests (app/src/test). org.json is listed because the stub copy inside
    // android.jar throws "not mocked" when called from a unit test.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
