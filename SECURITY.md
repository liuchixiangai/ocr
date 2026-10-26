# 安全策略 / Security policy

光为 OCR（本仓库 <https://github.com/liuchixiangai/ocr>）目前只维护最新源码和下载站上的最新便携包。

| 版本 | 是否接受安全修复 |
|---|---|
| 下载站当前通道（`src/product_brand.py` 的 `GW_RELEASE`）和 `main` | 是 |
| 更旧的便携包 | 否，请先换下载站上的新包 |

用户支持 QQ 群：124356408。安全问题不要发到群里。

## 如何报告漏洞

请不要在公开的 Issue、Pull Request 或 QQ 群里粘贴漏洞细节。

1. 优先使用 GitHub 私人漏洞报告：打开本仓库的 [Security](https://github.com/liuchixiangai/ocr/security) 页，选择 **Report a vulnerability**（<https://github.com/liuchixiangai/ocr/security/advisories/new>）。
2. 如果私人报告不可用，可以开一个 Issue，正文只写“有安全问题，请私下联系”，不要写复现步骤。

请提供版本（关于对话框或 `GW_RELEASE`）、Windows 版本、影响和复现步骤。维护者是 [liuchixiangai](https://github.com/liuchixiangai)。确认后会在发行说明里致谢，除非你要求匿名。

上游 ScoreSight 自身的问题也可以同时报告给 <https://github.com/royshil/scoresight/security>。只影响光为 OCR 改动的问题报告给本仓库。

## 下载与签名

- 终端用户的安装包只从 <https://ocr.4018.cn/> 获取。当前文件是未签名的 `GuangWeiOCR-Portable.zip`。
- 用下载站 `version.json` 里的 SHA-256 核对文件。Windows 示例：`Get-FileHash GuangWeiOCR-Portable.zip -Algorithm SHA256`。
- **这个包还没有 Authenticode 签名。** SmartScreen 提示未知发布者是预期现象，不代表校验和失败。
- 本仓库不包含 `.pfx`、`.p12` 或 HookSigntool。未来如果 SignPath Foundation 批准，签名只能来自 GitHub 托管的 Actions，发布者名称是 SignPath Foundation。政策见 [Code signing policy](docs/code-signing-policy.md)。在那之前，任何自称“已签名光为 OCR”的文件都不要运行。
- ScoreSight 旧域名 `scoresight.live` 已经不属于上游项目。不要从那个域名下载。

## 网络

光为 OCR 是桌面程序，但会打开网络连接。

### 本机 HTTP 服务

- 启动后在 **所有 IPv4 接口（`0.0.0.0`）的 18099 端口**（或 `--port`）上提供只读的比分页面和 JSON。没有登录。
- 能访问该端口的人可以读到当前比分、字段名和框的位置。
- 只在可信局域网放行。不要把端口映射到公网。公共 Wi-Fi 上应在防火墙里拦截入站。

### 出站

- 便携版每次启动会请求 `https://ocr.4018.cn/version.json`，失败后再试 `http://ocr.4018.cn/version.json` 和 `http://211.101.245.47/version.json`。请求带 `GuangWeiOCR/<通道版本>` User-Agent。只有你确认更新时才会下载 zip。从源码运行不会覆盖工作目录。
- vMix、OBS、你填写的 HTTP 地址，只在你打开对应输出时连接。`http://` 不加密，只适合可信局域网。
- 默认把 JSON 写到本机桌面 `光为OCR输出\results.json`，不上传。

### 配置文件里的口令

`scoresight.json` 可能以明文保存 OBS WebSocket 密码和你填写的 URL。不要把这个文件发到 Issue 或 QQ 群。日志里也可能有主机名和 URL。

## 范围

在范围内：本仓库的应用代码、`.github/workflows` 里的构建脚本、下载站上的光为 OCR 便携包。

不在范围内：OBS、vMix、芯象、NDI、Qt、Tesseract 等第三方软件的漏洞（请报告给上游），以及攻击者已经能操作你的 Windows 账号才能利用的问题。
