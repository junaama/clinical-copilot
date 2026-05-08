# Production OpenEMR image extended with the Co-Pilot launcher module
# and the local fork's PHP trees (src/, library/, apis/).
#
# Build base is openemr/openemr:latest from Docker Hub. We then overlay:
#   - oe-module-copilot-launcher/ (the custom module)
#   - patches/ — a mirror of the local fork's src/, library/, apis/
#     trees, staged into the build context by scripts/deploy-openemr.sh
#     before `railway up`. This is intentionally a whole-tree overlay
#     rather than per-file patches; see the rationale in
#     scripts/deploy-openemr.sh.
#
# Apache config, PHP runtime, MySQL wiring, and the upstream entrypoint
# come from the FROM image and are unchanged. Upstream security patches
# in dirs we DON'T overlay (interface/, public/, modules/, sql/, etc.)
# arrive by bumping the FROM tag. Patches in dirs we DO overlay (src/,
# library/, apis/) are masked — rebase the local fork against upstream
# periodically.
#
# Build-context constraint: Railway's CLI uploads only the build
# context, and reaching above it isn't allowed. Don't COPY from
# `interface/modules/...` or `src/...` directly — they must be staged
# into docker/openemr-railway/ first by deploy-openemr.sh.

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

# Overlay the local fork's PHP trees on top of the upstream image. The
# patches/ directory mirrors openemr's layout — patches/src/...,
# patches/library/..., patches/apis/... — and is staged by
# scripts/deploy-openemr.sh before `railway up`. The COPY effectively
# replaces the upstream src/, library/, and apis/ trees with the local
# fork's versions so deployed code matches what the local repo tests
# against (see deploy-openemr.sh for the full rationale).
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
