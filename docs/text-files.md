---
title: Text, CSV & XML Files
parent: Outputs
nav_order: 1
description: "Write each field to a .txt file, plus results.csv, results.xml, results.json and a key=value results.txt, for software that reads text files."
---

# Text, CSV & XML Files
{: .no_toc }

The **Text files** output writes the readings to files in a folder. Almost any broadcast or office software can read them: OBS (*Text (GDI+/FreeType 2)* > *Read from file*), Wirecast, vMix Data Sources, NewBlue Captivate, Ross XPression, SuiteCG, CharacterWorks, PlayBox TitleBox, Excel and more (see [Works With…](integrations.md)).

<a href="https://youtu.be/tKA6UBqcDC0" target="_blank" rel="noopener"><img src="videos/08-outputs.jpg" width="45%" alt="Video: Outputs: the built-in HTTP server, JSON and text files (3:23)"/></a>

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Setup

1. Select **Text files** in the **Outputs** tab on the right (or click **Files** in the status bar). (In ScoreSight 0.1.x this was the **Text Files** tab.)
2. Click **Open** next to **Folder** and choose an output folder. **Clear** stops file output. The switch of the Text files row does the same: turning it on asks for a folder, turning it off clears it.
3. Optionally tick **Save .csv file**, **Save .xml file**, **Save .json file** and/or **Save key=value .txt file**.
4. Choose an **Append** mode and a **Save / s** rate (see below).

The Text files row then shows ● *Writing to D:\Scores · 4 files* and when a file was last written (files are written when a value changes, so a few minutes is normal for a score). Before the first reading it shows ◐ *Waiting for readings*.

## Files written

| File | Written when | Content |
|---|---|---|
| `<Box Name>.txt` (for example `Home Score.txt`) | Always, once a folder is set | The latest good value of that box |
| `results.csv` | **Save .csv file** is ticked | A `Timestamp` column followed by one column per box |
| `results.xml` | **Save .xml file** is ticked | Every box with its name, text, state and position |
| `results.json` | **Save .json file** is ticked | One JSON object: every box with its last good value |
| `results.txt` | **Save key=value .txt file** is ticked | A `Box Name=value` line per box |

How the files are written:

- **Only good readings** (green `Success` or light green `SameNoChange`) are written. Rejected (yellow) and empty readings never overwrite a file, so it keeps the last good value when, say, the board blanks out for a moment or someone walks in front of the camera.
- **Only on change**: a file is written when its content would differ from what ScoreSight last wrote to it, or when the file is missing (for example after you emptied the folder). Unchanged values don't touch the file.
- **Never lost**: a value that changes between two writes (see [Save / s](#save--s)) is written at the next allowed moment, so the last change always reaches the file.
- **Atomic**: files that are overwritten are replaced in one step. ScoreSight writes a temporary file in the same folder and swaps it in, so programs that poll the file never see it half-written or empty.
- **UTF-8**: all files are UTF-8 encoded.

{: .new }
ScoreSight 0.2.0 rewrote file output. Writes are state-based and atomic, the CSV header appears only once, and the XML is always well-formed. This fixes missing `.txt` files ([#6](https://github.com/royshil/scoresight/issues/6)) and makes `results.csv` usable as a live data file ([#29](https://github.com/royshil/scoresight/issues/29)). Clicking a box or changing a box setting no longer empties `results.csv`.

### Per-box `.txt` files

- A file contains just the value, as plain text, without a line break.
- In an append mode (see below), each new value is added on its own line instead. A line is added only when the value differs from the last line in the file.
- The first time ScoreSight writes a file in a session, it compares with what is already on disk, so restarting ScoreSight doesn't rewrite or append an unchanged value.
- Characters that aren't allowed in file names (`< > : " / \ | ? *`) are replaced with `_` in the file name. A box named *Home/Away* writes `Home_Away.txt`.

### `results.csv`

A spreadsheet-friendly table with a timestamp and one column per box, for example:

```csv
Timestamp,Home Score,Away Score,Time,Period
2026-10-10T19:42:07.512345,65,39,4:27,2nd
```

- The header row is written once at the top of the file. Values are quoted correctly when they contain commas or quotes.
- `Timestamp` is the local date and time of the write (ISO 8601).
- A box that hasn't had a good reading yet has an empty cell. Otherwise each cell holds the box's last good value.
- Without an append mode, the file holds the header and one row, and it is rewritten when a value changes or a box is added, removed or renamed.
- In an append mode, a row is added each time a value changes (not on every save). If the boxes change (added, removed or renamed), the existing file is renamed to `results-<YYYYmmdd-HHMMSS>.csv` and a new `results.csv` with a new header is started, so every file is a consistent table. A file from ScoreSight 0.1.x (with repeated header rows) is kept the same way.

### `results.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<data>
  <result>
    <name>Home Score</name>
    <text>65</text>
    <state>SameNoChange</state>
    <rect>
      <x>525.9</x>
      <y>638.4</y>
      <width>214.6</width>
      <height>175.3</height>
    </rect>
  </result>
  <result>
    <name>Time</name>
    <text>4:27</text>
    <state>Success</state>
    <rect>
      <x>843.6</x>
      <y>663.0</y>
      <width>359.1</width>
      <height>207.2</height>
    </rect>
  </result>
</data>
```

- One `<result>` element per box, in the order of the boxes.
- `<text>` is the box's last good reading (empty if it has none yet), and `<state>` is the state of that reading (`Success` or `SameNoChange`). For a box without a good reading, `<state>` is its current state.
- `<rect>` is the box position in pixels on the processed picture.
- The file is rewritten when a text or a box position changes. Text is escaped, so values such as `<` or `&` always produce valid XML.

### `results.json`

```json
{
  "Home Score": "65",
  "Away Score": "39",
  "Time": "4:27",
  "Period": ""
}
```

- One flat object with every box (in the order of the boxes) and its last good value; an empty string for a box without a good reading yet.
- For programs that read one JSON file with all the fields, such as SuiteCG's Data Automation.
- Rewritten (atomically) when a value changes, or a box is added, removed or renamed. It isn't affected by the append mode.

### `results.txt` (key=value)

```text
Home Score=65
Away Score=39
Time=4:27
Period=
```

- A `Box Name=value` line per box, with its last good value (empty before the first). The first `=` ends the name: an `=` in a box name is written as `_`, and line breaks in a name or a value become spaces.
- For programs that read `key=value` text files: SuiteCG's Data Automation (TXT), a TriCaster DataLink watch folder (untested: the watch folder also sees the per-box `.txt` files, which have no `=`) and similar.
- Rewritten (atomically) when a value changes, or a box is added, removed or renamed. It isn't affected by the append mode.
- A box named *results* writes its own `results.txt` (its value only). The key=value file is then not written, and the Text files row says why: rename that box.

## Append modes

The **Append** drop-down chooses which files keep a history instead of only the latest value:

| Append mode | `.txt` files | `results.csv` |
|---|---|---|
| **Don't append results** (default) | Latest value only | Header + one row with the latest values (a live snapshot) |
| **Results in .csv file** | Latest value only | A row is added each time a value changes (a log) |
| **Results in .txt files** | A line is added each time the value changes | Header + one row with the latest values |
| **Results in both** | A line per change | A row per change |

Use **Don't append results** for live graphics, and an append mode to log a whole game for later analysis.

## Save / s

**Save / s** (1–10, default 5) limits how many times per second the files are updated. Lower it if the reading software struggles with frequent changes, and raise it for fast clocks. It affects only the files: OBS, vMix, UNO, the HTTP server and the outbound API are not throttled by it.

## Errors

- If a file can't be written (permissions, a full disk, a file locked by another program), the Text files row shows ■ *Can't write Home Score.txt* (hover it for the error), the log shows `Error writing to <path>: <error>` once, and ScoreSight tries again at the next save. When it works again, the row turns ● and the log shows `Writing to <path> works again`.
- If the output folder is deleted while ScoreSight is running (or is missing when it starts), ScoreSight logs `Output folder does not exist`, clears the folder setting and stops writing files. The Text files row shows ■ *Folder not found: ...* until you choose a folder again.

## Using the files

- **OBS**: add a *Text (GDI+)* (Windows) or *Text (FreeType 2)* source and enable **Read from file**, pointing at `Time.txt`, for example. To update OBS without files, see [OBS Studio](obs.md).
- **vMix**: add the CSV or XML file as a **Data Source** (Settings > Data Sources) and bind title fields to its columns. Alternatively, use the direct [vMix integration](vmix.md).
- **Wirecast**: add a *Text* source in *File* mode with **Keep reading text file continuously** (see [Wirecast](works-with/wirecast.md)).
- **SuiteCG**: point Data Automation at `results.json` or `results.txt` (see [SuiteCG](works-with/suitecg.md)).
- **CharacterWorks**: point a Key-Value data source at `results.txt` (see [CharacterWorks](works-with/characterworks.md)).
- **PlayBox TitleBox**: link each text object to a field's `.txt` file (see [PlayBox TitleBox](works-with/playbox-titlebox.md)).
- **Others**: see [Works With…](integrations.md) and [Other Software](other-software.md).

{: .tip }
Keep the output folder on a local disk. Network shares and cloud-synced folders (OneDrive, Dropbox, iCloud) can delay or lock files.
