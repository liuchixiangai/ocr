---
title: CasparCG
parent: Works With…
description: "Play a live scorebug in CasparCG from ScoreSight's scoreboard readings: ScoreSight's HTML template over AMCP (CG ADD / CG UPDATE), or a browser overlay with the HTML producer."
---

# CasparCG
{: .no_toc }

**Works, untested.** ScoreSight talks to CasparCG Server over AMCP: it loads a template and updates it with every change, using the CasparCG preset of the [TCP / UDP output](../tcp-output.md#casparcg). ScoreSight comes with a ready-made HTML scorebug template. Or play one of ScoreSight's [browser overlays](../overlays.md) with CasparCG's HTML producer.

<a href="https://youtu.be/HaWNl92JLBE" target="_blank" rel="noopener"><img src="../videos/13-broadcast.jpg" width="45%" alt="Video: Scoreboard data for CasparCG, Chyron, Vizrt and TriCaster (3:15)"/></a>

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Template over AMCP (TCP / UDP output)

1. In ScoreSight, select **TCP / UDP** in the Outputs list and pick **CasparCG** in **Preset**.
2. Click **Copy template…** and choose CasparCG's templates folder (the `<template-path>` in `casparcg.config`; the AMCP command `INFO PATHS` shows it). This copies `scoresight_scorebug.html` there.
3. Enter the CasparCG server's address. The port is 5250, CasparCG's AMCP port in its default configuration. Change the **Channel**, **Layer** and **CG layer** (1, 20 and 1) if you need to.
4. Click **Start sending**. ScoreSight sends `CG 1-20 ADD 1 "scoresight_scorebug" 1 "<data>"`, which loads and plays the template, then `CG 1-20 UPDATE 1 "<data>"` with every change.

**Load**, **Play** and **Stop** on the TCP / UDP page send `CG ADD`, `CG PLAY` and `CG STOP` while the output runs. The bundled template shows the scores, the clock, the period, the shot clock and the fouls; edit its CSS to change the look, or use your own template with the same `CG ADD`. The [TCP / UDP page](../tcp-output.md#casparcg) has the data format (templateData XML or JSON), the field names the template recognizes and CasparCG's answers.

## Browser overlay with the HTML producer

From the CasparCG client, or a telnet session to port 5250:

```text
PLAY 1-20 [HTML] "http://192.168.1.20:18099/overlay/scorebug"
```

`STOP 1-20` or `CLEAR 1-20` takes it off. The page sets its own values, so it doesn't use CasparCG's template data. Get the address from ScoreSight's **HTTP server** page (**Browser overlays** > **Copy**).

CasparCG renders HTML with an embedded Chromium (CEF; CasparCG 2.5 has CEF 142, the 2.3 LTS line a much older one). Frames have an alpha channel, so a transparent page keys over video; this hasn't been tested on a real server. For your own HTML template that reads ScoreSight, see [Overlays hosted elsewhere](../overlays.md#overlays-hosted-elsewhere).

## Vendor documentation

- [AMCP protocol](https://casparcg.com/docs/wiki/protocols/amcp-protocol)
- [HTML templates](https://casparcg.com/docs/wiki/media/html-templates)
