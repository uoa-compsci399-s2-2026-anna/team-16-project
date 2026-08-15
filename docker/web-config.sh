#!/bin/sh
#
# ONE ENVIRONMENT VARIABLE, TWO CONSUMERS THAT CANNOT DISAGREE.
#
# Installed as /docker-entrypoint.d/16-kaicalc-config.sh, so nginx's own entrypoint runs
# it before the server binds. It reads three variables from the environment and writes
# two files from them:
#
#   /etc/nginx/conf.d/kaicalc.conf        the server block, with `connect-src` and
#                                         `img-src` filled in
#   /usr/share/nginx/html/js/config.js    the ES module web/js/api.js imports
#
# THE DEFECT THIS EXISTS TO REMOVE. The client's production domain used to be written
# out twice - in web/js/api.js as the WordPress base, and in docker/nginx.conf as a
# `connect-src` entry - and the two had to agree. They fail asymmetrically, which is why
# nobody would have caught the drift: a wrong policy makes the news quietly not load, and
# a wrong URL sends the browser to ask a domain nobody chose. This project's deliverable
# is source code and documentation; DNS and hosting are explicitly out of scope, so no
# domain belongs in a built image at all. Now neither file names one, and the single
# value that does is deployment configuration.
#
# ENVSUBST IS CALLED WITH AN EXPLICIT SHELL-FORMAT LIST, AND THAT IS LOAD-BEARING.
# `envsubst < template` with no argument substitutes EVERY `$name` it finds, and the
# nginx configuration is full of them: $time_local, $request, $status, $body_bytes_sent,
# $request_time, $uri, $scheme, $http_host. Every one would be replaced by the empty
# string, producing a configuration that is still valid nginx syntax and logs blank lines
# for every request while `error_page` redirects land on nothing. The two-name list below
# means envsubst physically cannot touch anything else, whatever ends up in the
# container's environment. The stock 20-envsubst-on-templates.sh with
# NGINX_ENVSUBST_FILTER would also work, but its whitelist is built from whichever
# variables happen to exist at start-up, which is a weaker promise than naming them.
#
# VALIDATION IS ALSO THE INJECTION GUARD. Each origin has to match a bare
# scheme://host[:port] before it is used, so no value reaching either output can contain
# a quote, a semicolon, a newline or a space - which is what makes it safe to interpolate
# one into a CSP header and into a single-quoted JavaScript string literal. A value that
# does not match stops the container from starting, which is the correct failure: a typo
# in an API origin must not become a calculator quietly showing somebody else's numbers,
# and a typo in a news origin must not become a policy that silently refuses the feed.

set -eu

ME=kaicalc-config
TEMPLATE=${KAICALC_NGINX_TEMPLATE:-/etc/nginx/kaicalc.conf.template}
CONF=${KAICALC_NGINX_CONF:-/etc/nginx/conf.d/kaicalc.conf}
WEB_CONFIG=${KAICALC_WEB_CONFIG:-/usr/share/nginx/html/js/config.js}

fail() {
    echo "$ME: $1" >&2
    exit 1
}

# A bare origin: scheme, host, optional port. No path, no trailing slash, no query, no
# wildcard, no credentials. `https://example.org` passes; `https://example.org/`,
# `https://example.org/wp-json`, `*.example.org` and `example.org` do not.
check_origin() {
    printf '%s' "$2" | grep -Eq '^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$' || fail \
        "$1=$2 is not a bare origin. Use scheme://host[:port] with no trailing slash and no path (for example https://kaicommitment.org.nz)."
}

# Append a token to a space-separated CSP source list unless it is already there. Two
# variables may legitimately name the same origin, and a directive listing it twice is
# valid but reads like a mistake.
append_once() {
    for existing in $1; do
        if [ "$existing" = "$2" ]; then
            printf '%s' "$1"
            return 0
        fi
    done
    printf '%s %s' "$1" "$2"
}

NEWS_ORIGIN=${KAICALC_NEWS_ORIGIN:-}
API_ORIGIN=${KAICALC_API_ORIGIN:-}
NEWS_IMAGE_ORIGINS=${KAICALC_NEWS_IMAGE_ORIGINS:-}

# 'self' is the origin that served the page, and it is never removed: the calculator's own
# API calls are relative and the locale catalogues are same-origin.
CONNECT_SRC="'self'"
IMG_SRC="'self' data:"

if [ -n "$NEWS_ORIGIN" ]; then
    check_origin KAICALC_NEWS_ORIGIN "$NEWS_ORIGIN"
    CONNECT_SRC=$(append_once "$CONNECT_SRC" "$NEWS_ORIGIN")
    # img-src follows the news origin because the origin is configuration now rather than
    # a guess, and because connect-src already reaches it - see the note on
    # `createNewsCard` in web/js/home.js.
    IMG_SRC=$(append_once "$IMG_SRC" "$NEWS_ORIGIN")
fi

if [ -n "$API_ORIGIN" ]; then
    check_origin KAICALC_API_ORIGIN "$API_ORIGIN"
    CONNECT_SRC=$(append_once "$CONNECT_SRC" "$API_ORIGIN")
fi

# Media on a separate host. WordPress libraries commonly serve from a CDN rather than
# from the site origin, and that host is on img-src and on nothing else.
for image_origin in $NEWS_IMAGE_ORIGINS; do
    check_origin KAICALC_NEWS_IMAGE_ORIGINS "$image_origin"
    IMG_SRC=$(append_once "$IMG_SRC" "$image_origin")
done

[ -f "$TEMPLATE" ] || fail "$TEMPLATE is missing; the image is built wrong."

KAICALC_CSP_CONNECT_SRC=$CONNECT_SRC
KAICALC_CSP_IMG_SRC=$IMG_SRC
export KAICALC_CSP_CONNECT_SRC KAICALC_CSP_IMG_SRC

envsubst '${KAICALC_CSP_CONNECT_SRC} ${KAICALC_CSP_IMG_SRC}' < "$TEMPLATE" > "$CONF" \
    || fail "could not write $CONF. It must be writable by the user nginx runs as."

# The checked-in web/js/config.js carries the same two exports with empty values, so a
# plain checkout serves a working front end with no news feed and a relative API path.
# This overwrites it in the image, every start, from the environment.
cat > "$WEB_CONFIG" <<EOF || fail "could not write $WEB_CONFIG. It must be writable by the user nginx runs as."
// GENERATED AT CONTAINER START by docker/web-config.sh. Editing this copy inside a
// running container is overwritten on the next start, and the Content-Security-Policy
// would not follow the edit - set KAICALC_NEWS_ORIGIN and KAICALC_API_ORIGIN instead, and
// the policy and this file are built from them together. The checked-in default lives at
// web/js/config.js and documents both values.
export const NEWS_ORIGIN = '$NEWS_ORIGIN'
export const API_ORIGIN = '$API_ORIGIN'
EOF

echo "$ME: connect-src $CONNECT_SRC"
echo "$ME: img-src $IMG_SRC"
echo "$ME: news origin ${NEWS_ORIGIN:-<unset - the home page will drop its news section>}"
echo "$ME: api origin ${API_ORIGIN:-<unset - the front end uses the relative /api/v1>}"
