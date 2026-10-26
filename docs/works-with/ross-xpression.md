---
title: Ross XPression
parent: Works With…
description: "Feed ScoreSight's live scoreboard readings into Ross Video XPression with a DataLinq source that polls ScoreSight's HTTP server or reads its CSV file."
---

# Ross XPression
{: .no_toc }

**Works, untested.** XPression's **DataLinq** reads ScoreSight's values over HTTP (or from a file) and updates text objects live. It needs XPression's DataLinq option and a running DataLinq Server (DataLinq also works in the XPression Trial Edition).

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## DataLinq over HTTP

1. In ScoreSight, give the fields their final names and note the computer's IP address. The [HTTP server](../http_server.md) listens on port 18099.
2. In **XPression DataLinq Server**, choose **Add New** > **RSS / HTTP DataLinq Source**:
   - method GET, URL `http://<scoresight-ip>:18099/csv?pivot&all`, **Format** *Text*;
   - **Delimiter** *Comma*, tick **Text File has Header Row**;
   - **Update Interval** 250–500 ms, **Mode** *Static URL*.
3. Name the source (for example *ScoreSight*) and use **Browse** to check that it shows one row with a column per field.
4. In XPression, for each text object: **Object Inspector** > **Data Source** > **DataLinq** > **Set**, tick **Enabled**, pick the ScoreSight source, browse to the column (for example *Home Score*) in row 1, and tick **Live Update**.

`&all` keeps every column in the data, with its last good value, so a column never disappears while a reading is rejected. Instead of CSV you can use `/json?pivot&all` with **Format** *JSON*, or `/xml?pivot&all` with *XML*.

## DataLinq from a file

Tick **Save .csv file** in the [Text files](../text-files.md) output and add a **Text DataLinq Source** on `results.csv`: tick the header row, poll every 1 s, and tick **Allow reading of files that are currently being written**. The file's first column is `Timestamp`.

## RossTalk (untested idea)

The [TCP / UDP output](../tcp-output.md#custom-protocols) can send RossTalk's `TEMPLATEDATA` command to TCP port 7788 with the **Custom** preset: `TEMPLATEDATA <take id>:{field}:Text:{value}`, CR LF, a message per field. Ross documents `TEMPLATEDATA` with a property name, but whether the text property is called `Text`, and how a value with colons (a clock such as `04:30`) is handled, isn't documented. Prefer DataLinq.

## Notes

XPression updates as often as it polls; ScoreSight can't trigger takes or animations.

## Vendor documentation

- [XPression User Guide 12.6](https://documentation.rossvideo.com/files/Manuals/CG%20and%20Graphics/XPression/User%20Guides/XPression%20User%20Guide%20%283500DR-001-12.6%29.pdf) (DataLinq sources)
- [DataLinq and Scoreboard Sources](https://documentation.rossvideo.com/files/Manuals/CG%20and%20Graphics/XPression/Application%20Notes/XPression%20DataLinq%20and%20Scoreboard%20Sources%20%283500DR-550-01%20Rev%203%29.pdf)
