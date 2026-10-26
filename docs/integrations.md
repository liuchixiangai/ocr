---
title: Works With…
nav_order: 5
has_children: true
description: "Which broadcast, streaming and graphics products ScoreSight's live scoreboard data works with (vMix, OBS, overlays.uno, Wirecast, XSplit, TriCaster, CasparCG, Chyron, Ross XPression, Vizrt, CharacterWorks, NewBlue Captivate, PlayBox TitleBox and more), with a setup guide for each."
---

# Works With…
{: .no_toc }

ScoreSight's readings can reach most broadcast graphics, streaming and production products through one of its [outputs](outputs.md): a built-in integration, the [text files](text-files.md), the [HTTP server](http_server.md) that products poll, the [browser overlays](overlays.md) that products show as a web page, the [outbound HTTP API](out_api.md) that pushes to products, or the [TCP / UDP output](tcp-output.md). This page lists 33 products, says how well each works today, and links to a setup guide for each one that has a documented way in.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

{: .important }
**Untested** means the setup follows the product's documentation but the ScoreSight project hasn't tried it on that product yet. **Untested idea** means the vendor documents the mechanism but not every detail, so expect to adjust something. If you try one, please tell us how it went (or what had to change) in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell), so we can mark it tested.

## At a glance

| Product | Status | How | Guide |
|---|---|---|---|
| **CasparCG** | Works, untested | The [TCP / UDP output](tcp-output.md#casparcg)'s CasparCG preset (AMCP, with ScoreSight's scorebug template); or a browser overlay with the HTML producer | [CasparCG](works-with/casparcg.md) |
| **CharacterWorks** | Works, untested | A Key-Value data source fed by the TCP / UDP output, by `results.txt` or by the HTTP server | [CharacterWorks](works-with/characterworks.md) |
| **Chyron LYRIC / LyricX** | Untested idea | Intelligent Interface over Telnet, with numbered fields | [Below](#chyron-lyric--lyricx) |
| **Chyron PRIME** | Works, untested | The TCP / UDP output's Chyron preset (Intelligent Interface `P\UPDATE`); or a Data object polling `/json?pivot&all` | [Chyron PRIME](works-with/chyron-prime.md) |
| **ClassX LiveBoard** | Partly, untested | A DataSource on `results.csv` or `/csv?pivot&all` with automatic updates; or a web browser input showing an overlay | [Below](#classx-liveboard) |
| **DELTACAST DELTA-cg** | Not yet | No published data input | [Below](#deltacast-delta-cg) |
| **Epiphan Pearl** | Works, untested | Outbound API preset **Epiphan Pearl** (custom variables); or a browser overlay in Web Graphics | [Epiphan Pearl](works-with/epiphan-pearl.md) |
| **Evoux CG Station** | Not yet | No published data input | [Below](#evoux-cg-station) |
| **Grafster** (Converge Horizons) | Not yet | Its text feed protocol isn't published | [Below](#grafster) |
| **INTV Atlas Sport** | Not yet | No published data input | [Below](#intv-atlas-sport) |
| **Live Score** (live-score-app.com) | Works, untested | The TCP / UDP output's Live Score preset | [Live Score](works-with/live-score.md) |
| **Livestream Studio** (Vimeo) | Works, untested | A *.CSV over HTTP* graphics layer reading `/csv?pivot&all` | [Livestream Studio](works-with/livestream-studio.md) |
| **Magewell Director** | Works, untested | A browser overlay in a Webpage GFX | [Magewell Director](works-with/magewell-director.md) |
| **MagicSoft CG** | Works, untested | The TCP / UDP output's MagicSoft preset (`[name]=value`) | [MagicSoft CG](works-with/magicsoft-cg.md) |
| **Navis Media** sport clients | Through CasparCG | ScoreSight's CasparCG route on another layer | [Below](#navis-media) |
| **Nemo3D** | Untested idea | A delimited-text data source on `results.csv` | [Below](#nemo3d) |
| **NewBlue Captivate** | Works, untested | Outbound API push to Captivate's Simple Variable Update; or Captivate's JSON/XML reader polling ScoreSight | [NewBlue Captivate](works-with/newblue-captivate.md) |
| **NewBlue Titler Live** | Works, untested | Renamed NewBlue Captivate in 2023: same setup | [NewBlue Captivate](works-with/newblue-captivate.md) |
| **OBS Studio** | Works | Built-in OBS output (WebSocket); or a browser overlay in a Browser Source; or *Text (Read from file)* | [OBS Studio](obs.md) |
| **overlays.uno** | Works | Built-in UNO output | [Overlays.uno](uno.md) |
| **PlayBox TitleBox** | Works, untested | Text objects linked to the per-field `.txt` files | [PlayBox TitleBox](works-with/playbox-titlebox.md) |
| **Ross XPression** | Works, untested (needs the DataLinq option) | DataLinq RSS/HTTP source polling `/csv?pivot&all`; or a Text DataLinq source on `results.csv` | [Ross XPression](works-with/ross-xpression.md) |
| **RT Software Swift** | Not yet | Its control API runs over a WebSocket | [Below](#rt-software-swift) |
| **Singular.live** | Partly, untested | Outbound API to a Data Stream, plus a composition script | [Singular.live](works-with/singular-live.md) |
| **Streamstar** | Partly, untested | A browser overlay from OBS over NDI | [Below](#streamstar) |
| **SuiteCG** | Works, untested (0.7.136 or later) | Data Automation polling `/json?pivot&all`, or reading `results.json` / `results.txt` | [SuiteCG](works-with/suitecg.md) |
| **TriCaster** (Vizrt) | Works, untested | Outbound API preset **TriCaster DataLink**; or a browser overlay in a Live Link buffer | [TriCaster](works-with/tricaster.md) |
| **Viz Flowics** | Partly, untested | Outbound API to a JSON HTTP Push input; or the Data Bridge polling ScoreSight | [Viz Flowics](works-with/viz-flowics.md) |
| **Vizrt Viz Engine** | Partly, untested | The TCP / UDP output's Vizrt Shared Memory preset; or Vizrt Datacenter, or Viz Flowics | [Vizrt Viz Engine](works-with/vizrt.md) |
| **vMix** | Works | Built-in vMix output (Web API `SetText`); or a browser overlay in a Web Browser input | [vMix](vmix.md) |
| **Wirecast** | Works, untested | A browser overlay in a Web Page source; or Text sources reading the per-field `.txt` files | [Wirecast](works-with/wirecast.md) |
| **wTVision** | Partly, untested | Studio CG data sources on the files or the HTTP server; or a browser overlay in R³ | [Below](#wtvision) |
| **XSplit Broadcaster** | Works, untested | A browser overlay in a Webpage source; or Text sources loading the per-field `.txt` files | [XSplit Broadcaster](works-with/xsplit.md) |

## Which output for which kind of product

| The product can... | ScoreSight output | Shape |
|---|---|---|
| show a web page | [Browser overlays](overlays.md) | A transparent scorebug, lower third, clock or field (`/overlay/scorebug`...), or your own HTML, pushed live |
| poll a URL for JSON, XML or CSV | [HTTP server](http_server.md) | `/json?pivot&all` (one object), `/json?array` (an array of one row), `/xml?pivot&all`, `/csv?pivot&all` (a header and one row) |
| read a file | [Text files](text-files.md) | `<Box Name>.txt` (one value), `results.csv`, `results.xml`, `results.json` (one object), `results.txt` (`key=value` lines) |
| receive HTTP requests | [Outbound HTTP API](out_api.md) | One request per cycle or per field, JSON/XML/CSV or GET, with headers, a rate limit and presets |
| listen for text over TCP or UDP | [TCP / UDP output](tcp-output.md) | Text lines from a template (`{field}={value}`, JSON, CasparCG XML...), with presets |
| connect to a data source over TCP and read text lines | [TCP / UDP output](tcp-output.md#tcp-server-programs-connect-to-scoresight) as a **TCP server** | The same text lines, to every program that connects |
| take messages on a WebSocket (`ws://`, `wss://`) | [TCP / UDP output](tcp-output.md#websocket-scoresight-connects-to-a-url) over a **WebSocket** | JSON with every field (or any template), a message per change, with an Authorization header if needed |
| take NDI with alpha, but no web pages | [Browser overlays](overlays.md) through OBS | Put the overlay in an OBS scene and send it out with the DistroAV (NDI) plugin |

{: .tip }
Use the `&all` variants for programs that bind to a column or a key: every field is always there, with its last good value. The plain `?pivot` leaves out a field whose reading was rejected for a moment, and some programs blank the field or lose the column then.

## Products without a guide yet

These products have no step-by-step guide, because their vendors don't publish how other software sends them data, or publish only part of it. If you use one and know what it accepts, please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

### Chyron LYRIC / LyricX

**Untested idea.** LYRIC and LyricX, Chyron's earlier graphics systems, are still supported but no longer among Chyron's products. Their Intelligent Interface takes commands over serial or Telnet (TCP), but its update commands address a numbered template message and numbered fields, not names, and Chyron documents them only in Lyric's built-in help. The [TCP / UDP output](tcp-output.md#custom-protocols) with the **Custom** preset can send such commands, with each field's Lyric field number in the names table, once you have the exact command from that help. If your Lyric templates run in PRIME (PRIME's Lyric mode), the [Chyron PRIME](works-with/chyron-prime.md) route is documented.

### ClassX LiveBoard

**Partly, untested.** LiveBoard's DataSource Manager reads CSV, TXT, JSON (with a JSON Path), XML (with an XQuery) and RSS, from a file or a URL, and since version 8.4 it can update data sources automatically, with an interval in milliseconds.

- Use a CSV DataSource on `results.csv` (the [Text files](text-files.md) output, **Save .csv file**, no append) or on `http://<scoresight-ip>:18099/csv?pivot&all`, updated every 500–1000 ms, and assign it to the text objects. JSON (`/json?pivot&all`) and XML (`/xml?pivot&all`) work the same way with a query.
- LiveBoard's AV inputs include web browsers, which can show a ScoreSight [browser overlay](overlays.md).

ClassX's manual is for its customers, so the exact menus aren't given here. FOR-A distributes ClassX in the Americas. Vendor pages: [LiveBoard](https://web.classx.it/products-liveboard), [data-driven graphics](https://web.classx.it/solutions-data-driven-graphics-broadcast-production).

### DELTACAST DELTA-cg

**Not yet.** DELTACAST no longer uses the DELTA-cg name: its graphics are made with [Template Editor](https://deltacast.com/graphics-editor/), part of DELTA-stadium, DELTA-live and other DELTACAST products, which can import Excel, Google Sheets and XML data. How and how often it reads them isn't published. Products with an NDI input could show a ScoreSight browser overlay sent from OBS over NDI (untested).

### Evoux CG Station

**Not yet.** Evoux (its website now leads to Vizitec) documents news tickers, RSS feeds and "dynamic information" objects in CG Station, but not how other software sends it data. Ask Evoux or Vizitec for CG Station's remote-control documentation.

### Grafster

**Not yet.** Converge Horizons says [Grafster](https://www.convergetech.com/Products/Grafster/grafster.html)'s text fields can receive text from a TCP/IP socket (with the TCP/IP Ports option, in the Online Standard edition and up), but doesn't publish the port, the message format, or which side connects. ScoreSight's [TCP / UDP output](tcp-output.md) can connect to Grafster or, as a TCP server, let Grafster connect to it, so it can probably send the text once those are known.

### INTV Atlas Sport

**Not yet.** INTV's [Atlas Sport](http://www.intvco.com/en/products/intv-products/atlassport) exchanges data with timing systems and other graphics systems through its Sport-Proxy option, but INTV doesn't publish the protocol. Ask INTV what Sport-Proxy expects: if it connects to its data source over TCP and reads text lines, ScoreSight's [TCP server](tcp-output.md#tcp-server-programs-connect-to-scoresight) can be that source.

### Navis Media

**Through CasparCG.** Navis Media's sport clients (soccer, basketball, volleyball and others) are operator apps that control CasparCG, with no documented live data input. Run ScoreSight's [CasparCG](works-with/casparcg.md) route on another layer of the same CasparCG server.

### Nemo3D

**Untested idea.** Nautilus Studio says [Nemo3D](https://nemo3d.com/nemo3d.html) connects to databases, Excel tables and "ordinary delimited text", and to scoreboards over network or serial connections. A delimited-text source on ScoreSight's `results.csv` may work, but how often Nemo3D reads it again, and its scoreboard network protocol, aren't published.

### RT Software Swift

**Not yet.** Swift's [control API](https://rtsw.co.uk/all-products/api-control/) is a WebSocket with JSON messages, which no ScoreSight output speaks. RT Software documents indirect routes, none tried: a **Command** input, whose value is what a program prints (for example one that prints a ScoreSight text file), re-run by a timer event; or RT's separate MediaWatcher, which polls URLs into a database that Swift reads.

### Streamstar

**Partly, untested.** Streamstar SW (JVC now lists it as archived) documents no data input for its graphics, but it takes NDI inputs with keying. Show a ScoreSight [browser overlay](overlays.md) in OBS and send it out with the DistroAV (NDI) plugin. Whether the input keeps the overlay's transparency depends on the input, Streamstar says.

### wTVision

**Partly, untested.** wTVision's [Studio CG](https://www.wtvision.com/products/live-production/studio-cg) reads XML, TXT, CSV, JSON and other data sources, and its [R³ engine](https://www.wtvision.com/products/products-graphics/r3/) can show a web page as a texture. Point a Studio CG data source at `results.json` or `results.xml` (the [Text files](text-files.md) output) or at `/json?pivot&all`, or show a [browser overlay](overlays.md) in R³. wTVision systems are usually set up by wTVision, and its documentation is for customers: ask your wTVision contact.

## Another product?

Suggest it in [Ideas](https://github.com/royshil/scoresight/discussions/categories/ideas), in ScoreSight's Discussions, with the product's name and what it can receive (files, URLs, HTTP requests, TCP messages, web pages), ideally with a link to its documentation.
