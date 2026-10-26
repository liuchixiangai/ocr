---
title: Viz Flowics
parent: Works With…
description: "Send ScoreSight's live scoreboard readings to Viz Flowics cloud graphics with a JSON HTTP Push data connector, or let Flowics Data Bridge poll ScoreSight."
---

# Viz Flowics
{: .no_toc }

**Partly, untested.** A Flowics **JSON HTTP Push** data input takes JSON over HTTP, and Flowics graphics bind to its fields.

## JSON HTTP Push

1. In Flowics, **Settings** > **Data Connectors - Inputs** > **Add** > **JSON HTTP Push - Content**. Paste a sample with your exact field names, for example `{"Home Score":"0","Away Score":"0","Time":"00:00","Period":"1"}`, generate the schema (keep every type *String*) and copy the push URL.
2. In the Graphics Editor, add a Data Connector for that input and bind text elements to its fields. Publish.
3. In ScoreSight, select **HTTP API** in the Outputs list ([Outbound HTTP API](../out_api.md)) and set **URL** to the push URL, method **POST**, **Encode** *JSON (Simple key-value)*, and tick **Only changes** (keep **Fields** on *All in one request*). Tick **Send out API requests to external services.**

## Data Bridge

For the Basic plan or higher, on Windows: Flowics **Data Bridge** on the ScoreSight computer polling `http://127.0.0.1:18099/json?pivot&all` (the [HTTP server](../http_server.md)) every second.

## Notes

Flowics doesn't document push rate limits, or whether a payload replaces or merges with the previous one. Renaming a field in ScoreSight breaks the binding.

## Vendor documentation

- [JSON HTTP Push](https://support.flowics.com/en/articles/8873416-working-with-data-connectors-json-http-push)
- [Data Bridge](https://support.flowics.com/data-bridge/working-with-data-bridge)
