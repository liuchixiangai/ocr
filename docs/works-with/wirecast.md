---
title: Wirecast
parent: Works With…
description: "Show ScoreSight's live scoreboard readings (scores, clock, period) in Telestream Wirecast: a browser overlay in a Web Page source, or Text sources that read ScoreSight's text files."
---

# Wirecast
{: .no_toc }

**Works, untested.** Telestream Wirecast can show ScoreSight's readings in two ways: a ready-made [browser overlay](../overlays.md) in a **Web Page** source, or Wirecast's own **Text** sources reading the [text files](../text-files.md), one file per field.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Browser overlay (Web Page source)

A complete scorebug, lower third or clock, updated as soon as a value changes. Web Page sources are in Wirecast Studio and Pro.

1. In ScoreSight, select **HTTP server** in the **Outputs** list. Under **Browser overlays**, choose the **Overlay**, its **Fields** and **Theme**, and the **Address**: the network address of the ScoreSight computer if Wirecast runs on another computer, `localhost` if it runs on the same one. Click **Copy**.
2. In Wirecast, **Add Shot** > **Web Page**.
3. Paste the address as the **URL**, for example `http://192.168.1.20:18099/overlay/scorebug`. Set **Width** and **Height** to your canvas (1920 × 1080) and tick **Transparent Background**.
4. Put the shot on a layer above your camera.

If Wirecast runs on another computer, allow ScoreSight through the ScoreSight computer's firewall (TCP port 18099). Wirecast uses WebView2 on Windows (since 14.x). **Shutdown when inactive** is fine: the overlay gets the current values when it comes back.

## Text sources (one per field)

Wirecast's Text source can keep reading a text file, so it shows the per-field `.txt` files of the Text files output, in Wirecast's own fonts and styles.

1. In ScoreSight, select **Text files** in the Outputs list, choose a folder, keep **Append** on *Don't append results*, and set **Save / s** to 5–10.
2. In Wirecast, add a **Text** source to a shot layer, set **Mode** to **File**, pick for example `Home Score.txt` in that folder, and tick **Keep reading text file continuously**.
3. Style it (font, size, colour, alignment) and position it. Repeat for `Away Score.txt`, `Time.txt`, `Period.txt` and so on. Put a background image under the text layers to build a scorebug shot.

Each field needs its own Text source. The text changes at the **Save / s** rate plus Wirecast's own file polling: expect up to about a second. ScoreSight replaces each file in one step, so Wirecast never reads a half-written value.

## Notes

- Wirecast's built-in Scoreboard source can't take data from outside.
- Neither route has been tested by the ScoreSight project. If you try one, please tell us how it went in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Vendor documentation

- [Wirecast User Guide](https://www.telestream.net/pdfs/user-guides/wirecast/Wirecast-16-User-Guide-Windows.pdf): the Text source (File mode) and the Web Page source.
