# Releases

On every push to upstream `main`, including a merged PR, GitHub Actions tests that exact commit, builds the release downloads, and installs the VPS bundle on a disposable Linux runner. Failed checks stop publishing. A successful run creates a `v3.0.N` tag at the tested commit, attaches the verified downloads to a draft release, and then publishes it with GitHub-generated release notes. `N` is the release workflow's run number, so gaps are normal. [GitHub Releases](https://github.com/pboachie/Arma-AI-War-Godmode/releases)

| Download | What it contains |
| --- | --- |
| `Arma-AI-War-Godmode-<tag>-linux-vps.tar.gz` | Middleware, Docker Compose configuration, and the installer. It builds the middleware image on your VPS. |
| `Arma-AI-War-Godmode-<tag>-workbench-source.zip` | Enfusion scripts, reference scenario data, and config tooling. No compiled addon, game binaries, or Workshop upload. |
| `install-linux-vps.sh` | Release-pinned bootstrap that verifies the VPS bundle before running it. |
| `SHA256SUMS` | SHA-256 checksums of the archives and bootstrap. Both archives also contain `RELEASE.json` with their exact source commit. |

Download the archives you need and `SHA256SUMS` from the same release into one directory. On Linux, verify the files you downloaded before extracting them.

```bash
sha256sum --check --strict --ignore-missing SHA256SUMS
```

On Windows, use `Get-FileHash -Algorithm SHA256` on the ZIP and compare the result with its entry in `SHA256SUMS`.

For the middleware, use the published bootstrap or extract the VPS archive and run `bash scripts/install-vps.sh` from it. Use the archive directly for older releases without a bootstrap. Neither release path needs Git. The moving `main` source tree is for development, not the release-qualified install path. For the addon, follow the [Workbench guide](WORKBENCH_SETUP.md) and test against the intended game build. [VPS prerequisites](../README.md#start-the-middleware-on-a-linux-vps)

## Retry a failed release

Rerun the failed workflow on GitHub to reuse the same version and commit. If an upload failed, the release may have a tag or a draft but is not published until GitHub confirms all asset digests. A rerun can finish that draft. It does not move an existing tag or overwrite a published release. If the stored workflow artifact expired, rerun all jobs so the packages are rebuilt and tested. If a tag points to a different commit, investigate the conflict and leave the existing tag in place.

## Fork and credential boundaries

PRs, including PRs from forks, run read-only checks. They do not get a release job or feed artifacts into one. A fork can run its own checks, but the official publishing job is restricted to a `main` push in the upstream repository by its GitHub repository ID. The publisher uses only same-run artifacts from the successful checks, does not check out code or execute downloaded files, and gets the built-in `GITHUB_TOKEN` with `contents: write` only in the publishing job. Neither workflow references configured Actions secrets or repository variables.

Keep credentials out of source, `.env.example` files, and Actions variables. Actions variables are not a secret store. Review workflow and packaging changes before merging them to `main`, since `main` is the publishing trust boundary. Do not enable settings that send write tokens or secrets to untrusted fork PRs. The release packages come from an explicit allowlist in the tested Git commit, so a runner's environment files, credentials, logs, and generated game state are not packaged.
