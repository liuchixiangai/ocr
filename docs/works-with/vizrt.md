---
title: Vizrt Viz Engine
parent: Works With…
description: "Send ScoreSight's live scoreboard readings to Vizrt Viz Engine scenes through Shared Memory (TCP or UDP key|value pairs), Vizrt Datacenter or Viz Flowics."
---

# Vizrt Viz Engine
{: .no_toc }

**Partly, untested.** Viz Engine's **Shared Memory** input takes `key|value` pairs over TCP or UDP, which the Vizrt preset of the [TCP / UDP output](../tcp-output.md#vizrt-viz-engine) sends. Vizrt Datacenter and Viz Flowics are other routes, with their own licences.

<a href="https://youtu.be/HaWNl92JLBE" target="_blank" rel="noopener"><img src="../videos/13-broadcast.jpg" width="45%" alt="Video: Scoreboard data for CasparCG, Chyron, Vizrt and TriCaster (3:15)"/></a>

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Shared Memory (TCP / UDP output)

ScoreSight sends a pair per changed field, each ended by a NUL character:

```
HomeScore|21␀Time|3:12␀
```

1. In **Viz Configuration › Communication › Shared Memory**, set a **TCP Port** (or **UDP Port**). Vizrt recommends no particular port.
2. In ScoreSight, select **TCP / UDP** in the Outputs list, pick **Vizrt (Shared Memory)** in **Preset**, enter the Viz Engine computer's address and that port, and choose TCP or UDP. Vizrt recommends keeping a TCP connection open, which ScoreSight does.
3. The preset sends names without spaces (`HomeScore`, `ShotClock`). To give the keys a path, edit the template, for example `scoresight/{field}|{value}`.
4. In the scene, read the values from the shared memory map, for example with the **ApplySharedMemory** plugin and **Control** plugins whose field identifiers are the names (with the path as the memory base key).
5. Click **Start sending**.

Engines keep these values only while they run: turn on **Resend all every** so that an engine started later gets them. A connection must be made to each Viz Engine.

## Vizrt Datacenter

With a Datacenter licence:

- a **REST** dataset can poll `http://<scoresight-ip>:18099/json?pivot&all` (the [HTTP server](../http_server.md));
- the **Socket Listener** can take the [TCP / UDP output](../tcp-output.md#custom-protocols) with the **Custom** preset: UDP or TCP port 6000, **One message with every field** and the template `{json}`.

Both are untested.

## Viz Flowics

Viz Flowics graphics can take ScoreSight's values over HTTP: see [Viz Flowics](viz-flowics.md).

## Vendor documentation

- Viz Engine Guide, *External Data Input* (Shared Memory)
- [Vizrt Datacenter REST dataset](https://documentation.vizrt.com/datacenter-user-guide/2.5/Rest_Webserver_Dataset.html)
