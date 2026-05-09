# 004 — OpenEMR redeploy reruns installer and crashes on existing tables

## Symptom

After `bash scripts/deploy-openemr.sh` (or any `railway redeploy --service
openemr`), the container crash-loops with logs that look like:

```
Couldn't set up. Any of these reasons could be what's wrong:
 - You didn't spin up a MySQL container or connect your OpenEMR container to a mysql instance
 - MySQL is still starting up and wasn't ready for connection yet
 - The Mysql credentials were incorrect
chmod: ./ccdaservice/node_modules/oe-schematron-service/node_modules/oe-cda-schematron: No such file or directory
mkdir: can't create directory '/tmp/php-file-cache': File exists
ERROR IN OPENEMR INSTALL: Unable to execute SQL: CREATE TABLE ccda_field_mapping (...) due to: Table 'ccda_field_mapping' already exists
PHP Fatal error:  Uncaught Exception: ERROR: unable to execute SQL: 'CREATE TABLE ccda_field_mapping ...' due to: Table 'ccda_field_mapping' already exists
 in /var/www/localhost/htdocs/openemr/auto_configure.php:56
```

The first three "Couldn't set up" lines are red herrings printed
unconditionally by the upstream image. The fatal is the `auto_configure.php`
exception: the installer is re-running against a database that already has
tables, and bombs the moment it hits `CREATE TABLE`.

The user-visible effect is that every redeploy of the `openemr` service
appears to wipe the registered OAuth clients and admin-Globals settings,
forcing a full repeat of the post-redeploy admin checklist
(`agentforge-docs/DEPLOYMENT.md` §"Post-redeploy admin checklist").

## Root cause

Two Railway volumes back the deployment:

| Volume | Mount | Holds |
|---|---|---|
| `mariadb` | `/var/lib/mysql` | DB tables, OAuth clients, patient data |
| `openemr` | `/var/www/localhost/htdocs/openemr/sites` | `default/sqlconf.php` (install marker), `default/documents/` |

The upstream `openemr/openemr` entrypoint decides install-vs-skip by checking
for `sites/default/sqlconf.php` with `$config = 1`. When the **`openemr`
volume is wiped or detached but the `mariadb` volume persists**, the two
fall out of sync:

- The entrypoint sees no `sqlconf.php` → starts `auto_configure.php`.
- `auto_configure.php` connects, finds `ccda_field_mapping` already exists
  → fatal exception → container exits → Railway restarts → loop.

Mid-loop, `auto_configure.php` may run far enough to truncate or recreate
some tables (notably `oauth_clients`), which is why your registered agent
client sometimes vanishes mid-deploy even though "the DB persisted."

How the volumes get out of sync:

- The volume mount path was changed or the volume detached in the Railway
  dashboard.
- An earlier Dockerfile edit shadowed the mount path with image-baked
  files (a `COPY ... /var/www/localhost/htdocs/openemr/` whose source tree
  contained a `sites/` subdirectory).
- Railway re-provisioned the volume (rare; usually only on volume rename
  or project import).

Verify both volumes are attached:

```bash
railway service openemr
railway volume list   # should show one volume mounted at .../openemr/sites
railway service mariadb
railway volume list   # should show one volume mounted at /var/lib/mysql
```

If the openemr-side volume is missing or freshly empty, this runbook
applies. If both volumes are attached and `sqlconf.php` is present, the
problem is something else — escalate.

## Fix A — reconstruct `sqlconf.php` (preserves all data)

Use this when the DB still has the OpenEMR schema and you want to keep
patients, encounters, OAuth clients, and admin Globals settings.

### A.1 — Stop the install loop

The container is restarting on every crash. To exec into a stable shell,
either pause restarts in the Railway dashboard or rely on the brief window
between restarts.

### A.2 — Collect the credentials currently stored in Railway

```bash
# OpenEMR DB user password (the one that auto_configure.php would have written)
railway variables --service openemr | grep '^MYSQL_PASS='

# DB host (resolves to the private MariaDB hostname at runtime)
railway variables --service openemr | grep '^MYSQL_HOST='

# DB user
railway variables --service openemr | grep '^MYSQL_USER='
```

Capture `MYSQL_PASS`, `MYSQL_HOST`, and `MYSQL_USER` values. The DB name is
`openemr` and the port is `3306` unless overridden.

### A.3 — Confirm the DB user can still log in

The OpenEMR-side `MYSQL_PASS` was written into MariaDB on first boot. If
`MYSQL_PASS` was *rotated* in env after first boot, Railway env and DB are
out of sync; fall through to "If the password no longer matches" below.

```bash
railway connect mariadb
# In the MariaDB shell:
SELECT User, Host FROM mysql.user WHERE User='openemr';
# Then test the password from another shell:
mariadb -h <MYSQL_HOST> -u openemr -p'<MYSQL_PASS>' openemr -e 'SHOW TABLES LIMIT 1;'
```

If that returns a table name, the credentials are still valid — proceed.

If the password no longer matches, reset it inside MariaDB:

```sql
-- in `railway connect mariadb` (root)
ALTER USER 'openemr'@'%' IDENTIFIED BY '<the value of MYSQL_PASS>';
FLUSH PRIVILEGES;
```

### A.4 — Write `sqlconf.php` into the container

Get a shell into the running openemr container (Railway dashboard → service
→ "Connect" → shell). Write the file:

```bash
cat > /var/www/localhost/htdocs/openemr/sites/default/sqlconf.php <<'EOF'
<?php
// Manually reconstructed after volume desync.
// See runbook/004-openemr-redeploy-install-loop.md.
$host  = getenv('MYSQL_HOST');
$port  = '3306';
$login = getenv('MYSQL_USER');
$pass  = getenv('MYSQL_PASS');
$dbase = 'openemr';
$db_encoding = 'utf8mb4';

global $disable_utf8_flag;
$disable_utf8_flag = false;

$sqlconf['host']        = $host;
$sqlconf['port']        = $port;
$sqlconf['login']       = $login;
$sqlconf['pass']        = $pass;
$sqlconf['dbase']       = $dbase;
$sqlconf['db_encoding'] = $db_encoding;

// Install-complete marker. Without this, auto_configure.php re-runs on
// every container start and crashes on the first existing table.
$config = 1;
EOF

chown apache:apache /var/www/localhost/htdocs/openemr/sites/default/sqlconf.php
chmod 0644 /var/www/localhost/htdocs/openemr/sites/default/sqlconf.php
```

(If the upstream image has a different web user, e.g. `www-data`, use that
instead — check with `ps -ef | grep -E 'apache|httpd|php-fpm'`.)

### A.5 — Restart the container

```bash
railway redeploy --service openemr
railway logs --service openemr
```

Healthy logs end with Apache starting and stop printing `auto_configure`
output. Hit `https://openemr-production-c5b4.up.railway.app/` and log in.

### A.6 — Verify data survived

```bash
# OAuth client should still be registered
railway connect mariadb
SELECT client_id, client_name FROM openemr.oauth_clients;

# CareTeam seed should still be present
SELECT username FROM openemr.users WHERE username='dr_smith';
```

If both queries return rows, you skip the post-redeploy admin checklist
entirely. If either is empty, the install loop corrupted those tables
before this fix landed — run only the missing steps from
`agentforge-docs/DEPLOYMENT.md` §"Post-redeploy admin checklist" (typically
just §3 register OAuth client + §4 update agent env, since Globals are in
the `globals` table and usually survive).

## Fix B — nuclear: drop the schema and let install run cleanly

Use this only when the DB schema is partially corrupted (mid-install
crash left tables in inconsistent state) and Fix A doesn't resolve the
loop.

### B.1 — Back up first

```bash
railway connect mariadb
# In another shell:
mariadb -h <host> -u root -p'<root pass>' openemr > /tmp/openemr-backup-$(date +%F).sql
```

### B.2 — Drop the schema

```sql
DROP DATABASE openemr;
CREATE DATABASE openemr CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL ON openemr.* TO 'openemr'@'%';
FLUSH PRIVILEGES;
```

### B.3 — Redeploy and let the installer run

```bash
railway redeploy --service openemr
railway logs --service openemr   # watch for "OpenEMR install completed"
```

### B.4 — Re-run the post-redeploy admin checklist

Walk all six steps in `agentforge-docs/DEPLOYMENT.md` §"Post-redeploy
admin checklist": enable APIs, set `site_addr_oath`, register the
standalone OAuth client, update agent env, seed `dr_smith` + ACL +
CareTeam, verify the OAuth login chain.

## Verification (both fixes)

```bash
# 1. Container stable
railway logs --service openemr | tail -50    # no auto_configure spam

# 2. UI reachable
curl -sI https://openemr-production-c5b4.up.railway.app/ | head -1
# expect HTTP/2 200 or 302

# 3. OAuth login chain
curl -sk -X POST https://openemr-production-c5b4.up.railway.app/oauth2/default/login \
  -d 'username=dr_smith&password=dr_smith_pass' -o /dev/null -w '%{http_code}\n'
# expect 302
```

## Prevention

Until a Dockerfile-level fix lands (write `sqlconf.php` from env on every
container start, regardless of volume state), do the following before
each `deploy-openemr.sh`:

1. `railway service openemr && railway volume list` — confirm one volume
   is attached at `/var/www/localhost/htdocs/openemr/sites`.
2. Snapshot the current `sqlconf.php` to a known location *outside* the
   container, so recovery in A.4 is paste-not-reconstruct.
3. After a healthy redeploy, run the verification block above before
   touching anything else.
