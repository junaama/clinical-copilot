# Production OpenEMR image extended with the Co-Pilot launcher module.
#
# Stays as close to the upstream image as possible: we only COPY one
# directory in. Everything else (Apache config, PHP runtime, MySQL wiring,
# entrypoint script) is unchanged. That means upstream security patches
# arrive by bumping the FROM tag.
#
# Build context is `docker/openemr-railway/` — `oe-module-copilot-launcher/`
# is staged into the context by `build.sh` before `railway up`. Don't COPY
# from `interface/modules/...` directly: Railway's CLI uploads only the
# build context, and reaching above it isn't allowed.

FROM openemr/openemr:latest

# Drop our PHP module into the path OpenEMR's module loader scans.
# (See ModulesClassLoader + custom_modules discovery in upstream OpenEMR.)
#
# --chown=apache:root matches the ownership upstream uses for
# /var/www/localhost/htdocs/openemr/. Without this, COPY defaults to
# root:root, and the upstream entrypoint's `chmod 400` hardening then
# makes the files unreadable to Apache (which runs as `apache`).
COPY --chown=apache:root oe-module-copilot-launcher \
     /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-copilot-launcher

# Layer local patches to upstream OpenEMR PHP on top. The patches/ tree
# mirrors the upstream layout (patches/src/... → openemr/src/...), staged
# by scripts/deploy-openemr.sh. Currently fixes session-lock serialization
# on /apis/ requests by enabling read_and_close for the API session.
COPY --chown=apache:root patches/ /var/www/localhost/htdocs/openemr/

# Install marker bootstrap. The upstream image reruns auto_configure.php
# whenever sites/default/sqlconf.php is missing, which crashes on existing
# DB tables when the openemr-side Railway volume has desynced from the
# mariadb volume. This wrapper writes sqlconf.php from env vars (with the
# install-complete $config = 1 marker) before handing off to the upstream
# entrypoint, so a desynced volume no longer triggers the install loop.
# See runbook/004-openemr-redeploy-install-loop.md.
#
# Also snapshot the upstream sites/ tree to /opt/openemr-default-sites/.
# When Railway mounts the volume over /var/www/.../sites, the in-image
# sites/default/ contents (config.php, statement.inc.php, etc.) get
# hidden. The init script restores any missing files into the mounted
# volume from this snapshot.
RUN cp -a /var/www/localhost/htdocs/openemr/sites /opt/openemr-default-sites
COPY init-sqlconf.sh /usr/local/bin/init-sqlconf.sh
RUN chmod +x /usr/local/bin/init-sqlconf.sh
CMD ["/usr/local/bin/init-sqlconf.sh"]
