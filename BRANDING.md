# Takumi identity

The original indigo wave and muted sakura-petal mark on ivory is the Takumi master identity. It was supplied by the project owner. Its proportions, orientation, arrangement, and colors must not be modified when the application is reskinned. The transparent small-size derivative in `web/mark.png` is used only for UI placement; `assets/takumi-master.jpg` preserves the supplied artwork.

The MIT license covers the Takumi application source. Branding artwork is provided by its owner for this application and is **not** automatically granted under the MIT source license. Redistributors must have the appropriate permission for brand artwork.

The reference screenshots supplied by the owner define the layout: warm ivory workspace, stone sidebar, restrained muted rose interaction accent, indigo wordmark, compact navigation, readable prose, and a clean model/composer. Other themes are opt-in and must not change the logo itself.

## Transparent UI derivative

`web/mark-clean.png` is the 2026-11-02 edge-cleanup derivative used by the native titlebar and web UI. The owner requested removal of the pale fringe. It was produced with the built-in image-editing tool from the existing `web/mark.png`, preserving the intended wave/petal identity; both the original derivative and `assets/takumi-master.jpg` remain unchanged. The selected edit requested background extraction, preserved silhouette, arrangement and colors, a tight crop, removal of the pale edge matte, foreground-color antialiasing and true transparency. Dark themes place it on an ivory circle for contrast. This does not grant new artwork redistribution rights.

## Kage companions

`web/kage/akane.png` is the additional crimson ninja requested on 2026-11-02, generated using the existing `ren.png` as the style and pose reference. Its transparent background is part of the original generated PNG. Ren, Kiri, Hana and Tetsu remain available; this asset does not replace the Takumi master mark or silently change a saved companion choice. The same artwork distribution restrictions above apply; adding an asset is not a new grant of MIT rights to the existing character designs.

## Model identity

**Sumi 1.0** is the user-facing name of Takumi's locally configured model profile. Its original upstream attribution and model source are retained in `THIRD_PARTY.md` and `models/sumi-1.0.json`. Major/minor version increases require measured software or model improvements; an alias alone does not imply new foundation-model training.

## Lightweight model identity

**Rei 1.0** is the distinct lightweight Takumi model profile for 8 GB GPU systems. It runs on separately installed Bonsai 2 9B weights. This is a versioned inference configuration with emulator/decomp/web-engineering instructions and retrieval tools, **not newly trained neural weights**. Sumi 1.0 remains the default full-size profile when hardware supports it. Existing `sumi-1.0-lite` user models are recognized as legacy profiles rather than automatically deleted or renamed.
