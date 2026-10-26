# SignPath Foundation 申请草稿

Copy-paste values for <https://signpath.org/apply.html>.

提交是维护者在网页上手工完成的。本文没有账号、没有 API token、没有证书。
**现在不能签名。** 下载站上的 `GuangWeiOCR-Portable.zip` 是未签名的本地打包结果。

证书上的发布者将是 **SignPath Foundation**。申请表里不要把桐城市六尺巷网络有限公司写成发布者。该公司只出现在 [LICENSE](../LICENSE) 的修改版权声明里。

申请前请先：

1. 给 GitHub 用户 `liuchixiangai` 打开多因素认证（MFA）。
2. 阅读并遵守 [Code signing policy](code-signing-policy.md) 和 <https://signpath.org/terms.html>。
3. 在 <https://ocr.4018.cn/> 加上指向该政策的链接，标题使用英文 **Code signing policy**。本仓库 README 已经有这个标题；下载页目前还没有。
4. 用你自己收得到信的邮箱。不要把邮箱写进 git。

## 表单字段

**Project name**

```text
光为 OCR / GuangWei OCR
```

**Repository URL**

```text
https://github.com/liuchixiangai/ocr
```

**Homepage URL**

```text
https://ocr.4018.cn/
```

**Download URL**

```text
https://ocr.4018.cn/
```

当前文件名是 `GuangWeiOCR-Portable.zip`（Windows 11 x64，免安装）。通道版本 `2026.10.26.3`（`src/product_brand.py` 的 `GW_RELEASE`；这一版会在 PP-OCR 权重可用时，把已保存的 Tesseract General Scoreboard / Large 一次性改为 PP-OCR 自动）。这个 zip **未签名**，SmartScreen 可能报警。完整性以下载站 `version.json` 中的 SHA-256 为准。

**License**

```text
MIT
```

根目录 [LICENSE](../LICENSE) 是 MIT。保留 ScoreSight / OCC AI（2024）版权。光为 OCR 的修改由 liuchixiangai 与桐城市六尺巷网络有限公司于 2026 年以同一 MIT 许可发布。没有商业双许可。

**Code signing policy**

```text
https://github.com/liuchixiangai/ocr/blob/main/docs/code-signing-policy.md
```

合并进 `main` 之后再用这个链接。政策页包含 SignPath 要求的句子：Free code signing provided by SignPath.io, certificate by SignPath Foundation。

**Privacy policy**

```text
https://github.com/liuchixiangai/ocr/blob/main/docs/code-signing-policy.md#privacy
```

不要勾选“本程序除非用户要求否则绝不联网”这类表述。便携版启动时会请求 `ocr.4018.cn/version.json`（HTTPS，失败后再试 HTTP 和源站 IP）。用户确认后才下载更新包。详见政策里的 Privacy 一节。

**Maintainer**

```text
GitHub: https://github.com/liuchixiangai
Role: sole committer, reviewer, and approver
Support QQ group (end users): 124356408
```

**Maintainer type**

```text
Individual
```

公司名称栏留空。不要填写桐城市六尺巷网络有限公司。SignPath 证书发给 SignPath Foundation，不发给该公司。

**Email / first name / last name**

用维护者本人的姓名和邮箱填写。本仓库不记录这些信息。

**Wikipedia URL**

留空。

**Build system**

```text
PyInstaller 6.22.3 (scoresight.spec) on GitHub Actions, windows-latest.
Workflow: .github/workflows/windows-portable.yml (workflow_dispatch only).
```

**Description（短，可直接粘贴）**

```text
GuangWei OCR (光为 OCR) is a permanently free, MIT-licensed Windows 11 x64 portable app. It reads a live sports scoreboard (LED / 7-segment) and writes JSON for vMix and 芯象. End users download an unsigned portable zip from https://ocr.4018.cn/. Source is https://github.com/liuchixiangai/ocr, a Chinese fork of ScoreSight (MIT). Binaries are intended to be built with PyInstaller on GitHub-hosted GitHub Actions. They are not Authenticode-signed yet. The SignPath publisher, if approved, will be SignPath Foundation.
```

**Description（补充，空间够再贴）**

```text
The app is a desktop OCR tool for live sports production, not a security product. A camera or capture card is aimed at a venue scoreboard. Readings are written to Desktop\光为OCR输出\results.json. The Chinese UI, vMix outbound files, LED frame stacking / debounce, portable layout, and the ocr.4018.cn update channel are the fork's changes. ScoreSight (https://github.com/royshil/scoresight) remains credited under the MIT license.

PP-OCR weights are downloaded by scripts/fetch_models.py from Hugging Face at pinned revisions (Apache-2.0). They are not in git. Tesseract .traineddata files are also absent from this initial tree and must be copied from a ScoreSight packaging checkout before PyInstaller will succeed. The workflow skeleton refuses to package or sign until those files exist.

The zip already offered on https://ocr.4018.cn/ (GuangWeiOCR-Portable.zip) was built on a local packaging machine. It is unsigned and must not be submitted to SignPath as a hand-uploaded file. Only a future GitHub-hosted Actions artifact from liuchixiangai/ocr may be signed, and only after manual approval.

A frozen build may include the NDI runtime shipped by the cyndilib dependency (Vizrt, not MIT), because scoresight.spec collects it. If that binary is not acceptable under the Foundation rules, it will be removed before the first signing request.

Contact for users: QQ group 124356408. Maintainer GitHub: liuchixiangai.
```

**Artifact to sign（批准之后，不是现在）**

```text
Windows x64 portable folder produced by: python -m PyInstaller --clean --noconfirm scoresight.spec
Path today: dist/scoresight/scoresight.exe (onedir).
Download-site name: GuangWeiOCR.exe inside GuangWeiOCR-Portable.zip.
The CI skeleton zips dist/scoresight as scoresight-windows-x64-portable-unsigned.zip and does not rename the exe. Signing stays disabled until the workflow, the public tree, and the SignPath project all match.
```

**Has signed releases today?**

```text
No. The public portable zip is unsigned. SmartScreen warnings are expected.
```

## 勾选

- 同意 SignPath Foundation Code of Conduct（证书在违规时可以被吊销）。
- 同意 SignPath 保存并处理申请表里的个人数据。
- 营销邮件按你自己的意愿，可选。

## 批准之后才做的事

1. 安装 SignPath 要求的 GitHub App，只授权 `liuchixiangai/ocr`。
2. 在 SignPath 里把可信构建设为 GitHub.com 托管 runner，发布签名策略限定分支 `main`。
3. 每次签名都要维护者手工批准。
4. 把 `SIGNPATH_API_TOKEN` 放到 GitHub Actions secret，把 organization id 放到变量。不要写进仓库。
5. 再取消 `.github/workflows/windows-portable.yml` 底部 SignPath 步骤的注释。动作是 `signpath/github-action-submit-signing-request@v3`。取消注释本身不会让签名生效。
6. 用签过名的 CI 产物替换下载站上的未签名 zip，并在页面上写明已经签名。在此之前页面应继续说明文件未签名。
