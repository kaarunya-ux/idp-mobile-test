# R8 rules for the release build. The default Android rules come from proguard-android-optimize.txt;
# ML Kit and AndroidX ship their own consumer rules.

# Keep file names and line numbers so a release stack trace can be read with the mapping file.
-keepattributes SourceFile,LineNumberTable

# MainActivity reads the child process id from a private field of the platform's Process class
# by reflection (getPid). That class belongs to the Android runtime and is never renamed, so no
# rule is needed for it. If getPid is ever pointed at one of our own classes, keep that class here.
