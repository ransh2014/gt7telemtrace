plugins { id("org.jetbrains.kotlin.jvm") }
kotlin { jvmToolchain(17) }
dependencies {
    compileOnly("org.json:json:20240303")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
