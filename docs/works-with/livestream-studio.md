---
title: Livestream Studio
parent: Works With…
description: "Show ScoreSight's live scoreboard readings in Vimeo Livestream Studio 6 graphics with a .CSV over HTTP layer that reads ScoreSight's HTTP server, or a CSV file layer."
---

# Livestream Studio
{: .no_toc }

**Works, untested.** Livestream Studio's GFX can read a CSV file over HTTP and refresh it every few seconds. ScoreSight's [HTTP server](../http_server.md) serves its values as a one-row CSV with a header, which fits.

Livestream Studio 6 is no longer sold on its own: Vimeo includes it with its Advanced, Premium and Enterprise plans, for Windows and Mac.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## .CSV over HTTP

1. In ScoreSight, check in a browser on the Livestream Studio computer that `http://<scoresight-ip>:18099/csv?pivot&all` shows a header row with the field names and a row of values.
2. In Livestream Studio's GFX panel, **Create Custom Graphic** > **.CSV over HTTP**. Enter that URL and press Enter. Leave **Basic Authentication** off.
3. Tick **Use first row as column titles**, and set **Auto advance data interval** (how often, in seconds, Studio reads the URL again) to 1.
4. Click the paintbrush to open the **GFX Designer**, choose the CSV layer, and insert the fields (*Home Score*, *Away Score*, *Time*...) into the text.
5. Preview with **PRV**, then **PUSH** the graphic on air.

`&all` keeps every column in the CSV, with its last good value, so a field never goes blank while a reading is rejected.

## CSV file layer

Instead of HTTP, tick **Save .csv file** in ScoreSight's [Text files](../text-files.md) output (no append, so the file holds the header and one row), then in Studio **Create Custom Graphic** > **Import Layer** > **.CSV File**, pick `results.csv` and tick **Watch file for update**. Its first column is `Timestamp`.

## Notes

- Untested: whether Studio refreshes faster than once a second, and whether a graphic on air updates without being pushed again. For a fast clock, try the browser overlay instead: GFX Designer's webpage element can show a ScoreSight [browser overlay](../overlays.md) address (whether it keeps the transparency isn't documented).
- Untested by the ScoreSight project. If you try it, please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Vendor documentation

- [Use a CSV over HTTP for Livestream Studio graphics](https://help.vimeo.com/hc/en-us/articles/31111739866257-Use-a-CSV-Over-HTTP-for-Livestream-Studio-Graphics)
- [Import Excel spreadsheets and CSV files](https://help.vimeo.com/hc/en-us/articles/31111893237009-Import-Excel-Spreadsheets-and-CSV-Files)
- [Display a webpage in Livestream Studio](https://help.vimeo.com/hc/en-us/articles/31111758345105-Display-a-Webpage-in-Livestream-Studio)
- [About Livestream Studio](https://help.vimeo.com/hc/en-us/articles/12426924875665-About-Livestream-Studio)
