# 发布光为 OCR 便携包

已安装的光为 OCR **不会**因为 `src/version.py` 或 GitHub Release 变了就提示更新。启动时它只比较两处字符串：

- 程序里的 `src/product_brand.py` → `GW_RELEASE`
- 下载站 <https://ocr.4018.cn/version.json> 的 `version`

主窗口标题、状态栏和「关于」显示的就是这个 `GW_RELEASE`（例如 `光为 OCR · 2026.11.07.1`），不是上游 ScoreSight 的 `v0.4.1`。用户对一下标题栏，就知道自己在跑哪一个便携包。通道 `2026.11.07.1` 在签名基线 `2026.11.03.1`（与 `2026.10.27.5` 同功能）上加入 24 秒空白输出 24，以及**本机**可执行的强制更新协议。不含 main 上后来的三栏界面。

OBS 虚拟摄像机要先在 OBS 里点「启动虚拟摄像机」，确认 OBS 预览有画面，再在这里选择 `OBS Virtual Camera`。vMix 选 `vMix Video`，不要走这条 OBS 打开路径。两边都是 DirectShow 虚拟摄像头，但 OBS 会对不匹配的分辨率假装成功，预览就是一整块黑；vMix 会拒绝，所以能看到真实的 1920×1080。还是黑的就点刷新。整幅黑时大约几秒内就会提示，不会一直卡住。画面很暗但数字还在时不会当成没启动；只有整幅几乎全黑才会提示。采集卡（美乐威、Blackmagic、禾鸟）不受这条路径影响。

只有网站上的版本 **比本机 `GW_RELEASE` 更新** 时才会提示。两边一样，用户看起来就像「没有自动更新」。这是比较规则，不是检查没跑。日志里会有一行 `GW update check: installed …, site …, no update`。

`scripts/bump_version.py` 改的是上游 ScoreSight 的版本号。它结束时会提醒下面这两步，但**不会**改 `GW_RELEASE`。

## 打 zip（中文文件名）

下载站上的压缩包用下面这条命令生成，不要用 PowerShell 的 `Compress-Archive`：

```powershell
python scripts/portable_zip.py dist\scoresight -o dist\GuangWeiOCR-Portable.zip
```

`Compress-Archive`（Windows PowerShell 5.1，底层是 .NET Framework 的 `ZipArchive`）会把文件名按 UTF-8 写进 zip，但**不**设置通用标志位第 11 位（UTF-8 文件名）。中文版资源管理器看到这个标志没打开，就按系统 ANSI（GBK）解码。磁盘上本来正确的 `使用说明.txt` 解压后会变成 `浣跨敤璇存槑.txt`，`启动光为OCR.bat` 同样变成乱码。英文名 `GuangWeiOCR.exe`、`scoresight.exe` 不受影响。

这条命令会：

- 保留 `scoresight.exe`，并复制出 `GuangWeiOCR.exe`（两个都留）
- 放上仓库里的 `packaging/portable/使用说明.txt` 和 `packaging/portable/启动光为OCR.bat`（按 Unicode 文件名复制，不走系统代码页）
- 用 Python `zipfile` 打包：非 ASCII 文件名带 UTF-8 标志，并写入 Info-ZIP Unicode 路径扩展字段（`0x7075`）
- 写完再读 zip。标志没有、名字不对、或里面仍是乱码文件名，命令直接失败

暂存目录里如果已经有解压乱码（例如 `浣跨敤璇存槑.txt`），脚本会删掉这些名字，再写入正确的中文文件名。PyInstaller 目录本身不会被改掉。

检查一个已经打好的 zip：

```powershell
python scripts/portable_zip.py --check dist\GuangWeiOCR-Portable.zip
```

在中文 Windows 资源管理器里解压，根上应直接看到 `使用说明.txt` 和 `启动光为OCR.bat`。

## 每次发便携包

1. 用上一节的命令打好包（`GuangWeiOCR.exe`，zip 名 `GuangWeiOCR-Portable.zip`）。不要用 `Compress-Archive`。
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

4. 上传 zip，并把 `https://ocr.4018.cn/version.json` 改成同一个版本。现网 2026.11.03.1 的 `zip_url` 在 `/downloads/` 下，发布 2026.11.07.1 时沿用同一路径，例如 `packaging/version.json.example`：

   ```json
   {
     "version": "2026.11.07.1",
     "zip_url": "https://ocr.4018.cn/downloads/GuangWeiOCR-Portable.zip",
     "sha256": "<签名后 zip 的 SHA-256，小写十六进制>",
     "notes": "必须更新：24秒灯灭时输出24。请点「立即更新」，下载完成后关闭软件。点「下次再说」只会下次启动再提示，旧版不会后台强装。",
     "released_at": "2026-11-07",
     "mandatory": true,
     "min_version": "2026.11.07.1"
   }
   ```

   `sha256` 不能省。没有合法 64 位十六进制摘要时，新旧客户端都会拒绝这份清单。`mandatory` 和 `min_version` 只有 `2026.11.07.1` 起的客户端会读；`2026.10.27.5` / `2026.11.03.1` 忽略未知字段，只比较 `version`。不要把 `version.json` 写成源码里还没有的版本。不要把未签名 zip 的摘要写上网。

5. 用**上一版**便携包启动一次，确认会提示刚发布的通道版本。再用刚打出来的包启动一次，确认提示已是最新。手动菜单是「检查更新」：已是最新、有新版本、网络失败是三句不同的话。

检查地址按顺序是 `https://ocr.4018.cn/version.json`、`http://ocr.4018.cn/version.json`、`http://211.101.245.47/version.json`，整轮失败会再试。从源码运行只会打开下载站，不会覆盖仓库目录。

## 已发布客户端真正支持什么（2026.10.27.5 / 2026.11.03.1）

独立核对：`a1be0fd`（`2026.10.27.5`）与 `2ccb5aa`（`2026.11.03.1`）的 `src/gw_auto_update.py` 里 `parse_version_payload` 只取 `version`、`zip_url`、`sha256`（必填）、`notes`、`released_at`。现网 `https://ocr.4018.cn/version.json`（2026-11-07 拉取）也只有这五个键，没有 `mandatory` / `min_version`。

因此：

- 站点 `version` 比本机 `GW_RELEASE` 新 → **每次启动**弹出「发现新版本」，按钮是「立即更新」/「下次再说」。
- 用户必须点一次「立即更新」，下载通过 SHA-256 后，再关闭软件，才会覆盖安装。
- 点「下次再说」可以一直用旧版；下次启动会再问，但**不会**后台下载、不会锁死、不会读 `mandatory`。
- 检查失败或离线：启动保持安静，不弹窗，更不会锁死。
- 不能只靠服务端加 `mandatory: true` 声称旧版会被强制更新。那些字段对已发布客户端是空操作。

`notes` 会出现在旧版对话框里（截到约 400 字）。这是对旧版最有效的兼容手段：把「请点立即更新后关闭软件」写进 `notes`。

## 2026.11.07.1 本机强制更新协议

本版本开始：

- `mandatory` 为真，或本机 `GW_RELEASE` 比 `min_version` 旧，且站点 `version` 确实更新 → 对话框写「必须更新」，按钮是「立即更新」/「稍后继续使用」。
- 仍要用户确认后才下载 zip（与隐私说明一致：启动只拉 `version.json`）。
- 下载失败可点「重试」；SHA-256 不过关不会覆盖正在用的目录。
- 已经校验过的 `_gw_update_stage/payload` 下次启动会接着用，不再重下。
- 复制失败时脚本尝试把 `GuangWeiOCR.exe` / `scoresight.exe` 恢复成更新前的副本，然后启动旧程序并打开下载站。不删 `%LOCALAPPDATA%\scoresight` 配置，不绕过 ACL。
- 离线或清单无效：**不锁死**，旧程序照常跑。

把 `mandatory` 写进本次 `version.json`，是给**以后**装了 2026.11.07.1 的机器准备的。它改变不了已经在现场的 2026.10.27.5 / 2026.11.03.1。

## Windows 打包机（D:\\ScoreSight）

签名前不要封最终 zip。只对已签名的 `scoresight.exe` 再跑 `scripts/portable_zip.py`。不要用 `Compress-Archive`。不要从 `main` 构建：`main` 含 PR27 三栏界面。

```powershell
cd D:\ScoreSight
git fetch origin
git checkout cursor/gw-2026-11-07-1-release-7acd
# 确认：
#   git rev-parse --short HEAD
#   Select-String -Path src\product_brand.py -Pattern 'GW_RELEASE'
# 应为 2026.11.07.1；src\mainwindow.py 里不应出现三栏出站壳。

.venv\Scripts\activate
pip install -r requirements-win.txt
pip install -r requirements-build.txt
# tessdata 与 ocr_models\ppocr 沿用本机已有副本，不要从空树打包。

pyinstaller --clean --noconfirm scoresight.spec
```

暂存、签名、再打 zip（顺序不能反）：

1. `dist\scoresight\scoresight.exe` — **先签名**（本机已有签名工具）。
2. 若 `dist\scoresight\GuangWeiOCR.exe` 还在，先删掉，以免 `portable_zip.py` 留下未签名副本。
3. 需要看暂存目录时：

   ```powershell
   python scripts/portable_zip.py dist\scoresight -o dist\GuangWeiOCR-Portable.zip --stage dist\GuangWeiOCR-stage
   ```

   **签名完成前不要把这个 zip 当发布包。** 签名后再执行同一条（或只对已签名 onedir 打 zip）。`portable_zip.py` 会复制已签名的 `scoresight.exe` 为 `GuangWeiOCR.exe`，并放入 `使用说明.txt`、`启动光为OCR.bat`。

4. 校验：

   ```powershell
   python scripts/portable_zip.py --check dist\GuangWeiOCR-Portable.zip
   Get-FileHash dist\GuangWeiOCR-Portable.zip -Algorithm SHA256
   ```

便携根目录应有：`GuangWeiOCR.exe`、`scoresight.exe`、`使用说明.txt`、`启动光为OCR.bat`、`_internal\`。用户配置在 `%LOCALAPPDATA%\scoresight\scoresight.json`，不在这个 zip 里。

把 SHA-256 填进 `packaging/version.json.example` 的副本后再上传 `version.json`。在此之前不要改线上 `https://ocr.4018.cn/version.json`。
