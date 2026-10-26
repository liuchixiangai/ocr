/*
 * ScoreSight overlay client, served by ScoreSight at /overlay/client.js.
 *
 *   <script src="/overlay/client.js"></script>
 *   <span data-field="Home Score">0</span>
 *
 * Fills every element that has a data-field attribute with that field's last
 * good value, follows the changes live (Server-Sent Events from /events, or
 * polling /overlay/state where EventSource doesn't work) and fires DOM events
 * for custom logic. The full contract is in docs/overlays.md.
 *
 * Plain ES5 on purpose: it also runs in old embedded browsers (CasparCG 2.3's
 * Chromium 71, hardware switchers' web views).
 */
(function () {
  "use strict";

  if (window.ScoreSight && window.ScoreSight.clientVersion) {
    return; // included twice
  }

  var GOOD_STATES = { Success: true, SameNoChange: true };
  // the server sends a heartbeat every 5 s: a stream that is silent for
  // longer than this is dead (a sleeping PC, a pulled cable) and is reopened
  var SILENCE_MS = 16000;
  // a new stream that sends nothing for this long is blocked somewhere
  var FIRST_MESSAGE_MS = 8000;
  // the stream failed this often while /overlay/state answered: poll instead
  var SSE_FAILURES_BEFORE_POLLING = 2;

  var script = document.currentScript || lastScript();

  function lastScript() {
    var scripts = document.getElementsByTagName("script");
    return scripts[scripts.length - 1] || null;
  }

  function pageParam(name) {
    var pairs = window.location.search.replace(/^\?/, "").split("&");
    for (var i = 0; i < pairs.length; i++) {
      var pair = pairs[i].split("=");
      try {
        if (decodeURIComponent(pair[0].replace(/\+/g, " ")) === name) {
          return decodeURIComponent((pair[1] || "").replace(/\+/g, " "));
        }
      } catch (e) {
        // a malformed %-escape: not this parameter
      }
    }
    return null;
  }

  function option(attribute, param, fallback) {
    // the page's URL (?transport=poll) wins over the script tag's attribute
    var value = param ? pageParam(param) : null;
    if (value) {
      return value;
    }
    value = script ? script.getAttribute(attribute) : null;
    return value || fallback;
  }

  function serverBase() {
    var explicit = script ? script.getAttribute("data-server") : null;
    if (explicit) {
      return explicit.replace(/\/+$/, "");
    }
    // the server that served this script (also when the page itself comes
    // from somewhere else, e.g. a file:// template)
    var match = /^(https?:\/\/[^\/?#]+)/i.exec((script && script.src) || "");
    return match ? match[1] : "";
  }

  var base = serverBase();
  var transportOption = option("data-transport", "transport", "auto");
  var pollMs = Math.max(100, parseInt(option("data-poll-ms", "poll", "500"), 10) || 500);
  var changedMs = Math.max(0, parseInt(option("data-changed-ms", null, "1000"), 10) || 0);
  // "false": this page opens its own stream instead of sharing one
  var sharedOption = !/^(false|0|no)$/i.test(option("data-shared", "shared", "true"));

  var api = {
    clientVersion: 1,
    server: base,
    // name -> {value, state, good}
    fields: {},
    connected: false,
    transport: null,
    get: function (name) {
      var field = api.fields[name];
      return field ? field.value : null;
    },
    on: function (type, listener) {
      document.addEventListener("scoresight:" + type, function (event) {
        listener(event.detail, event);
      });
    },
    // fill elements added to the page after the values arrived
    render: function () {
      paintAll(null);
    }
  };
  window.ScoreSight = api;

  var haveSnapshot = false;

  function has(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function fire(type, detail) {
    var event;
    try {
      event = new CustomEvent("scoresight:" + type, { detail: detail });
    } catch (e) {
      event = document.createEvent("CustomEvent");
      event.initCustomEvent("scoresight:" + type, false, false, detail);
    }
    document.dispatchEvent(event);
  }

  // --- the page --------------------------------------------------------------

  function flash(element) {
    if (!changedMs || !element.classList) {
      return;
    }
    element.classList.remove("ss-changed");
    void element.offsetWidth; // restart the CSS animation
    element.classList.add("ss-changed");
    clearTimeout(element._ssChangedTimer);
    element._ssChangedTimer = setTimeout(function () {
      element.classList.remove("ss-changed");
    }, changedMs);
  }

  function paint(element, field, animate) {
    if (element._ssPlaceholder === undefined) {
      // shown until the field has a good value
      element._ssPlaceholder = element.textContent;
    }
    var hasValue = field && field.value !== null && field.value !== undefined;
    var text = hasValue ? String(field.value) : element._ssPlaceholder;
    if (element.textContent !== text) {
      element.textContent = text; // text only, never HTML
    }
    element.setAttribute("data-state", field ? field.state : "Missing");
    element.setAttribute("data-empty", hasValue ? "false" : "true");
    if (animate) {
      flash(element);
    }
  }

  // repaint the elements of the fields in names (null: all), animating those in
  // animated
  function paintAll(names, animated) {
    var elements = document.querySelectorAll("[data-field]");
    for (var i = 0; i < elements.length; i++) {
      var name = elements[i].getAttribute("data-field");
      if (names && !has(names, name)) {
        continue;
      }
      var field = has(api.fields, name) ? api.fields[name] : null;
      if (!field && !haveSnapshot) {
        continue; // not known yet: keep the placeholder
      }
      paint(elements[i], field, !!(animated && animated[name]));
    }
  }

  // --- the data ----------------------------------------------------------------

  function apply(data, isSnapshot) {
    var incoming = (data && data.fields) || {};
    var removed = (data && data.removed) || [];
    var touched = {};
    var animated = {};
    var valueChanges = [];
    var name;

    if (isSnapshot) {
      // a snapshot is complete: the fields it doesn't have are gone
      for (name in api.fields) {
        if (has(api.fields, name) && !has(incoming, name)) {
          removed.push(name);
        }
      }
    }
    for (name in incoming) {
      if (!has(incoming, name)) {
        continue;
      }
      var raw = incoming[name] || {};
      var old = has(api.fields, name) ? api.fields[name] : null;
      var value = raw.value === undefined ? null : raw.value;
      var field = { value: value, state: raw.state || null, good: !!GOOD_STATES[raw.state] };
      api.fields[name] = field;
      if (old && old.value === field.value && old.state === field.state) {
        continue;
      }
      touched[name] = true;
      if (!old || old.value !== field.value) {
        valueChanges.push({
          field: name,
          value: field.value,
          previous: old ? old.value : null,
          state: field.state,
          good: field.good,
          initial: !old,
          removed: false
        });
        if (old && field.value !== null) {
          animated[name] = true;
        }
      }
    }
    for (var i = 0; i < removed.length; i++) {
      name = removed[i];
      if (!has(api.fields, name)) {
        continue;
      }
      var gone = api.fields[name];
      delete api.fields[name];
      touched[name] = true;
      valueChanges.push({
        field: name,
        value: null,
        previous: gone.value,
        state: null,
        good: false,
        initial: false,
        removed: true
      });
    }

    var firstSnapshot = isSnapshot && !haveSnapshot;
    haveSnapshot = haveSnapshot || isSnapshot;
    // the first snapshot also marks the fields that don't exist
    paintAll(firstSnapshot ? null : touched, animated);

    for (var j = 0; j < valueChanges.length; j++) {
      fire("change", valueChanges[j]);
    }
    var changedNames = [];
    for (name in touched) {
      if (has(touched, name)) {
        changedNames.push(name);
      }
    }
    if (changedNames.length || firstSnapshot) {
      fire("update", { fields: api.fields, changed: changedNames, snapshot: isSnapshot });
    }
  }

  function parse(text) {
    try {
      return JSON.parse(text);
    } catch (e) {
      return null;
    }
  }

  function setConnected(connected, transport) {
    var changed = api.connected !== connected || api.transport !== transport;
    api.connected = connected;
    api.transport = transport;
    if (document.documentElement) {
      document.documentElement.setAttribute("data-ss-connected", connected ? "true" : "false");
    }
    if (changed) {
      fire("connection", { connected: connected, transport: transport });
    }
  }

  // --- polling (also the probe when the stream fails) --------------------------

  function fetchState(done) {
    var request = new XMLHttpRequest();
    var finished = false;
    function finish(data) {
      if (!finished) {
        finished = true;
        done(data);
      }
    }
    request.open("GET", base + "/overlay/state", true);
    request.timeout = 5000;
    request.onload = function () {
      finish(request.status === 200 ? parse(request.responseText) : null);
    };
    request.onerror = request.ontimeout = request.onabort = function () {
      finish(null);
    };
    try {
      request.send();
    } catch (e) {
      finish(null);
    }
  }

  var polling = false;

  function startPolling() {
    if (polling) {
      return;
    }
    polling = true;
    (function poll() {
      fetchState(function (data) {
        if (data && data.fields) {
          apply(data, true);
          setConnected(true, "poll");
          setTimeout(poll, pollMs);
        } else {
          setConnected(false, "poll");
          setTimeout(poll, Math.max(pollMs, 2000));
        }
      });
    })();
  }

  // --- Server-Sent Events ------------------------------------------------------
  //
  // Preferably through the shared worker (/overlay/client-worker.js): one
  // stream for all the overlay pages of this browser, because a browser keeps
  // at most 6 connections open per server and each stream holds one. Pages
  // from another origin (a file:// template) can't share it: they open their
  // own stream, as do browsers without shared workers.

  var source = null; // this page's own stream
  var sharedPort = null; // or the shared worker's port
  var sseFailures = 0;
  var lastActivity = 0;
  var lastDataAt = 0; // when the stream last brought values
  var gotMessage = false;
  var reconnectTimer = null;

  function now() {
    return new Date().getTime();
  }

  function closeSource() {
    if (source) {
      source.close();
      source = null;
    }
  }

  function stopShared() {
    if (sharedPort) {
      try {
        sharedPort.postMessage("close");
        sharedPort.close();
      } catch (e) {
        // the worker is gone already
      }
      sharedPort = null;
    }
  }

  function stopStreams() {
    clearTimeout(reconnectTimer);
    stopShared();
    closeSource();
  }

  // the stream failed before it sent anything: is the server up?
  function streamFailed() {
    var asked = now();
    fetchState(function (data) {
      if (!data || !data.fields || polling) {
        return; // ScoreSight isn't reachable: keep trying the stream
      }
      if (lastDataAt >= asked) {
        return; // the stream works after all, and its values are newer
      }
      apply(data, true); // show the values meanwhile
      sseFailures += 1;
      if (transportOption === "auto" && sseFailures >= SSE_FAILURES_BEFORE_POLLING) {
        // the server answers, but its stream doesn't get through
        stopStreams();
        startPolling();
      }
    });
  }

  function received() {
    lastActivity = now();
    if (!gotMessage) {
      gotMessage = true;
      sseFailures = 0;
    }
  }

  // values from the stream (live: not a copy kept while it was down)
  function streamData(data, isSnapshot, live) {
    if (live) {
      lastDataAt = now();
    }
    apply(data, isSnapshot);
  }

  function onMessage(handler) {
    return function (event) {
      received();
      var data = parse(event.data);
      if (data) {
        handler(data);
      }
    };
  }

  function startStream() {
    stopStreams();
    if (polling) {
      return;
    }
    gotMessage = false;
    lastActivity = now();
    try {
      source = new EventSource(base + "/events");
    } catch (e) {
      startPolling();
      return;
    }
    var current = source;
    source.addEventListener("snapshot", onMessage(function (data) {
      streamData(data, true, true);
      setConnected(true, "sse");
    }));
    source.addEventListener("change", onMessage(function (data) {
      streamData(data, false, true);
    }));
    source.addEventListener("heartbeat", onMessage(function () {}));
    source.onerror = function () {
      if (current !== source) {
        return;
      }
      setConnected(false, "sse");
      if (!gotMessage) {
        streamFailed();
      }
      if (source.readyState === 2) {
        // closed for good (e.g. an HTTP error): the browser won't retry
        closeSource();
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(startStream, 3000);
      }
      // otherwise the browser reconnects by itself; the snapshot it gets
      // then brings everything up to date
    };
  }

  // false when this page can't use the shared worker
  function startShared() {
    stopStreams();
    if (polling || !sharedOption || typeof window.SharedWorker === "undefined") {
      return false;
    }
    var worker;
    try {
      worker = new SharedWorker(base + "/overlay/client-worker.js", "scoresight-overlays");
    } catch (e) {
      return false; // e.g. a page from another origin
    }
    var port = worker.port;
    var heard = false;
    sharedPort = port;
    gotMessage = false;
    lastActivity = now();
    function giveUp() {
      if (port === sharedPort && !heard) {
        startStream(); // the worker doesn't run here
      }
    }
    port.onmessage = function (event) {
      if (port !== sharedPort) {
        return;
      }
      var message = event.data || {};
      heard = true;
      lastActivity = now();
      if (message.type === "snapshot") {
        // the worker's copy, also while its stream is down (connected false)
        var live = message.connected !== false;
        if (live) {
          received();
        }
        streamData(message.data, true, live);
        setConnected(live, "sse");
      } else if (message.type === "change") {
        received();
        streamData(message.data, false, true);
      } else if (message.type === "heartbeat") {
        received();
      } else if (message.type === "status") {
        if (!message.connected) {
          setConnected(false, "sse");
        }
      } else if (message.type === "error") {
        setConnected(false, "sse");
        if (!message.gotMessage) {
          streamFailed();
        }
      } else if (message.type === "unsupported") {
        startStream();
      }
    };
    worker.onerror = giveUp; // the worker's script didn't load
    port.start();
    setTimeout(giveUp, 4000);
    return true;
  }

  function startLive() {
    if (!startShared()) {
      startStream();
    }
  }

  function watchdog() {
    if (polling) {
      return;
    }
    var silent = now() - lastActivity;
    if (sharedPort) {
      // the worker passes a heartbeat (or an error) on every few seconds
      if (silent > SILENCE_MS + 4000) {
        setConnected(false, "sse");
        startLive();
      }
      return;
    }
    if (!source) {
      return;
    }
    if (silent > (gotMessage ? SILENCE_MS : FIRST_MESSAGE_MS)) {
      var neverWorked = !gotMessage;
      setConnected(false, "sse");
      startStream();
      if (neverWorked) {
        streamFailed();
      }
    }
  }

  function start() {
    if (transportOption === "poll" || typeof window.EventSource === "undefined") {
      startPolling();
      return;
    }
    startLive();
    setInterval(watchdog, 2000);
    // A page kept in the back/forward cache would keep its stream open (and
    // count against the 6 connections): stop it when the page is hidden,
    // start it again if the page comes back
    window.addEventListener("pagehide", function () {
      stopStreams();
    });
    window.addEventListener("pageshow", function (event) {
      if (event.persisted && !polling) {
        startLive();
      }
    });
  }

  if (document.readyState === "loading") {
    // values that arrive before the page is parsed are painted once it is
    document.addEventListener("DOMContentLoaded", function () {
      paintAll(null);
    });
  }
  setConnected(false, null);
  start();
})();
