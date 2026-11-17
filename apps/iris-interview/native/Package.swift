// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "IrisAudioHelper", platforms: [.macOS("15.0")], products: [.executable(name: "iris-audio-helper", targets: ["IrisAudioHelper"])], targets: [.executableTarget(name: "IrisAudioHelper", swiftSettings: [.unsafeFlags(["-swift-version", "5"])])])
