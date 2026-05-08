# Deployment

Public URL: **https://openemr-production-c5b4.up.railway.app**
Hosting: Railway (Hobby plan)
Project: `openemragent` (id `ba3eb824-ff41-4676-9373-f45c218a80d0`)
Region: `us-west2`

---

## Why Railway

Chosen for the AgentForge MVP because it:

- runs unmodified Docker images directly (no Dockerfile required for the MVP)
- supports multi-service projects with private networking, so OpenEMR + the future agent + MariaDB share one project
- gives a public HTTPS URL on `*.up.railway.app` with no DNS work
- costs ~$15–25/mo for this workload on the Hobby plan ($5 base + usage), well inside the $100 budget
- fast iteration: push-to-deploy, env vars in dashboard, CLI parity with the UI

Tradeoffs accepted for the MVP and called out in `agentforge-docs/AUDIT.md` later:

- **No HIPAA BAA from Railway.** Acceptable here because this project uses synthetic data only. The architecture (externalized DB, externalized session/file storage when added, no PaaS-specific lock-in) is being kept portable so a real-PHI deployment can move to AWS or DigitalOcean Business tier without code changes.
- Single region (us-west2). Fine for a demo, not for a production clinical product.
- Railway-managed MariaDB does not have read replicas or PITR. Daily snapshots only.

---

## Architecture

```
Railway project: openemragent
│
├── Service: mariadb
│   ├── Image: mariadb:11.8.6
│   ├── Volume: mariadb-volume → /var/lib/mysql
│   ├── No public domain (private network only)
│   └── Env: MYSQL_ROOT_PASSWORD = <generated, 32 hex>
│
└── Service: openemr
    ├── Image: built locally from docker/openemr-railway/Dockerfile
    │           (FROM openemr/openemr:latest + local fork overlay)
    ├── Volume: openemr-volume → /var/www/localhost/htdocs/openemr/sites
    ├── Public domain: https://openemr-production-c5b4.up.railway.app (port 80)
    └── Env:
        ├── MYSQL_HOST       = ${{mariadb.RAILWAY_PRIVATE_DOMAIN}}
        ├── MYSQL_ROOT_PASS  = ${{mariadb.MYSQL_ROOT_PASSWORD}}
        ├── MYSQL_USER       = openemr
        ├── MYSQL_PASS       = <generated, 32 hex>
        ├── OE_USER          = admin
        └── OE_PASS          = <generated, 32 hex>
```

OpenEMR's image entrypoint runs the schema installer on first boot, creates the `openemr` MySQL user with `MYSQL_PASS`, and writes credentials to `sites/default/sqlconf.php` inside the volume. After that, the env vars are NOT re-read.

### Shipping the forked tree

`docker/openemr-railway/Dockerfile` builds **`FROM openemr/openemr:latest`** and then overlays four trees from the local repo on top of the upstream image's copies:

| Tree | Why it ships |
|---|---|
| `src/` | ~766 files diverge from upstream (modern PSR-4 namespace; security and modernization patches throughout) |
| `library/` | `Document.class.php` and friends carry local extensions like the `$eid` parameter on `createDocument` |
| `apis/` | Local route definitions read query params (e.g. `?eid=`) the upstream routes don't |
| `interface/` | Co-evolved with `src/` — e.g. local `SessionWrapperFactory` exposes `getActiveSession()` while upstream exposes `getWrapper()`. Shipping `src/` alone left upstream `globals.php` calling a method the local class doesn't have. |

Staging is mechanical — `scripts/deploy-openemr.sh` does `cp -a $REPO_ROOT/{src,library,apis,interface} docker/openemr-railway/patches/` before each `railway up`. The Dockerfile then `COPY patches/ /var/www/localhost/htdocs/openemr/`, replacing the upstream copies path-for-path.

**Trees not shipped:** `templates/`, `public/`, `modules/`, `sql/`, and root-level dirs come straight from `openemr/openemr:latest`. If a fork divergence surfaces in any of those, add a `cp -a` line in `deploy-openemr.sh`.

**Tradeoff:** any upstream security patch landing in `src/`, `library/`, `apis/`, or `interface/` after our fork point is masked by the local version. Plan for a periodic rebase against `openemr/openemr:latest` to pull those forward.

**Why not hand-list patched files?** It was tried and produced silent caller/callee signature mismatches every deploy — local controller calling a 4-arg local service via an upstream router that called the local controller with 3 args, etc. Whole-tree shipping eliminates the class.

## Login

- URL: https://openemr-production-c5b4.up.railway.app
- Username: `admin`
- Password: retrieve via CLI (see "Retrieving credentials" below)

## Retrieving credentials

Source of truth is Railway. No secrets are committed to git.

```bash
# OE admin password
railway variables --service openemr | grep OE_PASS

# DB user password
railway variables --service openemr | grep MYSQL_PASS

# MariaDB root password (only needed for direct DB shell)
railway variables --service mariadb | grep MYSQL_ROOT_PASSWORD
```

Or in the Railway dashboard: project `openemragent` → service → Variables tab.

## How this was built (for reproducibility)

Prereqs: `railway login` with the Gauntlet account.

```bash
# 1. Project
railway init --name openemragent

# 2. MariaDB
railway add --image mariadb:11.8.6 --service mariadb \
  --variables "MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16)"
railway service mariadb
railway volume add --mount-path /var/lib/mysql

# 3. OpenEMR
railway add --image openemr/openemr:latest --service openemr \
  --variables 'MYSQL_HOST=${{mariadb.RAILWAY_PRIVATE_DOMAIN}}' \
  --variables 'MYSQL_ROOT_PASS=${{mariadb.MYSQL_ROOT_PASSWORD}}' \
  --variables 'MYSQL_USER=openemr' \
  --variables "MYSQL_PASS=$(openssl rand -hex 16)" \
  --variables 'OE_USER=admin' \
  --variables "OE_PASS=$(openssl rand -hex 16)"
railway service openemr
railway volume add --mount-path /var/www/localhost/htdocs/openemr/sites
railway domain --service openemr --port 80    # NOTE: --port currently no-ops, see gotcha #3 below

# Force the target port via GraphQL (CLI bug workaround)
TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['token'])")
# (Look up the IDs as shown in gotcha #3, then fire the serviceDomainUpdate mutation with targetPort: 80.)
```

Reference variables (`${{mariadb.RAILWAY_PRIVATE_DOMAIN}}`) are resolved by Railway at runtime; OpenEMR sees the actual hostname in its environment.

## Operational runbook

| Action | Command |
|---|---|
| View logs | `railway logs --service openemr` (Ctrl-C to stop streaming) |
| Open dashboard | `railway open` |
| Inspect env | `railway variables --service openemr` |
| Redeploy without changes | `railway redeploy --service openemr` |
| Roll back deployment | `railway down` (removes most recent deployment) |
| Connect to MySQL shell | `railway connect mariadb` (uses root creds automatically) |
| Service stats / status | `railway status --json` |

## Post-redeploy admin checklist

Run this whenever the OpenEMR volume is fresh — first deploy, volume wipe, or any time `OE_PASS` is rotated and the installer reruns. None of these survive a wiped volume; all of them must succeed before the agent can authenticate against FHIR.

The web steps live under **Admin → Globals → Connectors** unless noted.

### 1. Enable the REST + FHIR APIs

Admin → Globals → Connectors:

- [x] **Enable OpenEMR Standard REST API**
- [x] **Enable OpenEMR FHIR REST API**
- [x] **Enable OAuth2 Password Grant** (only if a service-account / system-scope token is wanted; not required for the standalone-launch flow the agent uses)

Save. Without these flags the FHIR endpoints return `"API is disabled"` 404s.

### 2. Set the OAuth issuer URL (`site_addr_oath`)

Admin → Globals → Connectors → **Site Address (`site_addr_oath`)**:

```
https://openemr-production-c5b4.up.railway.app
```

Must be `https://` (not `http://`) and must match the public Railway domain exactly. If wrong, the OAuth `aud` check fails with *"Aud parameter did not match authorized server"* on the authorize redirect, and the browser blocks the callback as mixed content.

### 3. Register the agent's standalone OAuth client

From the repo root:

```bash
cd agent
uv run python -m scripts.seed.bootstrap_standalone_oauth \
  --base-url   https://openemr-production-c5b4.up.railway.app \
  --agent-url  https://copilot-agent-production-3776.up.railway.app
```

Prints the new `client_id` + `client_secret` to stdout. The script registers a confidential authcode + PKCE client at `/oauth2/default/registration` with `token_endpoint_auth_method=client_secret_post`. See `agent/scripts/seed/bootstrap_standalone_oauth.py` for the exact scope set.

> **Scope drift check.** The bootstrap script's `REQUESTED_SCOPES` and the agent runtime's `SMART_STANDALONE_SCOPES` env var must list the same scopes. OpenEMR silently drops any scope from the issued token that wasn't registered against the client — and without `api:oemr` the Standard REST API rejects every call with 403 *"insufficient permissions for the requested resource"* (the FHIR-only `user/*.rs` scopes do not unlock `/apis/default/api/...`, where document upload, allergy/medication/problem writes live). After registering, confirm both sides match:
>
> ```bash
> # What the client is registered for
> railway ssh --service openemr 'MYSQL_PWD="$MYSQL_ROOT_PASS" mariadb -h "$MYSQL_HOST" -u root openemr -e \
>   "SELECT scope FROM oauth_clients WHERE client_id = \"<id from step 3>\";"'
>
> # What the agent will request on /authorize
> railway variables --service copilot-agent --json | python3 -c "import json,sys; print(json.load(sys.stdin)['SMART_STANDALONE_SCOPES'])"
> ```
>
> If they differ, align the client row to match the env (the env is canonical):
>
> ```bash
> SCOPES=$(railway variables --service copilot-agent --json | python3 -c "import json,sys; print(json.load(sys.stdin)['SMART_STANDALONE_SCOPES'])")
> railway ssh --service openemr "MYSQL_PWD=\"\$MYSQL_ROOT_PASS\" mariadb -h \"\$MYSQL_HOST\" -u root openemr -e \"UPDATE oauth_clients SET scope = '$SCOPES' WHERE client_id = '<id from step 3>';\""
> ```
>
> Then sign the user out of the agent UI and back in — the existing session is still holding a token issued before the scope fix and won't pick up the change until a fresh `/authorize` → `/token` exchange.

### 4. Update agent env with the new client credentials

```bash
railway variables --service copilot-agent \
  --set "SMART_STANDALONE_CLIENT_ID=<id from step 3>" \
  --set "SMART_STANDALONE_CLIENT_SECRET=<secret from step 3>"
railway redeploy --service copilot-agent
```

### 4b. Enable the client and confirm `is_enabled = 1`

`bootstrap_standalone_oauth.py` writes the client row with `is_enabled = 0` (OpenEMR's default for newly-registered clients). The agent's `/authorize` flow refuses to mint tokens for disabled clients. Flip it on:

```bash
railway ssh --service openemr 'MYSQL_PWD="$MYSQL_ROOT_PASS" mariadb -h "$MYSQL_HOST" -u root openemr -e \
  "UPDATE oauth_clients SET is_enabled = 1 WHERE client_id = \"<id from step 3>\"; \
   SELECT client_id, is_enabled FROM oauth_clients WHERE client_id = \"<id from step 3>\";"'
```

The dashboard route is **Admin → System → API Clients** if you'd rather click through.

### 5. Seed dr_smith + ACL + CareTeam

```bash
cd agent
uv run python -m scripts.seed.seed_careteam
```

This writes to MariaDB directly: `users`, `users_secure` (bcrypt with `$2y$` prefix — PHP's `password_get_info` rejects `$2b$`), `groups`, and the `care_teams` / `care_team_member` rows that scope dr_smith's panel.

The ACL row (`gacl_aro` membership in `Physicians`) cannot be inserted via raw SQL — phpGACL caches break. Use the script's PHP-helper output:

```bash
uv run python -m scripts.seed.seed_careteam --print-acl-php
# pipe the printed php -r "..." into the openemr container shell
```

### 6. Verify

```bash
# Confirm five-gate login chain passes:
# users.active=1, groups.user='dr_smith', aclGetGroupTitles non-empty,
# users_secure.password set, passwordVerify accepts dr_smith_pass.
curl -sk -X POST https://openemr-production-c5b4.up.railway.app/oauth2/default/login \
  -d 'username=dr_smith&password=dr_smith_pass'   # expect 302 to /authorize
```

Then sign into the agent UI as `dr_smith / dr_smith_pass` — panel should render with the seeded CareTeam patients.

## Known gotchas

1. **Passwords are baked into the volume on first boot.** Changing `MYSQL_PASS` or `OE_PASS` env vars after first boot does NOT change the actual passwords — those live in the DB and `sites/default/sqlconf.php`. To rotate: change the password inside OpenEMR's admin UI (for `OE_PASS`) or via SQL (for `MYSQL_PASS`), then update the env var to match.

2. **First boot takes ~3 minutes.** The image healthcheck has `start_period: 3m`. Railway will show 502 from the edge until Apache is ready and the schema is installed. If it stays 502 past 5–6 minutes, check `railway logs --service openemr` for installer errors.

3. **`railway domain --port` does NOT persist on first creation.** Confirmed during this deploy: passing `--port 80` to `railway domain` created the domain with `targetPort: null`, which made Railway's edge return 502 because it couldn't auto-detect the right port. Fix is to set `targetPort` explicitly via the GraphQL API (or the dashboard's domain settings):

   ```bash
   TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.railway/config.json'))['user']['token'])")
   curl -sS https://backboard.railway.com/graphql/v2 \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"query":"mutation { serviceDomainUpdate(input: { serviceDomainId: \"<DOMAIN_ID>\", domain: \"<HOST>\", targetPort: 80, serviceId: \"<SVC_ID>\", environmentId: \"<ENV_ID>\" }) }"}'
   ```

   `serviceDomainId`, `serviceId`, `environmentId` are visible via:
   ```bash
   curl -sS https://backboard.railway.com/graphql/v2 -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"query":"query { project(id: \"<PROJECT_ID>\") { services { edges { node { id name serviceInstances { edges { node { domains { serviceDomains { id domain targetPort } } } } } } } } } }"}'
   ```

4. **OpenEMR Apache binds both port 80 (HTTP) and 443 (HTTPS) internally.** Railway must target port **80** — port 443 fails because Railway speaks plain HTTP to the upstream and Apache rejects it as "speaking plain HTTP to an SSL-enabled server port". TLS is terminated at Railway's edge; the backend hop is plain HTTP on the private network.

5. **Volume sizing.** Default Railway volume size is 5 GB. Watch the dashboard; expand via `railway volume update` before hitting the cap.

6. **Sleeping/cold starts.** Hobby services do NOT sleep — both services run 24/7 and bill continuously. To avoid burn during inactive periods, scale `numReplicas` to 0 via the dashboard.

7. **First boot logs are dominated by `+` and `.` characters.** That's `openssl genrsa` building entropy for OpenEMR's self-signed certs. Ignore it. The real progress markers in order are: `Running quick setup!` → `Setup Complete!` → `Starting apache!` → Apache `resuming normal operations`. Total time observed: ~3 minutes.

## What this does NOT include yet

- The AI agent service. Will be added as a third Railway service (image or repo build) once Stage 5 of AgentForge planning is done.
- A custom domain. Project spec accepts `*.up.railway.app` for the demo.
- File storage externalization. Patient documents currently land in the `sites` volume. For real-PHI deployment we'd move uploads to S3-compatible storage (R2 / S3) — see `agentforge-docs/ARCHITECTURE.md` (TBD) for the migration plan.
- Audit log shipping. OpenEMR writes audit logs to its DB; for production we'd ship them to an immutable store. Out of scope for MVP.

## Migration path off Railway

If we ever need a HIPAA BAA or multi-region scale, the lift is:

1. Build the OpenEMR fork into a versioned image, push to ECR (AWS) or Artifact Registry (GCP).
2. Provision RDS MariaDB / Aurora MySQL, restore from a Railway dump (`railway connect mariadb` + `mysqldump`).
3. Stand up ECS Fargate / Cloud Run with the same env-var contract.
4. Mount EFS / Filestore for the `sites/` directory, or migrate uploads to S3 first.
5. ALB + ACM cert + Route 53 for the domain.

The env-var contract (`MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASS`, `OE_USER`, `OE_PASS`) is identical across providers, so the application layer needs zero changes.
