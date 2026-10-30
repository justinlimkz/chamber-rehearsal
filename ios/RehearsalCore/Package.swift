// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "RehearsalCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "RehearsalCore", targets: ["RehearsalCore"]),
        .executable(name: "fixture-check", targets: ["fixture-check"]),
        .executable(name: "import-check", targets: ["import-check"]),
    ],
    targets: [
        .target(name: "RehearsalCore"),
        .executableTarget(name: "fixture-check", dependencies: ["RehearsalCore"]),
        .executableTarget(name: "import-check", dependencies: ["RehearsalCore"]),
    ]
)
