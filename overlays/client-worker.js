/*
 * ScoreSight overlay client's shared worker, served at
 * /overlay/client-worker.js and started by /overlay/client.js.
 *
 * A browser keeps at most 6 connections open per server, and every live
 * stream holds one: 7 overlay pages in one browser (tabs, or browser sources
 * that share one engine) would starve each other. This worker keeps one
 * /events stream for all the overlay pages of this browser and passes its
 * messages on to each of them:
 *
 *   {type: "snapshot", data, connected}  the current values (also to each
 *                             new page; connected false: kept from a stream
 *                             that is down now)
 *   {type: "change", data}    a change, as /events sends it
 *   {type: "heartbeat"}       the stream is alive
 *   {type: "status", connected}
 *   {type: "error", gotMessage}  the stream failed (gotMessage: it had worked)
 *   {type: "unsupported"}     no EventSource here: the page streams itself
 *
 * A page sends "close" when it goes away. Plain ES5, like client.js.
 */
"use strict";

var SILENCE_MS = 16000;
var FIRST_MESSAGE_MS = 8000;

var ports = [];
var source = null;
var snapshot = null; // {version, fields}, kept up to date
var connected = false;
var gotMessage = false;
var lastActivity = 0;
var reconnectTimer = null;

function now() {
  return new Date().getTime();
}

function broadcast(message) {
  for (var i = 0; i < ports.length; i++) {
    ports[i].postMessage(message);
  }
}

function setConnected(value) {
  if (connected !== value) {
    connected = value;
    broadcast({ type: "status", connected: value });
  }
}

function parse(text) {
  try {
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

function merge(change) {
  var fields = change.fields || {};
  var removed = change.removed || [];
  for (var name in fields) {
    if (Object.prototype.hasOwnProperty.call(fields, name)) {
      snapshot.fields[name] = fields[name];
    }
  }
  for (var i = 0; i < removed.length; i++) {
    delete snapshot.fields[removed[i]];
  }
  snapshot.version = change.version;
}

function activity() {
  lastActivity = now();
  gotMessage = true;
}

function open() {
  clearTimeout(reconnectTimer);
  if (source) {
    source.close();
  }
  gotMessage = false;
  lastActivity = now();
  source = new EventSource("/events"); // the worker's own server
  var current = source;
  source.addEventListener("snapshot", function (event) {
    activity();
    var data = parse(event.data);
    if (data) {
      snapshot = data;
      connected = true; // the snapshot says so itself
      broadcast({ type: "snapshot", data: data, connected: true });
    }
  });
  source.addEventListener("change", function (event) {
    activity();
    var data = parse(event.data);
    if (data && snapshot) {
      merge(data);
      broadcast({ type: "change", data: data });
    }
  });
  source.addEventListener("heartbeat", function () {
    activity();
    broadcast({ type: "heartbeat" });
  });
  source.onerror = function () {
    if (current !== source) {
      return;
    }
    setConnected(false);
    broadcast({ type: "error", gotMessage: gotMessage });
    if (source.readyState === 2) {
      // closed for good: the browser won't retry
      source.close();
      source = null;
      reconnectTimer = setTimeout(open, 3000);
    }
  };
}

setInterval(function () {
  if (source && now() - lastActivity > (gotMessage ? SILENCE_MS : FIRST_MESSAGE_MS)) {
    setConnected(false);
    broadcast({ type: "error", gotMessage: gotMessage });
    open();
  }
}, 2000);

self.onconnect = function (event) {
  var port = event.ports[0];
  if (typeof EventSource === "undefined") {
    port.postMessage({ type: "unsupported" });
    return;
  }
  ports.push(port);
  port.onmessage = function (message) {
    if (message.data === "close") {
      var index = ports.indexOf(port);
      if (index >= 0) {
        ports.splice(index, 1);
      }
      if (!ports.length && source) {
        // no page left (they may come back from the back/forward cache)
        clearTimeout(reconnectTimer);
        source.close();
        source = null;
        snapshot = null;
        connected = false;
      }
    }
  };
  port.start();
  port.postMessage({ type: "status", connected: connected });
  if (snapshot) {
    // the latest values, also while the stream is down (then connected false)
    port.postMessage({ type: "snapshot", data: snapshot, connected: connected });
  }
  if (!source) {
    open();
  }
};
