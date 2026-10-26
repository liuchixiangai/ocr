---
title: Live Score
parent: Works With…
description: "Send ScoreSight's live scoreboard readings to the Live Score app (live-score-app.com) through its Scoreboard OCR input, with the Live Score preset of ScoreSight's TCP output."
---

# Live Score
{: .no_toc }

**Works, untested.** [Live Score](https://www.live-score-app.com/) (live-score-app.com) can take readings through its **Scoreboard OCR input** (Professional edition, version 1.8.14 or later). Live Score listens on a port; ScoreSight connects to it with the Live Score preset of the [TCP / UDP output](../tcp-output.md#live-score) and sends one JSON object per message:

```
{"type":"ocr", "values":{"ScoreHome": "21", "GameClock": "3:12"}}
```

{: .warning }
This message format doesn't come from Live Score's own documentation, and it hasn't been tested with Live Score. If it doesn't work with your version, adapt the template on the TCP / UDP page, and please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Set up

1. In Live Score, open **Input › Scoreboard OCR › OCR Setup**. Activate the input, set a port, and note the field names the dialog lists for your sport.
2. In ScoreSight, select **TCP / UDP** in the Outputs list, pick **Live Score** in **Preset**, and enter the Live Score computer's address and that port.
3. In the names table, enter Live Score's field names. The preset suggests `GameClock` (Time), `ShotClock` (Shot Clock), `ScoreAway` (Away Score) and `ScoreHome` (Home Score); check every name in the OCR Setup dialog.
4. Click **Start sending**. The OCR Setup dialog's **Start Test** shows the data that arrives. Then close it and press Live Score's blue start button.

Every message carries every field, at most four messages a second.
