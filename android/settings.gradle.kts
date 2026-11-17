pluginManagement {
    repositories {
        // Prefer Maven Central when dl.google.com is flaky
        mavenCentral()
        google()
        maven { url = uri("https://maven.google.com") }
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        mavenCentral()
        google()
        maven { url = uri("https://maven.google.com") }
    }
}

rootProject.name = "QuadrantParking"
include(":app")
