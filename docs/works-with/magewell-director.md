---
title: Magewell Director
parent: Works With…
description: "Show ScoreSight's live scoreboard readings on a Magewell Director as a browser overlay in a Webpage GFX, or drive its built-in scoreboard's scores over the Director API."
---

# Magewell Director
{: .no_toc }

**Works, untested.** The Director's **Webpage GFX** shows a web page over the video with a transparent background, so it can show ScoreSight's [browser overlays](../overlays.md) (built-in or your own). Its built-in scoreboards can also take the scores over HTTP, with limits.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Browser overlay in a Webpage GFX

1. Put the Director and the ScoreSight computer on the same network, and allow TCP port 18099 through the ScoreSight computer's firewall.
2. In ScoreSight, select **HTTP server** in the Outputs list, set up an overlay under **Browser overlays** with the computer's network address, and click **Copy**.
3. On the Director, tap **GFX** > **Create New** > **Webpage**, enter the overlay address (for example `http://192.168.1.20:18099/overlay/scorebug`) and tap **Go**.
4. In the GFX editor, set **CSS** to **Transparent CSS**, size and position it, save, and apply it in the show.

A Director holds up to 7 webpage GFX. The page runs in the device's own browser: ScoreSight pushes each change over one connection, which costs the device far less than polling, so keep the overlay's default `transport`. Untested: how fast the device's browser updates.

## Built-in scoreboard scores (untested idea)

The Director's built-in scoreboards have an HTTP API with one call per field. With `{field}` and `{value}` in the URL, the [Outbound HTTP API](../out_api.md) can drive the **scores**: name two custom boxes `setScoreOfHome` and `setScoreOfGuest`, and set the URL to `http://<director-ip>:8080/V1.0/gfx/scoreboard/{field}?score={value}` with **Only changes** ticked.

Every other field is sent the same way too (and fails), so this suits a setup with just the two scores. The clock and the period take other parameters, which this doesn't cover.

## Vendor documentation

- [Director API](https://www.magewell.com/api-docs/director-api)
- [Director user manual](https://www.magewell.com/files/documents/User_Manual/User_Manual_for_Director_en_US.pdf)
