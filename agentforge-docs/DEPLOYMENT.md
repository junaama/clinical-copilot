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
    ├── Image: openemr/openemr:latest
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
