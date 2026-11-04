# Preserved play illustration implementation

The active presentation now uses a compact lite field with one matchup heading and the existing API prediction centered inside it. Player props and the prediction explanation follow directly below. NBA comparisons use a compact table; recent results, source details, injuries and detailed scoring remain expandable. A saved 3D preference does not load WebGL or restore the previous oversized scene.

Win probability, rule confidence and NFL experimental scenario share remain distinct. Missing predictions stay explicitly unavailable. Kickoff and source-expiry safeguards still suppress expired predictions.

The earlier `SportsArena`, `ArenaScene`, `TacticalPlayers` and `arenaPlay` components remain in the repository but are not imported by active views. The remaining illustration notes below describe that preserved implementation.

The API supplies matchup names, logos, schedules and existing prediction displays. The animated formations are generic coaching examples, with their own visible label. They are not confirmed lineups, live tracking, recorded plays or outputs of a prediction model.

## Sport behavior

| Sport | Players | Illustrated action |
| --- | --- | --- |
| NFL | 11 against 11 | Seven offensive players on the initial line, quarterback behind the center, receivers running routes, defensive coverage, snap and forward pass. |
| NBA | 5 against 5 | Half-court spacing, off-ball cuts, defender adjustments, dribbling and two passes. |
| Soccer | 11 against 11 | Example 4–3–3 with goalkeepers, defensive/midfield/forward lines, a grounded buildup pass and a pass to the wing. |

Player movement and ball possession share a deterministic timeline in `frontend/src/components/immersive/arenaPlay.ts`. Players reset during the fade between example plays. No winner, score, expected lineup or actual tactical formation is inferred from these movements.

## Controls and performance

- Pause/resume and replay control the example play. Home/away view controls retain their existing perspective behavior.
- Animations stop advancing offscreen and in a hidden document. Reduced motion uses a static formation.
- Lite mode includes the same two formations without loading the Three.js scene.
- Player bodies, heads, arms and legs use four instanced meshes for both teams. The existing single WebGL context, 30 Hz invalidation and device pixel ratio limits remain in place.
- The large decorative floating ball has been replaced by a small ball that follows possession and passes.

## Verification of the current layout

`frontend/e2e/prediction-layout.spec.ts` checks the single matchup heading, actual API estimate, probability labels, compact field, absent WebGL and 1440/390/320 pixel layouts. The remaining frontend suite covers navigation, expiry, reduced motion, NBA comparisons, props and scoring. The arena tests retain checks for the preserved illustration timeline as well as the current lite presentation.

`frontend/scripts/verify-arena-framing.mjs` exercises real local API matchup labels with software WebGL, compares paused and moving frames, and checks replay, perspective controls, lite mode, reduced motion and offscreen suspension at 1440, 390 and 320 pixel widths. Image comparisons tolerate only small raster quantization differences.

Run the normal frontend lint, production build and Playwright suite. For current live browser evidence, run `node scripts/verify-prediction-layout.mjs ../.tmp/prediction-layout` from `frontend` while the local app is running. It verifies actual NBA, NFL and soccer matchups and writes screenshots plus a machine-readable report. The arena framing script described above is retained for the earlier illustration implementation.

The layout follows [W3C heading guidance](https://www.w3.org/WAI/tutorials/page-structure/headings/) and uses [progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) for supporting detail.

Backend prediction calculations, model artifacts and sports data stores are not part of this feature.
