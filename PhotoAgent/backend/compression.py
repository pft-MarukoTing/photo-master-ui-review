"""gzip for text responses (HTML/JS/CSS/JSON).

Nothing between the browser and this app compresses today -- nginx.conf in
this repo fronts the worker, not this Flask app -- so app.js (~575KB),
index.html (~325KB), styles.css (~210KB) and every /result JSON (~150KB)
went out uncompressed (measured: app.js 590KB -> 173KB, index.html
331KB -> 84KB; /result JSON compresses similarly, ~3.4x).

Skips anything already encoded, non-200 responses (a 304 has no body, a 206
is a byte range of the uncompressed file), small bodies, and non-text types
(images are already compressed). A reverse proxy in front that compresses
too leaves responses that already carry Content-Encoding alone.
"""
import gzip
import threading
from collections import OrderedDict

from flask import request

COMPRESSIBLE_MIMETYPES = {
    "text/html",
    "text/css",
    "text/plain",
    "text/javascript",
    "application/javascript",
    "application/json",
    "image/svg+xml",
}
MIN_SIZE = 1024
COMPRESS_LEVEL = 6

# Static files are served with an ETag (send_from_directory) and rarely
# change, so their compressed bytes are kept per ETag instead of re-gzipping
# app.js on every page load. Bounded -- the frontend is a few dozen files.
_STATIC_CACHE_MAX = 64
_static_cache = OrderedDict()
# The backend runs threaded (run_backend.py) -- without this, one request
# evicting an entry between another's get() and move_to_end() would raise
# KeyError and 500 that static file. Compression itself stays outside the
# lock so concurrent requests don't queue behind it.
_static_cache_lock = threading.Lock()


def _cached_gzip(etag, data):
    with _static_cache_lock:
        body = _static_cache.get(etag)
        if body is not None:
            _static_cache.move_to_end(etag)
            return body
    body = gzip.compress(data, compresslevel=COMPRESS_LEVEL)
    with _static_cache_lock:
        _static_cache[etag] = body
        _static_cache.move_to_end(etag)
        while len(_static_cache) > _STATIC_CACHE_MAX:
            _static_cache.popitem(last=False)
    return body


def gzip_response(response):
    if response.status_code != 200 or response.mimetype not in COMPRESSIBLE_MIMETYPES:
        return response
    if response.headers.get("Content-Encoding"):
        return response
    # Same representation, different encoding -- caches must key on it,
    # including on the uncompressed copy sent to a client without gzip.
    response.vary.add("Accept-Encoding")
    if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
        return response
    # send_from_directory hands back a file wrapper; read it so it can be
    # compressed (the frontend's files are all small enough for that).
    response.direct_passthrough = False
    data = response.get_data()
    if len(data) < MIN_SIZE:
        return response
    etag, weak = response.get_etag()
    body = _cached_gzip(etag, data) if etag else gzip.compress(data, compresslevel=COMPRESS_LEVEL)
    response.set_data(body)
    response.headers["Content-Encoding"] = "gzip"
    if etag and not weak:
        # The gzipped bytes differ from what the strong ETag names. A weak
        # ETag still validates If-None-Match (weak comparison), so 304s keep
        # working for revalidated static files.
        response.set_etag(etag, weak=True)
    return response


def init_compression(app):
    app.after_request(gzip_response)
