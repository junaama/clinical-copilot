# oe-module-copilot-launcher

Tiny OpenEMR companion module for the **Clinical Co-Pilot**. Three jobs:

1. Register the Co-Pilot as a SMART on FHIR client in `oauth_clients`.
2. Inject an "Open Co-Pilot" button + docked iframe into the patient chart,
   and bridge cross-frame `copilot:flash-card` events into a flash outline on
   the matching chart card.
3. Create the `agent_audit` table and expose a tiny POST endpoint the iframe
   uses to record per-turn agent decisions.

Per `ARCHITECTURE.md` §4 and §6 the module is intentionally thin: it does not
host any data API, does not modify upstream OpenEMR files, and does not
duplicate the existing SMART OAuth2 machinery (it delegates to
`SmartLaunchController`).

## Install

```bash
cd interface/modules/custom_modules/oe-module-copilot-launcher
composer install --no-dev
```

Then in OpenEMR:

1. Admin → System → Modules → Manage Modules. The "Co-Pilot Launcher"
   row should be in the *Unregistered* list. Click **Register** then
   **Install** then **Enable**.
2. Installation runs `sql/install.sql` — creates `agent_audit` and inserts
   a stub row in `oauth_clients` with `client_id = 'copilot-launcher'`.
3. On first request, `Bootstrap.php` calls
   `CopilotClientRegistration::ensureRegistered()`, which generates a fresh
   client secret, persists it on the row, and mirrors it into
   `globals.copilot_oauth_client_secret` so the agent backend can read it.
4. Admin → Globals → Connectors → set **`copilot_app_url`** to the absolute
   base URL of the Co-Pilot frontend (e.g. `http://localhost:5173` for dev).

Verify:

* Admin → System → API Clients should show **Clinical Co-Pilot**, enabled.
* `SHOW TABLES LIKE 'agent_audit';` returns one row.
* Open any patient chart. The "Open Co-Pilot" button appears at the top of
  the demographics column. Clicking docks the Co-Pilot iframe on the right
  edge of the chart.

## Cross-frame contract

The iframe posts:

```js
window.parent.postMessage(
    { type: 'copilot:flash-card', card: 'vitals', fhir_ref: 'Observation/123' },
    OPENEMR_ORIGIN
);
```

The bridge listener in `ChartSidebarListener` verifies `event.origin` against
`copilot_app_url` (no wildcard), and on match scrolls to and flashes the
chart card whose container carries `data-card="<vocab>"`.

The vocabulary is closed: `vitals | labs | medications | problems |
allergies | prescriptions | encounters | documents | other`. See
`agentforge-docs/CHAT-API-CONTRACT.md`.

### `data-card` heuristic

OpenEMR's stock chart cards do **not** carry stable `data-card` attributes
today. The bridge JS assigns them on `DOMContentLoaded` by matching section
headings to the vocab via a small `HEADING_TO_CARD` map. If upstream changes
the section heading text, update the map in
`src/Listeners/ChartSidebarListener.php`. The rest of the bridge is
data-driven by the attribute, so the map is the only update site.

## Audit endpoint

`POST /interface/modules/custom_modules/oe-module-copilot-launcher/public/audit.php`

* Auth: OpenEMR session cookie (same-origin from the iframe parent).
* CSRF: `X-CSRF-Token` header, validated via `CsrfUtils::verifyCsrfToken`.
* Body: JSON, see `Controller\AuditApiController::parseBody`.
* Response: `204 No Content` on success.

## Uninstall

`sql/uninstall.sql` drops `agent_audit`. The `oauth_clients` row is left in
place so re-installs do not regenerate keys; admins can disable / delete it
explicitly via Admin → System → API Clients.
