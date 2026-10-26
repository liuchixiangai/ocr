---
title: Epiphan Pearl
parent: Works With…
description: "Show ScoreSight's live scoreboard readings in Epiphan Pearl layouts with custom system variables set over Pearl's HTTP API, or as a browser overlay in Web Graphics."
---

# Epiphan Pearl
{: .no_toc }

**Works, untested.** Pearl's custom system variables show live values in a layout's text: ScoreSight sets them over Pearl's HTTP API. Or add a ScoreSight [browser overlay](../overlays.md) as a **Web Graphics** source.

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Custom variables in text overlays

1. In ScoreSight, select **HTTP API** in the Outputs list ([Outbound HTTP API](../out_api.md)) and pick **Epiphan Pearl** in **Preset**. The URL becomes:

   ```
   http://admin:PASSWORD@PEARL-IP/admin/set_variables.cgi
   ```

2. Replace `PEARL-IP` with the Pearl's IP address and `PASSWORD` with its admin password. Use `http://` (Pearl doesn't accept https for these commands).
3. Tick **Send out API requests to external services.** ScoreSight sends `GET /admin/set_variables.cgi?home_score=21&away_score=14&...`: only the changed fields, up to 6 per request and at most 5 requests per second (Pearl's documented limits), and every value again every 10 seconds, because Pearl forgets its variables when it restarts.
4. **The variable names**: Pearl allows only letters, digits and `_`, so each field is sent with a safe name: *Home Score* becomes `home_score`, *Away Score* `away_score`, *Time* `time`. The HTTP API page lists them (*Pearl variables: %(home_score), %(away_score)...*).
5. In Pearl's Admin panel, open the channel's **Layouts**, add a **Text** item and type for example `%(home_score)` (or `%(home_score) - %(away_score)`). A monospace font keeps the digits from jumping. Save: the text updates on every channel that uses the variable.

To check: open `http://PEARL-IP/admin/get_variables.cgi` in a browser; it lists the variables.

Unverified: whether Pearl wants Basic or Digest authentication for these commands (ScoreSight uses Digest if Pearl asks for it), and what Pearl does above its limits.

## Browser overlay in Web Graphics

Add a **Web Graphics** (HTML) source to a layout with the overlay address from ScoreSight's **HTTP server** page, for example `http://192.168.1.20:18099/overlay/scorebug` (Pearl-2 and Pearl Mini, firmware 4.19 or later).

Untested: whether Pearl's web graphics accept a plain `http://` address with a port on the local network and keep the page's transparency.

## Vendor documentation

- [Controlling Pearl with HTTP commands](https://www.epiphan.com/userguides/pearl-api/Content/integrate/thirdPartyConfig/man_br_3rdParty_controlling_with_http.htm)
- [Custom system variables in text overlays](https://www.epiphan.com/userguides/pearl-2/Content/capture/customSystemVarsTxtOverlay.htm)
