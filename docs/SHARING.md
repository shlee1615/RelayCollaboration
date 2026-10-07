# Preparing a source snapshot for GitHub

This project builds **allowlisted source archives** for GitHub distribution.
Packaging itself does not create a repository or remote release. The original 0.5.0 EXE/ZIP remains a
historical release; do not present it as containing the latest source features.

## Build only the public allowlist

From the source root:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts\package.py --output-dir dist\english-source-preview
.\.venv\Scripts\python.exe -B -X utf8 scripts\verify-package.py dist\english-source-preview\relay-collaboration-0.5.0.zip --manifest dist\english-source-preview\relay-collaboration-0.5.0.manifest.json
```

Choose a new output directory for each snapshot. Existing archive/manifest files
are never overwritten. The package's version still identifies the 0.5.0 source
line; its SHA-256 identifies this particular snapshot. Assign a new release
version and rebuild/test the executable before publishing a new executable release.
The 2026-10-07 publication uses a dated source-snapshot tag, preserves the 0.5.0
source version, and does not distribute the historical EXE as a current binary.
See [current release status](RELEASE_STATUS.md) and the [complete manual](USER_MANUAL.zh-TW.md).

The allowlist includes QuickStart.cmd, the source wizard and both quick-start guides,
code, GUI translations, tests, scripts, generic config
templates, English/Traditional Chinese READMEs and complete agent guides, public
docs and both source skill templates.
The archive has an embedded file-size/SHA-256 manifest and a matching external
manifest containing the archive hash. Verification checks required files,
unexpected entries, path safety, hashes and sizes.

It excludes `runtime`, `instances`, `.relay-private`, `.venv`, `workspace`, `work`,
`dist`, local JSON config, generated request/reply files, SQLite, credentials,
installed skill bindings, migration references, private development instructions
and agent logs. Package collection does not inspect those private directories.
Use the verified archive's contents as the future clean repository starting point;
do not upload the working development directory. `.gitignore` is additional
protection, not a substitute for the allowlist or a content review.

## Before a public release

1. Review the public file manifest and contents for accidental machine-specific
   paths, secrets, personal information and nonpublic examples. Package hashes
   prove consistency, not permission to publish.
2. Confirm ownership/provenance and choose an appropriate project license. No
   project license has been selected in this snapshot; `NOTICE.md` describes
   package contents and is not an open-source license. Preserve relevant third-party
   notices for an executable build. Do not invent a copyright holder or license.
3. Set the intended repository owner/name and release version. Repository creation,
   pushing and public publication are separate actions, performed when requested.
   Respect any existing project restriction on Git operations; packaging needs none.
4. Test installation in a clean writable folder, both skill entry points, both
   dispatch directions, English/Chinese GUI, recovery, stop/restart and Windows
   paths with spaces. Keep fixture validation distinct from official model calls.
5. On another machine, install the official Claude CLI and perform that machine's
   own login. Neither installed credentials nor private runtime records are portable.

For a development checkout, put private config/runtime outside the repository as
shown in the [English agent guide](AGENT_GUIDE.md) or [繁體中文操作指南](AGENT_GUIDE.zh-TW.md),
and read source skills directly. The
optional managed-skill installer requires its config inside the installation;
use a clean archive extraction outside Git ancestry for that layout. Do not reinitialize, move or clone an
existing private runtime to solve the CLI's `.git` ancestor check.

## Developer verification

Run tests in a clean source archive extraction outside Git ancestry. Some fixtures
initialize their own private runtime under `work`; a Git checkout's ancestor guard
will correctly reject those fixtures. Use a short Windows path for the extraction,
such as `D:\RelayQA`, to avoid deep nested fixture paths. Do not disable runtime
path or permission checks to make a checkout's fixtures pass.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -t . -v
node tests/test_frontend.mjs
```

These tests use fresh fixtures. A passing suite does not establish real-model or
second-machine acceptance. Node is a development dependency only; the browser
loads local ES modules without a bundler, CDN or package installation.
