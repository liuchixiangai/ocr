---
title: Other Software
parent: Outputs
nav_order: 8
description: "Use ScoreSight with NewBlue Captivate/Titler Live, CasparCG, Streamlabs, Home Assistant, Node-RED and anything that reads files or JSON."
---

# Other Software

ScoreSight has built-in integrations for [OBS](obs.md), [vMix](vmix.md) and [overlays.uno](uno.md). Other tools can use one of the generic outputs. For broadcast, streaming and graphics products (Wirecast, XSplit, TriCaster, CasparCG, Chyron, Ross XPression, Vizrt, CharacterWorks, NewBlue Captivate, PlayBox TitleBox and more), see [Works With…](integrations.md): 33 products, with a setup guide for each one that has a documented way in.

| If your software can... | Use |
|---|---|
| read a text, CSV or XML file | [Text, CSV & XML Files](text-files.md) |
| poll a URL for JSON, XML or CSV | [HTTP Server API](http_server.md) (`http://<scoresight-ip>:18099/json?pivot&all`) |
| show a web page (browser source, HTML template) | The [browser overlays](overlays.md) (built-in or your own HTML, pushed live) |
| receive webhooks / HTTP requests | [Outbound HTTP API](out_api.md) |
| take text lines over a TCP connection or UDP | [TCP / UDP output](tcp-output.md) (presets for CasparCG, Chyron PRIME, Live Score, MagicSoft CG and Vizrt) |
| connect to a TCP server and read text lines | The [TCP / UDP output](tcp-output.md#tcp-server-programs-connect-to-scoresight) as a **TCP server** |
| take messages on a WebSocket (`ws://`, `wss://`) | The [TCP / UDP output](tcp-output.md#websocket-scoresight-connects-to-a-url) with the **WebSocket (JSON)** preset |

## NewBlue Captivate / Titler Live

See [NewBlue Captivate](works-with/newblue-captivate.md) (Titler Live is its former name): ScoreSight pushes the values with the [Outbound HTTP API](out_api.md), or Captivate's JSON/XML readers poll the [HTTP server](http_server.md).

## CasparCG

See [CasparCG](works-with/casparcg.md): the [TCP / UDP output](tcp-output.md#casparcg)'s CasparCG preset loads ScoreSight's scorebug template over AMCP and updates it, or CasparCG's HTML producer plays a [browser overlay](overlays.md#casparcg).

## Streamlabs Desktop and other OBS-like apps

Use a **Text** source with "read from file" pointing at a field's `.txt` file, or a **Browser Source** with an HTML overlay as above.

## Home Assistant, Node-RED and automation

- **Home Assistant**: a REST sensor on `/json?pivot` (see the [example](http_server.md#home-assistant-rest-sensor)) turns any scoreboard value into a sensor you can automate on, for example to switch lights at the final buzzer.
- **Node-RED / n8n**: either poll `/json` with an HTTP request node, or let ScoreSight push to an HTTP-in node with the [Outbound HTTP API](out_api.md). In Node-RED, a **tcp in** node set to *Connect to* the [TCP server](tcp-output.md#tcp-server-programs-connect-to-scoresight) gets each change as a line too.

## Spreadsheets and statistics

Choose the **Results in .csv file** append mode in the [Text Files](text-files.md#append-modes) output to log every change during a game into `results.csv`, ready for Excel, Google Sheets or pandas.

{: .tip }
Built an integration with another tool? Share it in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell) or contribute a page to these docs. See [CONTRIBUTING.md](https://github.com/royshil/scoresight/blob/main/CONTRIBUTING.md).
