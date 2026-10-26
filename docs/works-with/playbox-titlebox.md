---
title: PlayBox TitleBox
parent: Works With…
description: "Show ScoreSight's live scoreboard readings in PlayBox TitleBox (and TitleBox Neo) text objects by linking each object to one of ScoreSight's text files."
---

# PlayBox TitleBox
{: .no_toc }

**Works, untested.** A TitleBox text object can be linked to a text file and updates every time the file is saved. ScoreSight's [Text files](../text-files.md) output writes a file per field, so each object shows one field.

The steps follow PlayBox Technology's *TitleBox User Manual* (v2.3, 2020). TitleBox Neo, from PlayBox Neo, lists the same data sources; whether its menus are the same hasn't been checked.

## Set up

1. In ScoreSight, select **Text files** in the Outputs list, choose a folder, keep **Append** on *Don't append results*, and set **Save / s** to 5–10. ScoreSight writes `Home Score.txt`, `Away Score.txt`, `Time.txt` and so on.
2. In TitleBox, right-click a text object > **Set File Link**, and pick for example `Home Score.txt` in that folder.
3. In the **Links** dialog, keep the link **Active**. Tick **AutoPlay On file Change** if the object should play when the value changes.
4. Repeat for each field.

## Alternative: one CSV for every field

TitleBox's **Data Source Manager** has a **FileLink** data provider for delimited text files, which can **Update Automatically**. ScoreSight's `results.csv` (tick **Save .csv file**, no append) has a header row and one row of values. The manual says FileLink reads `*.txt` files, so it may refuse the `.csv` name; the per-field links above are the safer route.

## Notes

- ScoreSight replaces each file in one step (a temporary file renamed over it). Whether TitleBox counts that as "saved" hasn't been tested.
- The files are UTF-8 without a byte order mark. Digits and plain letters read the same in any encoding.
- TitleBox's network control (**Net Control** port) uses a protocol that PlayBox documents only on request, so ScoreSight can't use it yet.
- Untested by the ScoreSight project. If you try it, please tell us in [ScoreSight's Discussions](https://github.com/royshil/scoresight/discussions/categories/show-and-tell).

## Vendor documentation

- [TitleBox User Manual v2.3](https://playboxtechnology.com/wp-content/uploads/2021/08/TitleBox_user_manual_2020.pdf) (Set File Link, Data Source Manager)
- [TitleBox Neo](https://playboxneo.com/interactive-graphics)
