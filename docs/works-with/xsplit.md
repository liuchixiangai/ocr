---
title: XSplit Broadcaster
parent: Works With…
description: "Show ScoreSight's live scoreboard readings in XSplit Broadcaster: a browser overlay in a Webpage source, or Text sources that load ScoreSight's text files."
---

# XSplit Broadcaster
{: .no_toc }

**Works, untested.** XSplit Broadcaster can show a ScoreSight [browser overlay](../overlays.md) in a **Webpage** source, or show each field in a **Text** source that loads one of ScoreSight's [text files](../text-files.md). Both sources are in XSplit's free licence too.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Browser overlay (Webpage source)

A complete scorebug, lower third or clock, updated as soon as a value changes.

1. In ScoreSight, select **HTTP server** in the **Outputs** list. Under **Browser overlays**, choose the **Overlay**, its **Fields** and **Theme**, and the **Address** (`localhost` if XSplit runs on the same computer). Click **Copy**.
2. In XSplit, **Add Source** > **Webpage**, paste the address (for example `http://localhost:18099/overlay/scorebug`) and click **OK**.
3. Size the source to the whole scene (the overlay places itself inside it). In the source's properties, under **Display**, turn on the transparency option if the background isn't transparent.

## Text sources (one per field)

1. In ScoreSight, select **Text files** in the Outputs list, choose a folder, keep **Append** on *Don't append results*, and set **Save / s** to 5–10.
2. In XSplit, **Add Source** > **Text**. Choose **Script**, pick **Load Text from Local File**, and click the gear icon.
3. In the script window, click **…** next to **File Path**, pick for example `Home Score.txt` in that folder, and click **Update Text**.
4. Style the text, and repeat for `Away Score.txt`, `Time.txt` and so on.

XSplit doesn't document how often the script reads the file again. If the clock lags, use the browser overlay.

## Notes

- **Load Text from Remote URL** expects a plain-text URL with one value. ScoreSight's HTTP server has no such address for a single field, so use the files or the overlay.
- Neither route has been tested by the ScoreSight project. If you try one, please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Vendor documentation

- [Webpage source](https://support.xsplit.com/en/article/webpage-1y1l6v)
- [Text source](https://support.xsplit.com/en/article/text-y686sg/) (Script: Load Text from Local File)
