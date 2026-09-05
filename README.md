# SALTY

SALTY is a marine-operations console for fishermen, researchers, and coastal
operators. It combines INCOIS ocean data, role-specific workflows, an AI marine
assistant, trip/SAR tooling, and a phone-based voice interface.

The repository is a working prototype: some screens call live services, while
many operational workflows use clearly labelled demo data or browser-local
state when their production API is unavailable.

## Product surfaces

### Web console (`ui2`)

The Next.js application starts at `/`, collects an operational role and phone
number, and opens the role-aware console at `/app`. The active role and selected
location are stored in `localStorage`.

| Role | Implemented surfaces |
| --- | --- |
| Fisherman | Dashboard, fishing zones/PFZ, trip risk and safety, vessel/trip tracking, AI agent, call-agent launch |
| Researcher | Dashboard, INCOIS forecast map, research layers, ERDDAP research console, historical charts/exports, AI research mode |
| Coastal operator | Dashboard, operations map/fleet view, alerts and disasters, SAR/lost-fisherman workflow, INCOIS forecast map, AI agent |

Shared capabilities include location and role switching, hazard alerts, AI
drawer, responsive navigation, saved PFZ zones, operator notifications, and
browser-local journey/SAR records.

Additional route: `/app/geofencing` provides a restricted-zone operator view
with zone selection, proximity-alert controls, and UI actions.

### Marine map

The map page has role-specific views:

- Fishermen see the official INCOIS Ocean State Forecast application through a
  same-origin iframe proxy.
- Researchers can switch between that official forecast and a SALTY research
  map using verified INCOIS WMS layers, map clicks for value inspection, PFZ
  overlays, location, and India boundary layers.
- Operators can switch between an operations/fleet map and the official forecast.

The official INCOIS application supplies its own Leaflet controls, forecast
animation, vectors, legends, WMS layers, and inspection tools. PFZ and
ocean-colour products are not wired as independently controlled official layers
where the required service request has not been verified.

### Voice call agent (`SaltyAI-CallAgent`)

The FastAPI service gives basic phones a regional-language voice interface via
Exotel AgentStream. It supports:

- Bidirectional 8 kHz mono PCM audio over WebSocket.
- Energy/RMS voice activity detection, silence endpointing, barge-in, and
  playback clearing.
- Sarvam Saaras speech-to-text and Sarvam Bulbul text-to-speech clients, with
  local STT/TTS implementations available for development.
- Multilingual/code-mixed language inference and DTMF language selection.
- Bounded multi-turn conversation sessions with location context.
- Layered distress detection: fast lexicon, semantic intent handling, and
  asynchronous forwarding to the main backend emergency endpoint.
- Exotel outbound-call status and call initiation endpoints.

## Architecture

```mermaid
flowchart LR
  U[Browser user] --> N[Next.js UI]
  N --> C[Browser state and demo fallbacks]
  N --> P[Next.js proxy routes]
  P --> I[INCOIS OSF / WMS / ERDDAP]
  N --> D[Root Python data API :8010]
  D --> E[ERDDAP client]
  D --> M[Risk, fishing, severe-weather and prediction modules]
  D --> O[Ollama tool agent]
  N --> V[Voice gateway :8001]
  V --> S[STT / TTS providers]
  V --> D
  V --> X[Exotel]
```

### Web-data flow

```mermaid
sequenceDiagram
  participant B as Browser
  participant UI as Next.js UI
  participant API as Next.js proxy or Python API
  participant SRC as INCOIS / ERDDAP

  B->>UI: Select role, location, map layer, query or workflow
  UI->>API: Fetch data or submit action
  API->>SRC: Server-side request with validation
  SRC-->>API: Forecast, WMS, catalogue or time-series data
  API-->>UI: Normalized response and source status
  UI-->>B: Render map, cards, charts, alerts or result
```

### Voice-call flow

```mermaid
sequenceDiagram
  participant F as Fisherman's phone
  participant E as Exotel
  participant V as FastAPI voice gateway
  participant STT as Sarvam STT/local STT
  participant AI as Root AI/data API
  participant TTS as Sarvam TTS/local TTS

  F->>E: Phone call
  E->>V: AgentStream WebSocket
  V->>V: Buffer PCM and detect speech endpoint
  V->>STT: Transcribe completed turn
  STT-->>V: Text and language
  V->>AI: Query or emergency event
  AI-->>V: Grounded response
  V->>TTS: Synthesize response
  TTS-->>V: Telephony audio
  V-->>E: PCM media frames
  E-->>F: Spoken answer
```

## Components and responsibilities

### Next.js console

- `ui2/app` contains landing, role entry, dashboard, map, weather, risk,
  fishing-zone, vessel, research, alerts, geofencing, SAR, and AI pages.
- `ui2/components` contains the application shell, role widgets, maps, charts,
  data badges, AI drawer, call launcher, and workflow views.
- `ui2/lib/marine-context.tsx` owns role, location, local notifications,
  journeys, saved zones, and backend health state.
- `ui2/lib/marine-data.ts` contains the bundled prototype dataset used by
  dashboard cards and fallback workflows.
- `ui2/lib/*-api.ts` contains browser API clients and source-aware fallback
  handling. Results identify `live` versus `demo` data.

### Next.js integration routes

- `GET /api/incois/frame/[...path]`: same-origin proxy for the official OSF
  page and relative resources; works around upstream frame restrictions.
- `GET /api/incois/osf-config`: discovers current OSF dataset filenames from
  the official page.
- `GET /api/incois/wms`: allowlisted server-side WMS proxy for verified
  `ww3/`, `currents/`, and `winds/` datasets.
- `GET /api/erddap/[...path]`: ERDDAP proxy for catalogue and data requests.

### Root Python data API (`api_server.py`)

Run on port `8010` by default. Implemented endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | API health and prototype/live mode |
| GET | `/api/predictions` | Strict real-data marine-risk and fishing-window predictions |
| GET | `/api/severe-weather` | Metadata-driven severe-weather records |
| GET | `/api/marine/point` | Point sample for the prototype marine dataset |
| GET | `/api/fishing-zones` | Fishing-zone data builder |
| POST | `/api/llm/chat` | Ollama-backed, allowlisted ERDDAP tool agent |
| POST | `/api/ai/query` | Compatibility response shape for the voice gateway/UI |

Core modules are `erddap_client.py`, `weather_forecast.py`, `risk_features.py`,
`fishing_zone.py`, `severe_weather.py`, `prediction_models.py`,
`geofencing.py`, `historical.py`, and `ollama_agent.py`.

The ERDDAP client validates metadata, variables, units, dimensions, bounding
boxes, and time coverage before querying. Most data-query paths can use an
explicitly tagged synthetic fallback for prototype operation. Predictions
deliberately disable that fallback and return `NOT AVAILABLE` when live inputs
cannot be retrieved.

### Voice gateway API

- `GET /health`, `/health/live`, `/health/ready`: service and dependency status.
- `GET /exotel/status`: safe Exotel configuration status.
- `POST /exotel/call`: start an outbound Exotel call.
- `WS /ws/exotel/stream` and `WS /ws/voice/stream`: bidirectional voice stream.

The gateway calls the root API through `app/ai/backend_client.py` and forwards
detected emergencies through `app/api/emergency.py`. It is a separate service,
not a module inside the Next.js server.

## Data and prototype boundaries

- INCOIS is the source of truth for the official OSF map and verified forecast
  layers.
- ERDDAP is used for catalogue discovery, metadata, point/region/time-series,
  historical, and forecast retrieval.
- Dashboard fixtures, local workflow estimates, and UI fallbacks are demo
  data; the UI displays source badges or explanatory text for them.
- No production fleet telemetry, Coast Guard dispatch, VHF broadcast, offline
  map cache, PFZ WebGIS service integration, or chlorophyll/Kd490 controlled
  layer is implemented in this repository.
- The custom MapLibre map remains in `ui2/components/map/ocean-map.tsx` for
  research/operations use; the official iframe remains the source of truth for
  the official forecast view.

## Run locally

```bash
./run_salty.sh
```

This starts the web console at `http://127.0.0.1:3000`, the root data API at
`http://127.0.0.1:8010`, and attempts to start the voice gateway at
`http://127.0.0.1:8001` when its dependencies are available. The voice gateway
still requires `SaltyAI-CallAgent/.env` configuration for real Exotel/Sarvam
calls.

For live root-API catalog access:

```bash
SALTY_LIVE=1 python3 api_server.py
```

For frontend checks from `ui2`:

```bash
./node_modules/.bin/tsc --noEmit
npm run lint
```

Python phase and voice-agent tests are available as `test_*.py` in the root
and under `SaltyAI-CallAgent/tests`.
