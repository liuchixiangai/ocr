# Code signing policy

代码签名政策（光为 OCR / GuangWei OCR）

This is the code signing policy for the public repository
<https://github.com/liuchixiangai/ocr>. It describes who may change the
source, who may approve a signature, and what may be signed. It does not
mean a signature exists today.

**Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org/).**

Status: **not in effect.** SignPath Foundation has not approved this project.
The portable zip on <https://ocr.4018.cn/> is unsigned. No workflow in this
repository submits a signing request. Do not describe a download as signed
until a SignPath request has been approved and the resulting Authenticode
signature verifies.

The name on a future certificate is **SignPath Foundation**. That publisher
name is expected. It is not 桐城市六尺巷网络有限公司, and it is not the
maintainer's personal name. Copyright of the ScoreSight code and of the
光为 OCR modifications stays as written in [LICENSE](../LICENSE).

## What this program is

光为 OCR is a Windows portable scoreboard OCR application for live sports
broadcasts. It reads a scoreboard image and writes JSON for vMix / 芯象. It
is permanently free for end users and MIT licensed. It is not a security
scanner, exploit tool, or remote-access tool.

## Roles

There is one maintainer. The same GitHub account holds every role below
until another person is added in this file and given the matching GitHub
permission. A signing request still needs a manual approval in SignPath.
Nobody else may approve one.

| Role | Who | Responsibility |
|---|---|---|
| Committer (author) | [liuchixiangai](https://github.com/liuchixiangai) | May change source in <https://github.com/liuchixiangai/ocr>. Write access is limited to this account. |
| Reviewer | [liuchixiangai](https://github.com/liuchixiangai) | Reviews every pull request from anyone who is not a committer before it is merged. The reviewer reads the diff, including workflow files and `scoresight.spec`, because those files define what a signature would cover. |
| Approver | [liuchixiangai](https://github.com/liuchixiangai) | Approves or rejects each SignPath signing request. Approval means the approver has confirmed the artifact was built by the workflow below from this repository. |

External contributors have no write access and no SignPath account. Their
changes are merged only after the reviewer accepts the pull request.

When a second maintainer is added, this table is updated in the same pull
request that grants access. Approvers remain a listed subset. A person who
is not listed here cannot approve a release.

## Multi-factor authentication

Every committer, reviewer, and approver must use multi-factor authentication
on:

- the GitHub account that has access to `liuchixiangai/ocr`
- the SignPath account that can submit or approve a signing request

Turn MFA on before submitting the application at
<https://signpath.org/apply.html>. An account without MFA must not be given
write access, a SignPath submitter role, or an approver role.

## What may be signed

Only these artifacts:

- Built by GitHub Actions on a **GitHub-hosted** `windows-latest` runner.
- Built from the public source, build scripts, and workflows of
  **<https://github.com/liuchixiangai/ocr>** at the commit GitHub reports for
  that run.
- Produced by `scoresight.spec` (PyInstaller) in
  [`.github/workflows/windows-portable.yml`](../.github/workflows/windows-portable.yml),
  or by a later workflow in this repository that replaces it and is reviewed
  under this policy.
- Uploaded as a workflow artifact **before** the signing request, so SignPath
  can tie the bytes to that run.
- Approved manually in SignPath by an approver listed above. One approval is
  required. A single rejection cancels the request.

The branch allowed for release signing is `main`, plus a release tag that
points at a commit already on `main`. Test builds from other branches are not
release-signed.

## What is not signed

- A binary built on a laptop, a self-hosted runner, or any machine outside
  GitHub-hosted Actions.
- A binary from another repository, a fork, or a zip already published at
  <https://ocr.4018.cn/> before this policy's CI produced it. The portable
  zip on the download site today was packaged locally and **must stay
  unsigned** until a CI build replaces it.
- Anything submitted by uploading a file by hand to SignPath.
- Private keys, `.pfx`, `.p12`, or HookSigntool scripts. Those do not belong
  in this repository. `scripts/ci/sign_windows.ps1` is upstream ScoreSight's
  Azure Artifact Signing helper. 光为 OCR releases do not use it, and it is
  not a SignPath integration.
- Dependencies rebuilt from a different upstream commit than the one recorded
  in this repository's requirements files, except for the PP-OCR weights
  fetched by `scripts/fetch_models.py` at the revisions and SHA-256 values
  pinned in that script.
- Malware, potentially unwanted programs, exploit tools, or code whose
  purpose is to bypass security controls.

## Open source only

The signed source is the MIT tree in this repository. There is no commercial
dual license and no proprietary module maintained alongside it.

Third-party components that a frozen build may include:

- PySide6 / Qt (LGPLv3), OpenCV (Apache-2.0), ONNX Runtime (MIT), Tesseract
  and tesserocr, PaddleOCR PP-OCRv6 weights (Apache-2.0; see
  `ocr_models/ppocr/LICENSE` and `NOTICE`).
- The NDI runtime redistributed via the `cyndilib` dependency. That runtime
  is Vizrt's, not MIT. It is a library the upstream ScoreSight spec already
  bundles. If SignPath does not accept it inside a Foundation-signed
  package, the signed build must drop that DLL before the first signing
  request. This policy does not treat it as the project's own code.

Tesseract `.traineddata` files are ScoreSight packaging assets. They are not
in this initial git tree. A signed build may include them only when the
workflow obtains the same files the maintainer documents, from the ScoreSight
project, and the spec hashes or the job log show which files were bundled.
Until that step exists, the Windows workflow does not package.

No maintainer-owned closed-source plugin is allowed in a signed artifact.

## No malware

The maintainers will not request a signature for malware or for a build they
know has been tampered with. Scoreboard OCR, LED debounce, and writing a JSON
file are the product. Features that scan for vulnerabilities or circumvent
the host's security controls are out of scope and will not be added for the
purpose of getting them signed.

If SignPath or a user reports a violation, the approver investigates and
stops further signing requests until the report is resolved.

## Build reproducibility notes

PyInstaller output is not bit-for-bit reproducible across toolchain updates.
SignPath's check is that the artifact comes from this repository's workflow
on GitHub-hosted runners, not that a third party can rebuild identical
bytes. The workflow must stay in this repository so reviewers can see it.

Model weights:

- PP-OCR: `python scripts/fetch_models.py` (pinned Hugging Face revisions
  and SHA-256). Not committed, except the license notices.
- Tesseract `.traineddata`: not in this initial tree. `scoresight.spec`
  refuses to build without them. The workflow skeleton records that and
  does not emit an artifact.

## Privacy

This section is the privacy statement for the Windows application.

光为 OCR has no account and no analytics product. It does contact the network
in the cases below. Because of the update check, this program does **not**
match a blanket claim that it never transfers information unless the user
asks.

- **Update check.** A frozen portable build, on startup, requests
  `version.json` from `https://ocr.4018.cn/version.json`, then if needed
  `http://ocr.4018.cn/version.json` and `http://211.101.245.47/version.json`
  (`src/product_brand.py`), and retries that round when nothing answered.
  The request sends a `GuangWeiOCR/<channel>` user agent. If the user
  confirms an update, the app downloads the portable zip from the URL in
  that manifest and checks its SHA-256 before installing. A run from source
  only opens the download page; it does not overwrite the git checkout.
- **Operator-configured outputs.** vMix, OBS, outbound HTTP, TCP/UDP, and
  the optional serial/TCP data port run only as the operator sets them.
  The default file output is local: `Desktop\光为OCR输出\results.json`.
- **Built-in HTTP server.** The app listens on `0.0.0.0` port 18099 (or
  `--port`) with no authentication. Anyone who can reach that port can read
  current scoreboard values. Do not expose it on an untrusted network.
- **Upstream update check.** Inherited ScoreSight code can query GitHub for
  `royshil/scoresight` when the GuangWei channel flag is off. The portable
  channel keeps `SIMPLIFY_OUTPUT_UI` on and uses the `ocr.4018.cn` manifest
  instead.

The program does not sell data. Third-party services the operator turns on
(vMix, OBS, overlays.uno, a custom HTTP endpoint) have their own policies.

## Maintainer duties before the first signed release

1. Enable MFA on GitHub and on SignPath.
2. Submit [docs/SIGNPATH_APPLICATION.md](SIGNPATH_APPLICATION.md) at
   <https://signpath.org/apply.html>.
3. Put a visible **Code signing policy** link on <https://ocr.4018.cn/>
   pointing at this file on `main`. The download page does not have that
   link yet.
4. Protect `main` so force-pushes are rejected. Prefer pull requests for
   changes that affect `scoresight.spec` or `.github/workflows/`.
5. Leave the SignPath step in `windows-portable.yml` commented out until
   SignPath has created the organization, project slug, and signing policy,
   and the approver has the API token stored as a GitHub Actions secret
   (not in git).
