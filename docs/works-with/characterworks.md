---
title: CharacterWorks
parent: Works With…
description: "Feed ScoreSight's live scoreboard readings into CharacterWorks graphics with a Key-Value data source: over TCP from ScoreSight's TCP output, from its key=value text file, or by polling its HTTP server."
---

# CharacterWorks
{: .no_toc }

**Works, untested.** A CharacterWorks **Key-Value Data Source** takes `Key=Value` lines, which ScoreSight can send over TCP or write to a file, and with **Auto-Update** it pushes each change straight to the graphics on air. Data sources are built into CharacterWorks (no extra module).

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Over TCP (recommended)

CharacterWorks' Key-Value source can listen for TCP connections ("server" role) and read UTF-8 text with a `Key=Value` entry per line. ScoreSight's [TCP / UDP output](../tcp-output.md) connects to it and sends a line per changed field.

1. In CharacterWorks, create a **Key-Value Data Source** in the Data Sources Browser. In its Data Editor, under **Connection**, choose TCP in the server role and set a port, for example `5300`.
2. Turn on **Auto-Update** for the source, so that changes reach the live layers without pressing Update.
3. Assign the source to a text layer: drag it from the Data Sources Browser to **Attributes** > **Data Sources Box** (or right-click the box > **Assign**). In the text, insert the field, for example `<data field="HomeScore"/>`. Field names are case-sensitive.
4. In ScoreSight, select **TCP / UDP** in the Outputs list and pick **Custom** in **Preset**. Enter the CharacterWorks computer's address and the port from step 1, and set:
   - **Send**: *A message per changed field*, template `{field}={value}`;
   - **Line ending** *LF*, **Encoding** *UTF-8*.
5. In the names table, give each field the key you used in CharacterWorks, for example `HomeScore` for *Home Score* (names without spaces avoid any doubt about spaces in `<data field="..."/>`).
6. Click **Start sending**, and turn on **Resend all every** (for example 10 s) if CharacterWorks may be restarted while ScoreSight runs.

The Key-Value source merges new values and never deletes a key, so a field that has no new reading keeps its last value.

The other way around works too: set the Key-Value source's connection to the TCP **client** role with the ScoreSight computer's address and a port, and choose **TCP server** with that port in ScoreSight (see [TCP server](../tcp-output.md#tcp-server-programs-connect-to-scoresight)). CharacterWorks then connects to ScoreSight and gets every value on each connection.

## From ScoreSight's key=value file

ScoreSight's `results.txt` is exactly the file format of a Key-Value source: a `Home Score=65` line per field.

1. In ScoreSight, select **Text files** in the Outputs list, choose a folder and tick **Save key=value .txt file** ([Text files](../text-files.md#resultstxt-keyvalue)).
2. In CharacterWorks, point the Key-Value source's file at `results.txt` in that folder (a local file), turn on **File Tracking** (re-reads the file when it changes) and **Auto-Update**.
3. Bind the text layers as in step 3 above, with the field names as ScoreSight writes them, for example `<data field="Home Score"/>`.

ScoreSight replaces the file in one step (a temporary file renamed over it). Whether File Tracking notices that kind of change hasn't been tested: if values don't update, also turn on **Retrieve Periodically** with an interval of one second.

## Polling ScoreSight's HTTP server

A Key-Value or XML/JSON source can also read a URL periodically: `http://<scoresight-ip>:18099/xml?pivot&all` (the [HTTP server](../http_server.md)) gives an element per field (`<HomeScore>65</HomeScore>`), which CharacterWorks reads with an XQuery. The right query (for example `/data`) is an educated guess; check the keys in the Data Editor.

## Notes

- CharacterWorks' Remote Control Server (HTTP POST of JSON commands such as `set_text`, port 5201) needs a command format that ScoreSight's outputs don't produce: use a data source.
- None of these routes has been tested by the ScoreSight project. If you try one, please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Vendor documentation

- [Key-Value Data Source](http://chrworks.com/help/doc/KeyValueDataSource.html) (file, TCP/UDP, Auto-Update)
- [Data Sources](http://chrworks.com/help/doc/DataSource.html) (assigning a source, `<data field="..."/>`)
- [Updating Data Sources](http://chrworks.com/help/doc/UpdatingDataSources.html)
- [XML/JSON Data Source](http://chrworks.com/help/doc/XMLJSONDataSource.html)
