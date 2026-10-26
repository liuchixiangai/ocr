# 发布光为 OCR 便携包

已安装的光为 OCR **不会**因为 `src/version.py` 或 GitHub Release 变了就提示更新。启动时它只比较两处字符串：

- 程序里的 `src/product_brand.py` → `GW_RELEASE`
- 下载站 <https://ocr.4018.cn/version.json> 的 `version`

只有网站上的版本 **比本机 `GW_RELEASE` 更新** 时才会提示。两边一样，用户看起来就像「没有自动更新」。这是比较规则，不是检查没跑。日志里会有一行 `GW update check: installed …, site …, no update`。

`scripts/bump_version.py` 改的是上游 ScoreSight 的版本号。它结束时会提醒下面这两步，但**不会**改 `GW_RELEASE`。

## 每次发便携包

1. 确认新包已经打好（`GuangWeiOCR.exe`，zip 名 `GuangWeiOCR-Portable.zip`）。
2. 把通道版本抬到比当前 `GW_RELEASE` 更新的字符串（日期加点修订即可。画面翻转和默认 25 帧是 `2026.10.26.1`；加固后的自动更新是 `2026.10.26.2`。下一次包含新代码的 zip 必须再用一个更新的字符串，相同版本会被脚本拒绝）：

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

   `sha256` 不能省。没有合法摘要时，客户端会拒绝下载。画面翻转和默认 25 帧的包是 `2026.10.26.1`。加固自动更新（重试、必须 SHA-256、后台下载并可续传、更安全的覆盖脚本、中文「检查更新」）的源码通道是 `2026.10.26.2`：发布那个 zip 时，`version.json` 的 `version` 必须是同一个 `2026.10.26.2`。不要把 `version.json` 写成源码里还没有的版本。

5. 用**上一版**便携包启动一次，确认会提示刚发布的通道版本。再用刚打出来的包启动一次，确认提示已是最新。手动菜单是「检查更新」：已是最新、有新版本、网络失败是三句不同的话。

检查地址按顺序是 `https://ocr.4018.cn/version.json`、`http://ocr.4018.cn/version.json`、`http://211.101.245.47/version.json`，整轮失败会再试。从源码运行只会打开下载站，不会覆盖仓库目录。
