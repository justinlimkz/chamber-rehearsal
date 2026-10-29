#!/bin/zsh
# Builds build/tone/bin/render from the engine (ios/RehearsalCore) and the app's Strings.swift, unmodified.
set -e
root=${0:A:h:h:h}
out=$root/build/tone/bin
mkdir -p $out
cd $out
swiftc -O -parse-as-library -emit-library -static -emit-module -module-name RehearsalCore \
    $root/ios/RehearsalCore/Sources/RehearsalCore/*.swift -o libRehearsalCore.a 2>&1 | grep -v -i warning || true
swiftc -O -I $out -L $out -lRehearsalCore -o render \
    $root/tone/render/main.swift $root/ios/App/Rehearsal/Strings.swift
echo $out/render
