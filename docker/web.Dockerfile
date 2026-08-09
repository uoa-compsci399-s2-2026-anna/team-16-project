# kaicalc-web - nginx plus the static front end, on port 18080.
#
# Build context is the REPOSITORY ROOT:
#     docker build -f docker/web.Dockerfile -t kaicalc-web:local .
#
# This is the only service that has to be published, and it is the only origin
# the browser ever sees: it serves web/ at /, proxies /api/v1/ to the api
# service and /admin to the admin service. See docker/nginx.conf for the
# routing and for why /admin is routed by default.
#
# There is no build step. The front end is plain HTML, CSS and ES modules with
# Chart.js - no React, no Node.js, no bundler - so the image is a copy, not a
# compilation. Nothing to go stale, nothing to reproduce on a machine that has
# never had npm on it.

FROM nginx:1.27-alpine

# The default site. Removed rather than overwritten so there is no doubt about
# which server block is in force.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY docker/nginx.conf /etc/nginx/conf.d/kaicalc.conf
COPY docker/nginx-proxy-headers.conf /etc/nginx/kaicalc_proxy_headers.conf

# The front end, exactly as it is in the repository. `web/README.md` is a
# developer note and is not worth a separate COPY to exclude - it is four
# kilobytes of Markdown behind a URL nobody will guess.
COPY web/ /usr/share/nginx/html/

# Run unprivileged.
#
# The stock image starts its master process as root so it can bind port 80 and
# then drops the workers to `nginx`. This one listens on 18080, which needs no
# privilege at all, so the whole process tree can run as `nginx` (uid 101, an
# account the base image already creates). Two things have to move for that to
# work:
#   - the pid file, because /var/run is root-owned; and
#   - the temp paths nginx writes request bodies and proxy buffers into.
# Both are redirected into /tmp by the override below.
# `pid` is REPLACED, not added. nginx refuses a duplicate `pid` directive
# outright ("directive is duplicate"), so an include that adds a second one
# produces an image that fails at start-up rather than an unprivileged one.
RUN sed -i 's|^pid .*|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf \
 && sed -i 's|^http {|http {\n    client_body_temp_path /tmp/client_temp;\n    proxy_temp_path /tmp/proxy_temp;\n    fastcgi_temp_path /tmp/fastcgi_temp;\n    uwsgi_temp_path /tmp/uwsgi_temp;\n    scgi_temp_path /tmp/scgi_temp;|' /etc/nginx/nginx.conf \
 && chown -R nginx:nginx /var/cache/nginx /etc/nginx/conf.d

USER nginx

EXPOSE 18080

# The stock entrypoint runs /docker-entrypoint.d/*.sh (envsubst templating and
# ipv6 detection). Those scripts skip their root-only steps with a notice when
# the container is not root, which is fine - nothing here uses a template.
