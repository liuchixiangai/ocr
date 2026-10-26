---
title: Home
layout: home
nav_order: 1
permalink: /
description: "ScoreSight documentation: install, set up, tune and integrate real-time scoreboard OCR."
---

# ScoreSight documentation
{: .fs-9 }

Real-time scoreboard OCR for live production. Free, open source, and available for Windows, macOS and Linux.
{: .fs-6 .fw-300 }

[Download ScoreSight](https://github.com/royshil/scoresight/releases/latest){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[Quickstart](quickstart.md){: .btn .fs-5 .mb-4 .mb-md-0 .mr-2 }
[View on GitHub](https://github.com/royshil/scoresight){: .btn .fs-5 .mb-4 .mb-md-0 }

---

ScoreSight reads text and numbers from a live video feed (a stadium scoreboard filmed by a camera, a capture card, an NDI feed, an RTSP stream, a window on your screen or a video file). It sends the values to your production tools many times per second. You draw a box around each value you care about, such as the home score, the clock or the period. ScoreSight then reads every box with [PaddleOCR's PP-OCRv6](https://github.com/PaddlePaddle/PaddleOCR) text recognition models (or [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)) and publishes the results to text files, a built-in HTTP server, OBS, vMix, overlays.uno, or any HTTP endpoint you choose.

<div align="center">
<a href="https://youtu.be/21LhNHbH9gU" target="_blank" rel="noopener"><img src="videos/01-overview.jpg" width="60%" alt="Video: ScoreSight in 3 minutes (2:20)"/></a>
</div>

## Start here

| I want to... | Go to |
|---|---|
| Install ScoreSight | [Installation](INSTALL.md) |
| Read my first scoreboard in five minutes | [Quickstart](quickstart.md) |
| Connect a camera, NDI feed, RTSP stream, screen or video file | [Video Sources](sources.md) |
| Get the data into OBS, vMix, UNO, a browser or my own app | [Outputs](outputs.md) |
| Use it with Wirecast, XSplit, TriCaster, CasparCG, Chyron, XPression, Vizrt, CharacterWorks, Captivate... | [Works With…](integrations.md) |
| Follow the game: each score, period and the final buzzer | [Game & Sports](game.md) |
| Fix readings that are wrong or flicker | [Recognition Tuning](recognition-tuning.md) |
| Run two scoreboards or cameras at once | [Multiple Instances & Profiles](profiles.md) |
| Fix a problem | [Troubleshooting](troubleshooting.md) and [FAQ](faq.md) |
| Build ScoreSight or contribute | [Developer Guide](development/index.md) |

## How it works

1. **Capture**: ScoreSight grabs frames from your [source](sources.md).
2. **Correct**: Optional rotation, cropping, stabilization and 4-corner perspective correction make the scoreboard flat and steady ([Viewer & Image Tools](view-tools.md)).
3. **Read**: Every [detection box](boxes.md) is read by an [OCR model](ocr-models.md) (PP-OCR by default, or Tesseract, which first cleans the box up according to its [per-box settings](box-settings.md)), then checked against the box's format.
4. **Validate**: Format rules (for example "a clock looks like `mm:ss`") reject impossible readings before they reach your graphics.
5. **Publish**: Results go to every [output](outputs.md) you enabled. Outputs run in the background, so a slow or offline target never freezes the app.

## Video tutorials

A short series on the current version, from the first source to your graphics. The guides show the matching video at the top of their page.

<div>
<a href="https://youtu.be/21LhNHbH9gU" target="_blank" rel="noopener"><img src="videos/01-overview.jpg" width="30%" alt="Video 1: ScoreSight in 3 minutes (2:20)"/></a>
<a href="https://youtu.be/6P1K4CpHUwM" target="_blank" rel="noopener"><img src="videos/02-get-started.jpg" width="30%" alt="Video 2: Install ScoreSight and connect your camera, NDI, RTSP stream or screen (4:06)"/></a>
<a href="https://youtu.be/XUPebWz5Cuc" target="_blank" rel="noopener"><img src="videos/03-straighten.jpg" width="30%" alt="Video 3: Straighten the scoreboard picture: rotate, crop, 4-corner, stabilize (3:08)"/></a>
<a href="https://youtu.be/fwKIpfiTbCo" target="_blank" rel="noopener"><img src="videos/04-fields.jpg" width="30%" alt="Video 4: Place boxes over every score and clock (3:06)"/></a>
<a href="https://youtu.be/ZOoNYqGhmmI" target="_blank" rel="noopener"><img src="videos/05-accurate-readings.jpg" width="30%" alt="Video 5: Rock-solid readings: models, formats and filters (3:46)"/></a>
<a href="https://youtu.be/1HRfxRJHBKk" target="_blank" rel="noopener"><img src="videos/06-game-day.jpg" width="30%" alt="Video 6: Game day: Live layout, Pause outputs and Hold value (3:06)"/></a>
<a href="https://youtu.be/OrIesDf_fsY" target="_blank" rel="noopener"><img src="videos/07-template-fields.jpg" width="30%" alt="Video 7: Template fields: combine scores and clock in one line (3:05)"/></a>
<a href="https://youtu.be/tKA6UBqcDC0" target="_blank" rel="noopener"><img src="videos/08-outputs.jpg" width="30%" alt="Video 8: Outputs: the built-in HTTP server, JSON and text files (3:23)"/></a>
<a href="https://youtu.be/2i_7QR6-yKY" target="_blank" rel="noopener"><img src="videos/09-browser-overlays.jpg" width="30%" alt="Video 9: Free scoreboard browser overlays for OBS, vMix and more (3:07)"/></a>
<a href="https://youtu.be/anQl1D921O0" target="_blank" rel="noopener"><img src="videos/10-obs.jpg" width="30%" alt="Video 10: Live scoreboard scores in OBS Studio (2:44)"/></a>
<a href="https://youtu.be/vMrr92-3LeI" target="_blank" rel="noopener"><img src="videos/12-uno.jpg" width="30%" alt="Video 12: overlays.uno and Singular.live scoreboards, updated live (3:29)"/></a>
<a href="https://youtu.be/HaWNl92JLBE" target="_blank" rel="noopener"><img src="videos/13-broadcast.jpg" width="30%" alt="Video 13: Scoreboard data for CasparCG, Chyron, Vizrt and TriCaster (3:15)"/></a>
<a href="https://youtu.be/FVqdjvmnFIU" target="_blank" rel="noopener"><img src="videos/14-profiles.jpg" width="30%" alt="Video 14: Two cameras, backups and troubleshooting (3:58)"/></a>
<a href="https://youtu.be/BYQ4KhoY6eU" target="_blank" rel="noopener"><img src="videos/16-auto-layout.jpg" width="30%" alt="Video 16: What's new: Auto crop and Auto layout with AI (2:04)"/></a>
</div>

Older videos, about earlier versions of ScoreSight: [ScoreSight with vMix](https://youtu.be/yowoYzBWrps) (vMix now uses port **8088**; see the [vMix guide](vmix.md)), [ScoreSight with NewBlue Captivate](https://youtu.be/ptR-Yh5FSPg) and [collecting OCR training data](https://youtu.be/QO76EFmJ7Ig).

## Getting help

- Search these docs (the search box at the top) and read [Troubleshooting](troubleshooting.md).
- Ask in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions): questions in [Q&A](https://github.com/royshil/scoresight/discussions/categories/q-a), ideas in [Ideas](https://github.com/royshil/scoresight/discussions/categories/ideas), and your production in [Show and tell](https://github.com/royshil/scoresight/discussions/categories/show-and-tell). In the app, click **Feedback** at the right end of the status bar.
- Look through [existing issues](https://github.com/royshil/scoresight/issues?q=is%3Aissue).
- [Open a new issue](https://github.com/royshil/scoresight/issues/new/choose) and attach your log files. [Troubleshooting](troubleshooting.md#collect-logs-first) explains where to find them.
