# SALTY Project Handoff Context

## Current objective

SALTY is a marine operations application. The Marine Map must use the real INCOIS Ocean State Forecast application/data, not simulated values or a recreated substitute map.

The official sources are:

- OSF: https://www.incois.gov.in/oceanservices/osfforecast.jsp
- PFZ WebGIS entry page: https://www.incois.gov.in/MarineFisheries/PfzWebGis
- PFZ map application discovered from the entry page: https://www.incois.gov.in/DataInfo/MFASPFZ/index.html

## Current map implementation

The Marine Map page currently embeds the official INCOIS OSF page through a same-origin reverse proxy:

`ui2/app/app/map/page.tsx`

The iframe source is:

`/api/incois/frame/oceanservices/osfforecast.jsp`

The iframe is vertically cropped by approximately 86px so the INCOIS branding header is hidden while the official blue forecast toolbar and the actual map remain visible. SALTY’s application header/sidebar remain outside the iframe.

The map is therefore the actual INCOIS Leaflet application, including its own:

- WMS forecast layers
- Wind/current vector visualization
- Layer menu
- Forecast timestamp controls
- Play/pause animation
- Dynamic legends
- Area selection
- Coordinate display
- Click/value inspection
- Zoom and pan
- Download controls

Do not replace this with generated points, fake heatmaps, SVG map backgrounds, or synthetic wind arrows.

## Important proxy routes

### Official-page iframe proxy

File:

`ui2/app/api/incois/frame/[...path]/route.ts`

This server route fetches resources from `https://www.incois.gov.in`, removes the upstream frame restrictions by returning the response through SALTY, and forwards HTML/CSS/JavaScript/images/binary resources.

The page needs the base path `/oceanservices/` because the official OSF page references scripts such as:

- `forecast_js/wms.js`
- `forecast_js/forecastwms.js`
- `js/leaflet.js`
- `js/leaflet-velocity-test2.js`
- `js/leaflet.timedimension.js`

The proxy rewrites selected root-relative paths such as `/thredds/`, `/geoserver/`, `/json/`, `/site/`, `/portal/`, and `/assets/` back through the proxy.

If the iframe displays HTML but no map, inspect browser Network requests first. The most common issue is a relative resource resolving to the wrong `/oceanservices/` path.

### WMS proxy

File:

`ui2/app/api/incois/wms/route.ts`

This was added for the earlier custom MapLibre implementation because direct browser WMS requests failed with:

`No 'Access-Control-Allow-Origin' header is present`

The route validates datasets under `ww3/`, `currents/`, and `winds/`, then fetches the request server-side from the INCOIS WMS host. The current iframe implementation uses the official app instead, but this route should be retained unless proven unnecessary.

### OSF configuration discovery

File:

`ui2/app/api/incois/osf-config/route.ts`

This fetches the official OSF HTML and extracts the live dataset variables exposed by the page, including:

- `rsmc_combined_ww3`
- `currentsFile2`
- `mldnio`
- `sstnio`

Do not hardcode guessed dataset filenames when updating this integration. The official page changes filenames as new forecast runs are issued.

## Dataset/service details discovered from the official OSF source

The official page source exposes these WMS services:

- Wave/Wind dataset path: `/thredds/wms/osf/ww3/{rsmc_combined_ww3}`
- Current dataset path: `/thredds/wms/osf/currents/{currentsFile2}`
- MLD/D20 dataset path: `/thredds/wms/osf/winds/{mldnio}`
- SST dataset path: `/thredds/wms/osf/winds/{sstnio}`

Official WMS variables/layers:

- Wind: `UWND:VWND-mag`, style `raster/x-Occam`
- Significant wave height: `HS`, style `raster/x-Rainbow`
- Swell height: `PHS01`, style `raster/x-Rainbow`
- Wave period: `T02`, style `raster/x-Rainbow`
- Swell period: `PTP01`, style `raster/x-Rainbow`
- Surface current map: `U:V-mag`; official legend identifies it as `CURRENT`
- Mixed Layer Depth: `MLD`
- D20: `D20`
- Sea Surface Temperature: `SST`

Official vector JSON resources are relative to the OSF page’s `json/` directory. The source constructs filenames such as:

- `YYYY-MM-DD_12-00-00_wind.json`
- `YYYY-MM-DD_HH-MM-SS_current.json`
- `YYYY-MM-DD_HH-MM-SS_swh.json`
- `YYYY-MM-DD_HH-MM-SS_swell.json`

The official page uses Leaflet Velocity for these vectors.

Official basemap and boundary resources:

- Satellite: `https://basemap.nationalmap.gov/ArcGIS/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}`
- India boundary WMS: `/geoserver/BaseMaps-Common/wms`
- Boundary layer: `BaseMaps-Common:gdam_410_l0_india_corrected`

## Why direct iframe embedding was not used

The official OSF response includes `X-Frame-Options: SAMEORIGIN`, so a direct iframe from `localhost:3000` is blocked. The current solution proxies the official page through SALTY, making the iframe same-origin while retaining the official page and its functionality.

## Current custom map file

`ui2/components/marine-map.tsx` contains an earlier direct-WMS MapLibre implementation. The page currently uses the official iframe instead. Treat the iframe as the source of truth for the Marine Map. If the custom component is reactivated, it must use verified INCOIS configuration and must not reintroduce fake fallback data.

The old synthetic data file is:

`ui2/lib/frontend-map-data.ts`

It was used during earlier prototype work and must not be used for INCOIS forecast rendering.

## SALTY shell/navigation

The application shell is:

`ui2/components/app-shell.tsx`

The map page intentionally remains inside this shell. Do not make the map fixed to the viewport in a way that covers or removes the SALTY sidebar/header.

The map page uses a relative full-height content region. The iframe is cropped within that region, not across the whole application viewport.

## Running the project

From `/home/pavan/salty`:

```bash
./run_salty.sh
```

The script starts the SALTY UI on `http://127.0.0.1:3000` and the data API on `http://127.0.0.1:8010`.

For the official iframe, the machine running the SALTY server needs outbound access to `www.incois.gov.in`.

## Validation

From `ui2/`:

```bash
./node_modules/.bin/tsc --noEmit
npm run lint
```

TypeScript passed after the current changes. ESLint has existing warnings elsewhere in the project but no errors.

## Known limitations / next checks

1. Verify the proxied iframe in the browser Network panel. Confirm that Leaflet JavaScript, `forecast_js/forecastwms.js`, `js/leaflet-velocity-test2.js`, WMS image requests, and `json/*_wind.json` return successful responses.
2. Confirm the iframe crop remains correct at desktop and mobile widths. The current crop is 86px based on the official desktop header height.
3. PFZ WebGIS has been identified, but its underlying service requests still need to be captured from the browser Network panel before adding PFZ as a SALTY map layer. Do not guess PFZ WMS URLs.
4. Chlorophyll appears in the OSF/PFZ interfaces, but its exact active request must be captured and verified before exposing it as a SALTY-controlled layer.
5. If server-side proxy requests fail, check DNS/network access from the machine running Next.js. Browser CORS settings do not affect server-side fetches, but upstream availability still does.

## Handoff rule

Always inspect the official INCOIS page/source/network requests first. Record the exact URL, protocol, dataset/layer, variables, required parameters, and HTTP result before integrating a new service. Never silently fall back to generated marine values.

## Backend prediction handoff

The Python backend no longer owns map layers. `map_layers.py`, `/api/map/layers`, and the frontend context’s map-layer fetch were removed. The Marine Map is served by the official INCOIS iframe/proxy in the Next.js frontend.

Backend prediction entry point:

- `prediction_models.py` — transparent marine-risk and fishing-window models using retrieved forecast records only.
- `GET /api/predictions` — strict prediction endpoint; it uses `synthetic_fallback=False` and returns `NOT AVAILABLE` when real source data cannot be retrieved.
