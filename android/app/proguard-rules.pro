# R8 rules for the TRACE release build (see app/build.gradle.kts).
#
# What needs no rule: TRACE uses no reflection, no serialization library, no JNI and no WebView JS bridge.
# MainActivity is kept through the manifest; Compose, AndroidX and coroutines ship their own consumer rules;
# org.json and java.* come from the Android framework, which R8 never touches.

# Readable crash traces: keep file names and line numbers. app/build/outputs/mapping/release/mapping.txt maps the
# shortened class names back (the Android workflow uploads it next to the APK).
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# The shared telemetry module decodes GT7 packets and holds the golden-tested analysis code. Keep its class and
# member names so log lines and crash traces from it still name the real class (it is small; R8 still removes
# whatever is unused).
-keepnames class app.trace.telemetry.** { *; }
