---
title: TriCaster
parent: Works With…
description: "Show ScoreSight's live scoreboard readings in Vizrt TriCaster title pages with DataLink keys set over TriCaster's HTTP API, or as a browser overlay in a Live Link buffer."
---

# TriCaster
{: .no_toc }

**Works, untested.** TriCaster's **DataLink** keys show live values in title pages, also while they are on air: ScoreSight sets one key per field over TriCaster's HTTP API. Or show a ScoreSight [browser overlay](../overlays.md) in a **Live Link** web buffer.

<a href="https://youtu.be/HaWNl92JLBE" target="_blank" rel="noopener"><img src="../videos/13-broadcast.jpg" width="45%" alt="Video: Scoreboard data for CasparCG, Chyron, Vizrt and TriCaster (3:15)"/></a>

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## DataLink keys in title pages

1. In ScoreSight, select **HTTP API** in the Outputs list ([Outbound HTTP API](../out_api.md)) and pick **TriCaster DataLink** in **Preset**. The URL becomes:

   ```
   http://admin:admin@TRICASTER-IP/v1/datalink?key={field}&value={value}
   ```

2. Replace `TRICASTER-IP` with the TriCaster's IP address, and `admin:admin` with its login if you changed it. (ScoreSight uses Basic or Digest authentication, whichever the TriCaster asks for.)
3. Tick **Send out API requests to external services.** ScoreSight now sends `GET /v1/datalink?key=Home%20Score&value=21` and so on: one request per changed field, at most 10 per second.
4. In a TriCaster title, type `%` in a text line and pick the key: **each field is a DataLink key named exactly like the field**, so `%Home Score%` shows the Home Score field and `%Time%` the clock. The HTTP API page lists the keys (*DataLink keys: %Home Score%, %Time%...*). A fallback for an empty key: `%{Home Score, "0"}%`.

To check: open `http://TRICASTER-IP/v1/datalink` in a browser; it lists the DataLink keys and their values.

Unverified: whether DataLink accepts keys with spaces over HTTP. If `%Home Score%` stays empty, tick **Letters, digits and _ only** on the HTTP API page and use `%home_score%`. The highest update rate TriCaster accepts for titles on air isn't documented: lower **Rate** if updates lag.

## Browser overlay in a Live Link buffer

1. In ScoreSight, select **HTTP server** in the Outputs list, set up an overlay under **Browser overlays** with the computer's network address, and click **Copy**.
2. On the TriCaster, hover one of the first 10 buffer slots, click **+** to open the Media Browser, and choose **Web Browser** (Live Link).
3. Paste the overlay address, for example `http://192.168.1.20:18099/overlay/scorebug`.
4. Use the buffer in a DSK or as a key source.

Live Link needs a recent TriCaster software revision (Vizrt's partners cite Rev 7–9 or later). The rendered page keeps its alpha. Untested: how often the buffer refreshes on air.

## DataLink watch folder (untested idea)

A DataLink watch folder reads `key=value` text files. ScoreSight's [Text files](../text-files.md) output can write one (**Save key=value .txt file**: `results.txt`), but the watch folder also sees the per-field `.txt` files, which have no `=`. Prefer the HTTP route above.

## Vendor documentation

- [TriCaster Automation and Integration Guide](https://downloads.newtek.com/LiveProductionSystems/VMC1/Automation%20and%20Integration%20Guide.pdf) (DataLink, HTTP commands, password protection)
- [Live Link](https://downloads.newtek.com/LiveProductionSystems/TCMiniGo/VizTCMini.pdf)
