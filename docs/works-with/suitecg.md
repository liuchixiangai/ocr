---
title: SuiteCG
parent: Works With…
description: "Drive SuiteCG's clocks, scores and periods from ScoreSight's live scoreboard readings with SuiteCG Data Automation (JSON, XML or key=value TXT, over HTTP or from a file)."
---

# SuiteCG
{: .no_toc }

**Works, untested.** SuiteCG's **Data Automation** (version 0.7.134 and later) reads basic XML, JSON, CSV and `key=value` TXT data from a file or over HTTP, and drives SuiteCG's clock, score and period counters with it.

## Set up

1. Install SuiteCG **0.7.136 or later**: it fixes a crash on data keys with a space, such as `Home Score`. (If your copy is licensed, deactivate the licence before upgrading, as SuiteCG asks.)
2. In ScoreSight, check in a browser that `http://<scoresight-ip>:18099/json?pivot&all` shows the values (the [HTTP server](../http_server.md)).
3. In Data Automation, add a JSON source over HTTP with that URL. Or, from a file: tick **Save .json file** (or **Save key=value .txt file**) in the [Text files](../text-files.md) output and point SuiteCG at `results.json` (or `results.txt`) in the output folder. On an older SuiteCG, use `http://<scoresight-ip>:18099/xml?pivot&all`, whose element names have no spaces.
4. Map the keys: *Time* to the game clock, *Home Score* and *Away Score* to the score counters, *Period* to the period counter, possession or bonus to toggles. Export the mapping profile to reuse it.
5. Check that SuiteCG accepts ScoreSight's clock text (`4:30`, `04:30` or `45.3`); if not, change the box's [Format](../box-settings.md#format).

## Notes

SuiteCG automates clocks, counters, toggles and basic team details; macros and animations stay manual. Its polling interval and accepted JSON shapes aren't documented.

## Vendor documentation

- [Data Automation (0.7.134)](https://suitecg.com/2026/08/10/changelog-0-7-134/)
- [0.7.136](https://suitecg.com/2026/08/18/changelog-0-7-136/)
- [TXT as key=value](https://suitecg.com/2024/09/03/coming-soon-external-data-football-updates/)
