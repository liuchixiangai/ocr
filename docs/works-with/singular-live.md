---
title: Singular.live
parent: Works With…
description: "Send ScoreSight's live scoreboard readings to Singular.live graphics through a Singular Data Stream and a composition script."
---

# Singular.live
{: .no_toc }

**Partly, untested.** Singular **Data Streams** take any JSON over HTTP, without call limits, and a composition script puts the values into the graphic. For overlays.uno, which runs on Singular, use the built-in [UNO output](../uno.md) instead: it needs no script.

<a href="https://youtu.be/vMrr92-3LeI" target="_blank" rel="noopener"><img src="../videos/12-uno.jpg" width="45%" alt="Video: overlays.uno and Singular.live scoreboards, updated live (3:29)"/></a>

## Set up

1. In the Singular Dashboard, open **Data Stream Manager** > **New** and copy the private and public tokens. (Data Streams need a Pro plan or the free trial.)
2. In ScoreSight, select **HTTP API** in the Outputs list ([Outbound HTTP API](../out_api.md)) and set **URL** `https://datastream.singular.live/datastreams/<PRIVATE TOKEN>`, method **PUT**, **Encode** *JSON (Simple key-value)*, and tick **Only changes**. Tick **Send out API requests to external services.**
3. In Composer, give each value a text control node, and add a composition script along these lines:

   ```js
   // init
   const sc = comp.find("<Sub-composition name>")[0];
   ds = context.utils.createDataStream("<PUBLIC TOKEN>", (status, msg) => {
     if (status !== "message") return;
     console.log(msg);  // check the shape first
     const data = (msg && msg.payload) || msg;  // the values, wrapped or not
     const update = {};
     if (data["Home Score"] !== undefined) update["HomeScore"] = data["Home Score"];
     if (data["Time"] !== undefined) update["Clock"] = data["Time"];
     sc.updateContent(update);  // control node IDs on the left
   });
   // close
   ds.close();
   ```

4. Check the stream's status in the Data Stream Manager, and watch the Composer preview.

## Notes

The requests carry only fields with a good reading, so update only the keys that are present.

## Vendor documentation

- [Data Stream API](https://developer.singular.live/data-stream-api/introduction.md)
