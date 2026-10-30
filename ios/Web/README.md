# Verovio for the iPad app

`verovio-toolkit-wasm.js` is Verovio 6.3.0 (the same version as the Mac's Python package), unmodified, from the
npm package `verovio@6.3.0` (`dist/verovio-toolkit-wasm.js`, the WebAssembly toolkit with its fonts built in).
The app's score page (`score.html`) loads it to engrave the MusicXML on the iPad.

Licence: LGPL-3.0-or-later (https://github.com/rism-digital/verovio). It is loaded as a separate, replaceable
file, not compiled into the app.
