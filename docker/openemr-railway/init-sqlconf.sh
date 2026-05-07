#!/bin/sh
# Materialize sqlconf.php from environment variables if it is missing from
# the mounted sites/ volume. Solves the failure mode where the openemr-side
# Railway volume desyncs from the mariadb volume (volume detached, wiped,
# or recreated) — without this, the upstream entrypoint reruns
# auto_configure.php and crashes against existing DB tables.
#
# Runbook: runbook/004-openemr-redeploy-install-loop.md
#
# Idempotent: only writes the file when it does not exist. Never overwrites
# a working sqlconf.php that the volume already has.

set -eu

SITES_DIR=/var/www/localhost/htdocs/openemr/sites
DEFAULT_DIR="$SITES_DIR/default"
SQLCONF="$DEFAULT_DIR/sqlconf.php"
SNAPSHOT=/opt/openemr-default-sites

# Restore default sites/ contents that the volume mount hid. The upstream
# image ships with sites/default/{config.php,statement.inc.php,LBF/,...}
# baked in; the empty (or partial) volume mount masks them. Copy any
# missing files in from the build-time snapshot. -n (no-clobber) ensures
# we never overwrite live data the volume already has.
if [ -d "$SNAPSHOT" ]; then
    echo "[init-sqlconf] restoring missing sites/ defaults from snapshot"
    mkdir -p "$SITES_DIR"
    # rsync --ignore-existing recurses into shared dirs and only
    # copies entries the volume is missing. BusyBox `cp -a -n` skips
    # any directory that already exists in the destination, so it
    # would never restore sites/default/config.php once the volume
    # has anything in sites/default/ at all.
    rsync -a --ignore-existing --owner --group --perms \
        "$SNAPSHOT/" "$SITES_DIR/"
else
    echo "[init-sqlconf] WARN snapshot $SNAPSHOT missing, cannot restore site defaults"
fi

db_has_tables() {
    [ -n "${MYSQL_HOST:-}" ] || return 1
    [ -n "${MYSQL_USER:-}" ] || return 1
    [ -n "${MYSQL_PASS:-}" ] || return 1
    db_count=$(MYSQL_PWD="$MYSQL_PASS" mariadb --connect-timeout=8 \
        --skip-column-names --batch \
        -h "$MYSQL_HOST" -u "$MYSQL_USER" \
        -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${MYSQL_DATABASE:-openemr}'" 2>/dev/null) || return 1
    [ -n "$db_count" ] && [ "$db_count" -gt 0 ]
}

if [ -f "$SQLCONF" ] && grep -q '\$config = 1' "$SQLCONF"; then
    echo "[init-sqlconf] $SQLCONF present and configured, skipping bootstrap"
elif ! db_has_tables; then
    # Fresh install path: DB is empty (or unreachable). Don't write
    # sqlconf.php — let upstream auto_configure.php run a clean install,
    # which writes its own sqlconf.php with $config=1 after the schema
    # is loaded.
    echo "[init-sqlconf] DB has no tables, skipping bootstrap so upstream auto_configure runs"
else
    if [ -f "$SQLCONF" ]; then
        echo "[init-sqlconf] $SQLCONF present but \$config != 1, rewriting from env"
    else
        echo "[init-sqlconf] $SQLCONF missing but DB has tables, writing from env (desync recovery)"
    fi

    : "${MYSQL_HOST:?MYSQL_HOST is required to bootstrap sqlconf.php}"
    : "${MYSQL_USER:?MYSQL_USER is required to bootstrap sqlconf.php}"
    : "${MYSQL_PASS:?MYSQL_PASS is required to bootstrap sqlconf.php}"

    mkdir -p "$DEFAULT_DIR"

    # Generate the file via PHP so var_export() handles escaping. Shell
    # interpolation into single-quoted PHP strings would corrupt
    # passwords containing single quotes or backslashes.
    SQLCONF_OUT="$SQLCONF" php -r '
$host  = getenv("MYSQL_HOST");
$port  = getenv("MYSQL_PORT") ?: "3306";
$login = getenv("MYSQL_USER");
$pass  = getenv("MYSQL_PASS");
$dbase = getenv("MYSQL_DATABASE") ?: "openemr";

$lines = [];
$lines[] = "<?php";
$lines[] = "// Bootstrapped by docker/openemr-railway/init-sqlconf.sh.";
$lines[] = "// See runbook/004-openemr-redeploy-install-loop.md.";
$lines[] = "";
$lines[] = "\$host  = " . var_export($host, true) . ";";
$lines[] = "\$port  = " . var_export($port, true) . ";";
$lines[] = "\$login = " . var_export($login, true) . ";";
$lines[] = "\$pass  = " . var_export($pass, true) . ";";
$lines[] = "\$dbase = " . var_export($dbase, true) . ";";
$lines[] = "\$db_encoding = \"utf8mb4\";";
$lines[] = "";
$lines[] = "global \$disable_utf8_flag;";
$lines[] = "\$disable_utf8_flag = false;";
$lines[] = "";
$lines[] = "\$sqlconf = [];";
$lines[] = "\$sqlconf[\"host\"]        = \$host;";
$lines[] = "\$sqlconf[\"port\"]        = \$port;";
$lines[] = "\$sqlconf[\"login\"]       = \$login;";
$lines[] = "\$sqlconf[\"pass\"]        = \$pass;";
$lines[] = "\$sqlconf[\"dbase\"]       = \$dbase;";
$lines[] = "\$sqlconf[\"db_encoding\"] = \$db_encoding;";
$lines[] = "";
$lines[] = "// Install-complete marker. Without this, auto_configure.php re-runs on";
$lines[] = "// every container start and crashes on the first existing table.";
$lines[] = "\$config = 1;";

file_put_contents(getenv("SQLCONF_OUT"), implode("\n", $lines) . "\n");
'

    chmod 0644 "$SQLCONF"
    # Match upstream sites/default ownership so the upstream chmod 400
    # hardening leaves Apache able to read the file.
    if id apache >/dev/null 2>&1; then
        chown apache:root "$SQLCONF"
    fi

    echo "[init-sqlconf] wrote $SQLCONF (host=$MYSQL_HOST db=${MYSQL_DATABASE:-openemr} user=$MYSQL_USER)"
fi

# Hand off to the upstream entrypoint. Working directory is the same as the
# upstream image (/var/www/localhost/htdocs/openemr), so the relative path
# matches openemr/openemr:latest's CMD.
exec ./openemr.sh "$@"
