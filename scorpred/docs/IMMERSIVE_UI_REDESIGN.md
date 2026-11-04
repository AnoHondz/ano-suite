# Immersive ScorPred interface — 2026-10-22

ScorPred now uses one dark graphite/glass design across Dashboard, NFL, NBA, Soccer, Match Analysis, and Insights, with sport-specific interactive fields and courts. The local app is running at <http://127.0.0.1:5173>. Existing navigation, APIs, real matchup selection, research restrictions, and prediction calculations are preserved.

## Actual reusable components

| Component | Implementation and interaction |
| --- | --- |
| Football field | Three.js geometry: stadium platform, yard lines, hash marks, goalposts, floating football and laces. Pointer parallax, ball hover/selection, Home/Away perspective controls. |
| Basketball court | Three.js wood-colored court, keys/arcs, boards, hoops, floating basketball and seams. Same interaction conventions. |
| Soccer pitch | Three.js striped pitch, boxes, center markings, goals, floating ball and geometric panels. Same interaction conventions. |
| `SportsArena` / `ArenaScene` | Shared lazy scene loader, lifecycle, preference controls, WebGL error boundary, and fallback. One arena/context per visible route. |
| `MatchupStage` | Actual teams/logos/tipoff on opposite sides of the venue, existing estimate or explicit unavailable state, source receipt, existing analysis navigation. |
| `DepthSurface`, sport portals and matchup cards | Restrained pointer tilt and hover depth; sport portals navigate to the existing sport routes. Tilt is disabled for coarse pointers and reduced motion. |
| `AnimatedIndicator`, `CoverageVisual` | Animated bars for actual API counts, observed scoring comparisons, or existing eligible estimates. No invented percentages or confidence values. |

Three.js and React Three Fiber were added to the existing React 19/Vite application. React Three Fiber's documented React 19 pairing is version 9: [installation reference](https://r3f.docs.pmnd.rs/getting-started/installation). No frontend rewrite or alternative backend was introduced. Design tokens and responsive rules live in `frontend/src/immersive.css`; the reusable scene and presentation components live in `frontend/src/components/immersive/`.

The Dashboard has sport navigation, a real featured matchup, existing decision counts, and an available-slate visualization. Each sport has its venue and unchanged slate controls. Match Analysis keeps the original scoring/research sections and adds the arena and NBA descriptive history; the decision explanation expands/collapses. Insights uses the same venue and information hierarchy while retaining its existing data and controls. Route changes, loading states, card hover, and comparison bars animate without delaying navigation.

The venues are stylized illustrations. They are explicitly labeled as illustrations rather than live player tracking. No example probabilities, live statuses, weather, or reference-image statistics were copied into the app. NFL scenario shares remain labeled experimental/descriptive; NBA unavailable estimates remain unavailable.

## Performance controls

- Lazy import the scene only after a near-visible arena has remained eligible for 900 ms; reserve layout space and render a lightweight SVG/CSS venue immediately.
- Cap device pixel ratio at 1 on narrow/mobile layouts and at 1.5 on larger screens. Use small 512-pixel shadow maps.
- Use demand rendering with an approximately 30 Hz idle invalidation target. Stop animation when the arena is offscreen or the document is hidden. Avoid React state updates for each pointer/frame movement.
- Use one canvas per route. Dispose the prior route normally; do not create extra WebGL capability-probe contexts.
- Honor `prefers-reduced-motion` with no WebGL canvas and no decorative transitions. Offer a persistent Lite preference, and `?graphics=lite`, plus a lightweight fallback after WebGL creation failure.
- Keep mobile scrolling available over the arena. All data actions remain ordinary accessible buttons and links.

Demand rendering follows the library's [performance guidance](https://r3f.docs.pmnd.rs/advanced/scaling-performance).

## Before/after measurements

The same unmocked Edge journey script visited the existing routes at 1440×1000 and 390×844 before and after the changes. It waits for content and existing controls, rather than waiting for optional 3D. Both runs used the local Vite server and real providers. Cache/provider state and machine load differed; these are observations, not a controlled claim that the design accelerates upstream services.

| Desktop content readiness | Before | After |
| --- | --- | --- |
| Dashboard, first visit | 10,025 ms | 6,351 ms |
| NFL | 502 ms | 468 ms |
| NBA | 446 ms | 417 ms |
| Soccer | 961 ms | 474 ms |
| NBA analysis | 355 ms | 248 ms |
| Soccer analysis | 4,043 ms | 3,542 ms |
| Insights | 663 ms | 631 ms |

On the 390-pixel journey, Dashboard was 683 → 677 ms; NBA 264 → 427 ms; NBA analysis 361 → 228 ms. Mobile NBA was slower in this observation, and the data do not support a universal speedup claim.

A separate real-WebGL check at 1600×1000 measured page shells at 236–277 ms after the first Dashboard load, content at 592–949 ms, and completed scene initialization at 2.46–3.34 seconds. The first Dashboard shell appeared in 1.36 seconds, while full provider-dependent content took 11.02 seconds. Other runs also showed multi-second cold provider waits. The interface remains available while those requests settle; this external-data latency is still a limitation.

Initial static JavaScript graph: **222,406 → 232,551 bytes (+4.6%)**. Local gzip measurements: **71,087 → 72,808 bytes**. The deferred Three.js scene chunk is 923,536 bytes (241,315 gzip bytes using Python gzip); Vite reports a different compressed estimate and warns about this deferred chunk's size. It is excluded from the initial static graph and is never requested in reduced-motion/Lite mode. Current compiled CSS is 76,477 bytes. Fonts and route-specific chunks are additional resources, not included in that initial-JavaScript metric.

The two-second SwiftShader check observed 78 browser animation callbacks, mean frame interval 25.7 ms and 95th percentile 33.4 ms. This measures browser UI frame cadence under software rendering, not physical GPU render FPS. The scene's own idle render target is 30 Hz. Real phone GPU/battery behavior has not been measured.

Raw runs, metric definitions, and bundle sizes: [performance evidence](assets/immersive_ui/performance.json).

## Browser verification and screenshots

Real API/WebGL checks passed on all six redesigned desktop pages with one canvas each, no horizontal overflow, no page exceptions, and no error alerts. The browser exercised pointer movement and Home/Away controls. Touch emulation at 390×844 with device scale factor 2 rendered at the capped DPR of 1; Dashboard and all three sports fit without overflow. Offscreen animation pause, reduced-motion zero-canvas behavior, and a controlled WebGL-unavailable fallback passed.

- [Dashboard](assets/immersive_ui/dashboard.png)
- [NFL](assets/immersive_ui/nfl.png)
- [NBA](assets/immersive_ui/nba.png)
- [Soccer](assets/immersive_ui/soccer.png)
- [Match Analysis](assets/immersive_ui/match-analysis.png)
- [Insights](assets/immersive_ui/insights.png)
- [NBA mobile](assets/immersive_ui/nba-mobile.png)
- [NBA reduced motion](assets/immersive_ui/nba-reduced-motion.png)

These are screenshots of the running application with its actual data, not mockups. [WebGL verification](assets/immersive_ui/webgl-browser.json) and [22-step navigation verification](assets/immersive_ui/navigation-browser.json) record the checks.

## Tests, preservation, and limitations

- Backend: **832 passed**, including NBA/NFL/soccer regressions.
- Frontend: **38 passed**, including isolated 320/390/768/1440-pixel route checks, NBA descriptive/outage/archive cases, no-fallback behavior, and Lite/reduced-motion contracts. Routine data tests use Lite mode to avoid competing software-rendered contexts; real WebGL has its own browser run.
- ESLint, TypeScript, and production build: passed. Dependency audit: **0 reported vulnerabilities**. The large deferred chunk warning remains visible.
- All **160 protected files** hash-identical; six fixed-input soccer prediction outputs unchanged. Backend loaded-code fingerprint matches the workspace; research remains disabled. No deployment or model retraining.

Remaining limitations: provider-dependent cold content latency; substantial deferred 3D download; no physical-device GPU/battery benchmark; stylized geometry rather than photorealistic stadium assets. The current R3F/Three pairing also emits nonblocking upstream legacy-clock/shadow deprecation warnings. Browser tests found no page exceptions. NBA early-season predictions remain intentionally unavailable where the existing sample requirements are unmet; the separate descriptive comparisons make those pages useful.

To restart or reuse the local services, run `powershell -ExecutionPolicy Bypass -File .\Start-ScorPred.ps1` from the project directory. The launcher checks ownership and source fingerprints, binds to loopback, and avoids duplicating existing services. The next UI performance step is a production-preview benchmark on an actual mobile GPU, focusing on cold deferred-scene transfer and provider latency before considering visual expansion.
