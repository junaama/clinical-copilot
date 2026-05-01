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
COPY oe-module-copilot-launcher \
     /var/www/localhost/htdocs/openemr/interface/modules/custom_modules/oe-module-copilot-launcher
