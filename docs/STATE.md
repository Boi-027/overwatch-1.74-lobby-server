# Short handoff

- Purchases use the catalog price and the correct wallet: credits, OWL tokens or competitive points. A full24300 refresh sends all balances.
- All11 existing box types are available. Three Anniversary Remix scenes work after loading the missing skin themes. Unsupported scene options are hidden; seasonal boxes remain.
- Weekly Tracer/Symmetra bindings are implemented in content helpers, but automatic selection and complete native banner/popup integration are unfinished. A standalone38901 notification did not show the requested popup.
- PracticeRange sends24000 (kind2, empty name, flags4); normal search sends44100 and cancellation44102. Workers are created/cancelled accordingly.
- A targeted53000 state4 probe produces52903 acknowledgment but leaves a search lock; state0 restores idle. The probe resets by default. A candidate20600 handoff produced no game UDP traffic.
- Competitive season metadata is unresolved. Do not present the server as gameplay-complete.

The runtime fixtures retain decoded server templates from the public reference capture. Raw capture streams and client traffic were replaced by a JSON template set and synthetic smoke-client messages; the retained display-name marker is anonymized. Binary protocol schemas in `server/bnet/descriptors.pb` are required data, not an executable.

Packaging-only changes: portable relay logging beside the DLL, relative game discovery plus --game-exe override, fresh default player name, and JSON-backed reference templates. The development checkout is unchanged.

Packaging verification: 82 Python tests and 2 JavaScript tests passed; synthetic login/actions decoded 58 + 12 messages with zero errors, without raw capture files. The relocated relay source compiled with MSVC x64. The real game was not launched during packaging.
