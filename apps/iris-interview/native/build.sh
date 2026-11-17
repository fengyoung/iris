#!/bin/sh
# Compile without SwiftPM dependencies or permissions; output stays under native/.build.
set -eu
NATIVE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ARCH=$(uname -m)
mkdir -p "$NATIVE_DIR/.build"
exec xcrun swiftc -swift-version 5 -parse-as-library -target "$ARCH-apple-macos15.0" "$NATIVE_DIR/Sources/IrisAudioHelper/Helper.swift" -o "$NATIVE_DIR/.build/iris-audio-helper"
