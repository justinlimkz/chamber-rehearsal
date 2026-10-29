#!/bin/zsh
# Builds build/tone/bin/compare-events against the engine library that tone/render/build.sh makes.
set -e
root=${0:A:h:h:h}
out=$root/build/tone/bin
[[ -f $out/libRehearsalCore.a ]] || $root/tone/render/build.sh
swiftc -O -I $out -L $out -lRehearsalCore -o $out/compare-events $root/tone/compare/events.swift
echo $out/compare-events
