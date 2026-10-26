---
title: MagicSoft CG
parent: Works With…
description: "Update MagicSoft CG text items from ScoreSight's live scoreboard readings over MagicSoft's socket server, with the MagicSoft preset of ScoreSight's TCP output."
---

# MagicSoft CG
{: .no_toc }

**Works, untested.** [MagicSoft CG](https://magicsoft.tv/cg.html) runs a socket server that sets its **TCP/IP variables**: text items whose text is a name in brackets, such as `[home_score]`. The MagicSoft preset of ScoreSight's [TCP / UDP output](../tcp-output.md#magicsoft-cg) sends a line per changed field:

```
[home_score]=21
```

MagicSoft's help describes the socket server and the variables but doesn't print the message syntax. The preset follows another OCR application's public guide, and MagicSoft's own sample macro files use the same `[name]=value` lines.

## Set up

1. In MagicSoft CG, select a text item of your scorebug, click **TCP/IP Variable** in its properties, and set its text to the field's name in brackets, for example `[home_score]`, `[away_score]` or `[clock]` (`[time]` is reserved for MagicSoft's clock). Click **Refresh** and put the project on air.
2. In **Tools › Options › Socket communication**, pick a port pair that no other client uses. MagicSoft's default configuration has port pairs between 2610 and 2643, and its own tools send to the first port of a pair (the Extended keyboard uses 2634, GPI 2638). The server's default IP is `127.0.0.1`: if ScoreSight runs on another computer, check that setting.
3. In ScoreSight, select **TCP / UDP** in the Outputs list and pick **MagicSoft CG** in **Preset**. Enter the MagicSoft computer's address and the port from step 2 (the preset's 2034 is only an example port).
4. Check the names table: the preset sends lower-case names (`home_score`, `away_fouls`), and Time as `clock`. Change them to match your variables.
5. Click **Start sending**.

## Notes

- Which port of a pair takes the commands, the text encoding on the wire (the preset sends UTF-8, the same as ASCII for digits) and whether a variable updates on air without playing the project again aren't documented. If nothing changes, try the other port of the pair.
- MagicSoft CG 10 inputs accept NDI with alpha, so a ScoreSight [browser overlay](../overlays.md) can also reach it through OBS: put the overlay in a scene and send it out with the DistroAV (NDI) plugin.
- Untested by the ScoreSight project. If you try it, please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Vendor documentation

- [MagicSoft CG](https://magicsoft.tv/cg.html); the help (`MagicSoft_CG_Help.pdf`) comes with the installer
- [TCP/IP protocol](https://magicsoft.tv/presentations/TCPIP_protocol.html)
