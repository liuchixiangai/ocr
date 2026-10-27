# 发布光为 OCR 便携包

已安装的光为 OCR **不会**因为 `src/version.py` 或 GitHub Release 变了就提示更新。启动时它只比较两处字符串：

- 程序里的 `src/product_brand.py` → `GW_RELEASE`
- 下载站 <https://ocr.4018.cn/version.json> 的 `version`

主窗口标题、状态栏和「关于」显示的就是这个 `GW_RELEASE`（例如 `光为 OCR · 2026.10.27.2`），不是上游 ScoreSight 的 `v0.4.1`。用户对一下标题栏，就知道自己在跑哪一个便携包。

OBS 虚拟摄像机要先在 OBS 里点「启动虚拟摄像机」，确认 OBS 预览有画面，再在这里选择 `OBS Virtual Camera`。vMix 选 `vMix Video`，不要走这条 OBS 打开路径。两边都是 DirectShow 虚拟摄像头，但 OBS 会对不匹配的分辨率假装成功，预览就是一整块黑；vMix 会拒绝，所以能看到真实的 1920×1080。还是黑的就点刷新。整幅黑时大约几秒内就会提示，不会一直卡住。画面很暗但数字还在时不会当成没启动；只有整幅几乎全黑才会提示。采集卡（美乐威、Blackmagic、禾鸟）不受这条路径影响。

只有网站上的版本 **比本机 `GW_RELEASE` 更新** 时才会提示。两边一样，用户看起来就像「没有自动更新」。这是比较规则，不是检查没跑。日志里会有一行 `GW update check: installed …, site …, no update`。

`scripts/bump_version.py` 改的是上游 ScoreSight 的版本号。它结束时会提醒下面这两步，但**不会**改 `GW_RELEASE`。

## 每次发便携包

1. 确认新包已经打好（`GuangWeiOCR.exe`，zip 名 `GuangWeiOCR-Portable.zip`）。
2. 把通道版本抬到比当前 `GW_RELEASE` 更新的字符串（日期加点修订即可。画面翻转和默认 25 帧是 `2026.10.26.1`；加固后的自动更新是 `2026.10.26.2`；已保存的 Tesseract General Scoreboard / Large 一次性迁到 PP-OCR（自动）是 `2026.10.26.3`；比赛时间在选定运动后能写入结果、节次「二」和时钟前导零是 `2026.10.26.4`。下一次包含新代码的 zip 必须再用一个更新的字符串，相同版本会被脚本拒绝）：

   ```bash
   python scripts/bump_gw_release.py <新通道版本> --dry-run
   python scripts/bump_gw_release.py <新通道版本>
   ```

   脚本拒绝空版本、相同版本和更旧的版本。然后把这个改动提交进将要发布的程序里，**再用这个程序重新打包**。先打包再改版本，用户装上的仍是旧的 `GW_RELEASE`。

3. 计算 zip 的 SHA-256（小写十六进制）。Windows：

   ```powershell
   Get-FileHash GuangWeiOCR-Portable.zip -Algorithm SHA256
   ```

4. 上传 zip，并把 `https://ocr.4018.cn/version.json` 改成同一个版本，例如：

   ```json
   {
     "version": "<与 GW_RELEASE 相同>",
     "zip_url": "https://ocr.4018.cn/GuangWeiOCR-Portable.zip",
     "sha256": "<上一步的摘要>",
     "notes": "",
     "released_at": "2026-10-26"
   }
   ```

   `sha256` 不能省。没有合法摘要时，客户端会拒绝下载。画面翻转和默认 25 帧的包是 `2026.10.26.1`。加固自动更新（重试、必须 SHA-256、后台下载并可续传、更安全的覆盖脚本、中文「检查更新」）的源码通道是 `2026.10.26.2`。默认 OCR 改为 PP-OCR（自动），并在权重可用时把已保存的 General Scoreboard / Large 迁过去（只此一次）的源码通道是 `2026.10.26.3`。比赛时间在选定运动后写入结果、时钟前导零和节次「二」的源码通道是 `2026.10.26.4`：发布那个 zip 时，`version.json` 的 `version` 必须是同一个 `2026.10.26.4`。不要把 `version.json` 写成源码里还没有的版本。

5. 用**上一版**便携包启动一次，确认会提示刚发布的通道版本。再用刚打出来的包启动一次，确认提示已是最新。手动菜单是「检查更新」：已是最新、有新版本、网络失败是三句不同的话。

检查地址按顺序是 `https://ocr.4018.cn/version.json`、`http://ocr.4018.cn/version.json`、`http://211.101.245.47/version.json`，整轮失败会再试。从源码运行只会打开下载站，不会覆盖仓库目录。
