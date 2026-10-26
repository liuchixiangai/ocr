---
title: Chyron PRIME
parent: Works With…
description: "Update Chyron PRIME scenes from ScoreSight's live scoreboard readings with Intelligent Interface P\\UPDATE commands over TCP, or a PRIME Data object that polls ScoreSight."
---

# Chyron PRIME
{: .no_toc }

**Works, untested.** ScoreSight sends Intelligent Interface `P\UPDATE` commands to PRIME over TCP, with the Chyron preset of the [TCP / UDP output](../tcp-output.md#chyron-prime). Or a PRIME scene's **Data** object reads ScoreSight's JSON, XML or CSV.

<a href="https://youtu.be/HaWNl92JLBE" target="_blank" rel="noopener"><img src="../videos/13-broadcast.jpg" width="45%" alt="Video: Scoreboard data for CasparCG, Chyron, Vizrt and TriCaster (3:15)"/></a>

<details open markdown="block">
  <summary>On this page</summary>
  {: .text-delta }
1. TOC
{:toc}
</details>

## Intelligent Interface (TCP / UDP output)

ScoreSight sends a command per changed field, for example:

```
P\UPDATE\1\*\Home Score\21\\
```

That is: update, on channel `1`, every scene (`*`) that has an object named `Home Score`, with the value `21`.

1. In PRIME, open **View › Automation Settings** and add an **Intelligent Interface** connection. Its connection type is TCP/IP and its encoding Western European (ISO-8859-1) by default; ScoreSight's preset uses the same encoding. **Enable** the connection (it then waits for a connection), and turn on **Connect on Startup** if you like.
2. In ScoreSight, select **TCP / UDP** in the Outputs list and pick **Chyron PRIME** in **Preset**. Enter the PRIME computer's address and the connection's **port**. Chyron documents no default port.
3. Name the objects (or parameters) of your scene like the names in ScoreSight's names table: by default the field names, such as `Home Score`.
4. Click **Start sending**.

To target only the scenes on Program, a channel or one scene, edit the template: see [Chyron PRIME](../tcp-output.md#chyron-prime) on the TCP / UDP page.

## Data object polling ScoreSight

A PRIME scene's **Data** object reads JSON, XML or CSV, and pushes it to its bindings on an **Update** command.

1. In the PRIME scene, add a **Data** object (scene toolbox, Resources).
2. Set **Source** to *JSON* with the URL `http://<scoresight-ip>:18099/json?pivot&all` (the [HTTP server](../http_server.md)) and a short download interval. Or **Source** *Web* with `/xml?pivot&all`.
3. In the binding section, drag each text object's **Text** property onto its field (*Home Score* and so on).
4. The Data object updates its bindings only on an Update command: set its **Timer Interval** (for example 0.5 s) with **Timer Elapsed** = *Update*, and start the timer in the scene's in-animation (keyframe **Command** = *Start Timer*). Or wire the **Data Changed** event to an Update.

The scene designer has to set up the timer; the shortest download interval isn't documented.

## Browser overlay (HTML Input)

PRIME 5.3 and later has an HTML Input, which can show a ScoreSight [browser overlay](../overlays.md) address. Untested.

## Vendor documentation

- [PRIME Data object](https://help.chyron.com/hc/en-us/article_attachments/28076292820244)
- *PRIME Automation: Intelligent Interface P Commands* (Chyron's documentation for PRIME customers)
