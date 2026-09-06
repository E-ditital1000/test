/* ==========================================================================
   A1 360 — the offline layer for every mobile surface.

   Connectivity is assumed absent, not present. Three things live here so the
   clock, the check-in and the assessment share one implementation rather
   than three subtly different ones:

     A1.uuid()    a client-generated id, minted on the device BEFORE a record
                  leaves it, which is what makes resubmission idempotent
     A1.locate()  GPS captured at the event only, never continuously, and
                  never blocking: five seconds, then record without it
     A1.store     durable key/value on the device

   A1.store is IndexedDB first. A site assessment carries photographs, and a
   handful of them exceeds the ~5 MB localStorage ceiling — losing a
   technician's afternoon because the fifth photo overflowed a quota is
   exactly the failure this system exists to prevent. localStorage remains as
   a fallback for browsers where IndexedDB is unavailable or blocked.
   ========================================================================== */
(function (global) {
  "use strict";

  var DB_NAME = "a1-360";
  var DB_VERSION = 1;
  var STORE = "drafts";

  function uuid() {
    if (global.crypto && global.crypto.randomUUID) return global.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  // -- IndexedDB, with a localStorage fallback ---------------------------

  var dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve, reject) {
      if (!global.indexedDB) return reject(new Error("no indexeddb"));
      var request = global.indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = function () {
        if (!request.result.objectStoreNames.contains(STORE)) {
          request.result.createObjectStore(STORE);
        }
      };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error); };
      // Safari in private mode resolves neither; do not hang the caller.
      setTimeout(function () { reject(new Error("indexeddb timeout")); }, 3000);
    }).catch(function (error) {
      dbPromise = null;
      throw error;
    });
    return dbPromise;
  }

  function idb(mode, work) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode);
        var request = work(tx.objectStore(STORE));
        request.onsuccess = function () { resolve(request.result); };
        request.onerror = function () { reject(request.error); };
      });
    });
  }

  function localGet(key) {
    try {
      var raw = global.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  function localSet(key, value) {
    try { global.localStorage.setItem(key, JSON.stringify(value)); return true; }
    catch (e) { return false; }
  }

  var store = {
    get: function (key) {
      return idb("readonly", function (s) { return s.get(key); })
        .then(function (value) { return value === undefined ? null : value; })
        .catch(function () { return localGet(key); });
    },
    set: function (key, value) {
      return idb("readwrite", function (s) { return s.put(value, key); })
        .then(function () { return true; })
        .catch(function () { return localSet(key, value); });
    },
    remove: function (key) {
      return idb("readwrite", function (s) { return s.delete(key); })
        .catch(function () {
          try { global.localStorage.removeItem(key); } catch (e) {}
        });
    },
    keys: function () {
      return idb("readonly", function (s) { return s.getAllKeys(); })
        .catch(function () {
          var out = [];
          try {
            for (var i = 0; i < global.localStorage.length; i++) {
              out.push(global.localStorage.key(i));
            }
          } catch (e) {}
          return out;
        });
    }
  };

  // -- GPS ---------------------------------------------------------------

  function locate(timeoutMs) {
    var limit = timeoutMs || 5000;
    return new Promise(function (resolve) {
      if (!global.navigator || !global.navigator.geolocation) {
        return resolve({ unavailable: true });
      }
      var settled = false;
      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        // No fix in time. Record the event anyway — GPS never blocks it.
        resolve({ unavailable: true });
      }, limit);

      global.navigator.geolocation.getCurrentPosition(
        function (position) {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          resolve({
            latitude: position.coords.latitude.toFixed(6),
            longitude: position.coords.longitude.toFixed(6),
            accuracy_m: Math.round(position.coords.accuracy)
          });
        },
        function () {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          resolve({ unavailable: true });
        },
        { enableHighAccuracy: true, timeout: limit, maximumAge: 0 }
      );
    });
  }

  // -- posting, with the CSRF token the page already carries -------------

  function csrf() {
    var field = document.querySelector("[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  function postForm(url, fields) {
    var body = new FormData();
    body.append("csrfmiddlewaretoken", csrf());
    Object.keys(fields).forEach(function (key) {
      if (fields[key] !== undefined && fields[key] !== null) body.append(key, fields[key]);
    });
    return fetch(url, {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" }
    });
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(payload)
    });
  }

  global.A1 = {
    uuid: uuid,
    store: store,
    locate: locate,
    postForm: postForm,
    postJson: postJson
  };
})(window);
