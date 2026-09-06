{% load static %}/* ==========================================================================
   A1 360 service worker.

   Without this, a technician who closes the app on a site with no signal and
   reopens it gets the browser's dinosaur — the queued work is safe on the
   device but unreachable, which reads as lost. This makes the shell and the
   screens they have already visited load with no network at all.

   Served from the site root so its scope covers every URL. See the `sw`
   route in inventoryproject/urls.py.

   Two rules it will not break:
     * Only GET is ever cached. A clock event or an assessment is queued by
       the page in IndexedDB and replayed deliberately — never silently
       retried from here, where nothing could report the outcome.
     * Pages are cleared the moment an unauthenticated page loads, so a
       shared phone cannot show the previous person's records after a logout.
   ========================================================================== */

var VERSION = "a1-360-v1";
var SHELL_CACHE = VERSION + "-shell";
var PAGE_CACHE = VERSION + "-pages";

// The parts of the app that never differ per user.
var SHELL = [
  "{% static 'css/a1.css' %}",
  "{% static 'js/a1-offline.js' %}",
  "{% static 'img/a1-mark.png' %}",
  "{% static 'img/a1-logo.png' %}",
  "{% url 'offline' %}"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) {
      // addAll fails the whole install if one entry 404s; add individually so
      // a missing asset degrades rather than leaving the worker uninstalled.
      return Promise.all(
        SHELL.map(function (url) {
          return cache.add(url).catch(function () { return null; });
        })
      );
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names.filter(function (name) {
          return name.indexOf(VERSION) !== 0;
        }).map(function (name) { return caches.delete(name); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

// The page tells us when nobody is signed in, which is the moment to drop
// every cached page. Anything the previous user saw goes with it.
self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "clear-pages") {
    event.waitUntil(caches.delete(PAGE_CACHE));
  }
});

function isStatic(url) {
  return url.pathname.indexOf("{% get_static_prefix %}") === 0;
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never hold on to a file the technician uploaded or a CSV export.
  if (url.pathname.indexOf("/media/") === 0 || url.searchParams.get("export")) return;

  // Static files change name when they change content, so serving from the
  // cache first is safe and is what makes a cold start instant.
  if (isStatic(url)) {
    event.respondWith(
      caches.match(request).then(function (hit) {
        return hit || fetch(request).then(function (response) {
          if (response.ok) {
            var copy = response.clone();
            caches.open(SHELL_CACHE).then(function (c) { c.put(request, copy); });
          }
          return response;
        });
      })
    );
    return;
  }

  if (request.mode !== "navigate") return;

  // Pages are network-first: a technician with signal must never be shown a
  // stale job list. The cache is the fallback, and the offline page is the
  // fallback of last resort.
  event.respondWith(
    fetch(request).then(function (response) {
      if (response.ok && response.type === "basic") {
        var copy = response.clone();
        caches.open(PAGE_CACHE).then(function (c) { c.put(request, copy); });
      }
      return response;
    }).catch(function () {
      return caches.match(request).then(function (hit) {
        return hit || caches.match("{% url 'offline' %}").then(function (page) {
          return page || new Response(
            "You are offline and this screen has not been opened on this device yet.",
            { status: 503, headers: { "Content-Type": "text/plain" } }
          );
        });
      });
    })
  );
});
