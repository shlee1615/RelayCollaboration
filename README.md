# Relay Collaboration

English | [繁體中文](README.zh-TW.md)

**2026-10-07 source snapshot:** see [release status and known CLI compatibility issue](docs/RELEASE_STATUS.md) before use. [Complete Traditional Chinese manual](docs/USER_MANUAL.zh-TW.md) covers installation, architecture, operation, recovery and packaging. No new executable is included in this source snapshot.

A local Windows console for **Codex App ↔ Claude Code** collaboration. Send a
review or proposal to either participant, inspect its inputs and reply, and
deliberately send a follow-up back to the other side.

**Current source updates:** English / Traditional Chinese GUI, independent
instances, binding management and a shared LLM operating guide. The original
0.5.0 EXE/ZIP does not include these updates; use this source snapshot. A new
official executable release has not been built from this snapshot.

## Start here

**Recommended:** double-click **QuickStart.cmd**, complete official Claude login,
then paste the generated connection instructions into Codex (and Claude for reverse dispatch).
The bilingual wizard handles Python, ports, independent instances and configuration.
See the three-step guide: [English](docs/QUICKSTART.md) / [繁體中文](docs/QUICKSTART.zh-TW.md).

**Model selection:** the wizard offers Claude Opus 5.5, family aliases and custom
IDs. `python relay.py --config <full-path> model show` reports both providers.
For a stopped CLI instance, use `model set --actor claude --model claude-opus-5-5`
or `model set --actor codex --model gpt-6-astra`. Add `--effort default` to reset
reasoning effort, or omit it to preserve the current setting. `--model default`
restores the CLI default. Start the instance afterward; unclaimed queued tasks
also use the new selection. Original configuration bytes are backed up.
Presets do not certify account availability. With **Codex App**, choose the model
in the bound App task instead; this remains true when Claude leads. The web
console displays the setting; selection is through the wizard or CLI.
See [configuration details](docs/CONFIGURATION.md).

- **Codex and Claude agents:** read the [English guide](docs/AGENT_GUIDE.md) or
  [繁體中文操作指南](docs/AGENT_GUIDE.zh-TW.md). Both include
  complete setup, login, instance selection, both dispatch directions, expected
  states, durable request IDs, inbox scheduling and conflict recovery.
- **People installing the tool:** use [Quick Start](docs/QUICKSTART.md); the [manual setup](docs/AGENT_GUIDE.md#1-install-a-fresh-source-package) remains available.
  Requires Windows, Python 3.11+, Codex App and your own official native Claude Code
  CLI. Relay itself uses the Python standard library; no pip download is needed.
- **GitHub distribution:** use the [public-source packaging procedure](docs/SHARING.md).
  This publication contains allowlisted source only. No project open-source
  license has been selected; NOTICE.md is not a license grant.

Keep private config/runtime in a writable folder **outside Git repository
ancestry**, even when the source program is in a cloned checkout. The guide uses
separate program and data folders and preserves existing installations. External
config works with direct source skills; the optional managed-skill installer
requires config inside a clean installation outside Git ancestry.

## What is included

- An English / Traditional Chinese console with saved browser language preference.
  Switching language preserves drafts, attachments, selected tasks and pending
  requests. Model replies and supplied text retain their original language.
- Independent instances with separate ports, queues, runtime, Claude sign-in,
  Codex task binding and receipts. One Codex App task owns each instance.
- **Manage connections / Unbind**: discover local instances, see current ownership
  and recent activity, preview release and retry a saved release after a lost response.
- Bidirectional review/proposal dispatch, text attachments and bounded PNG input.
  Fixed request UUIDs and same-content retries prevent duplicate submission.
- A hidden supervisor, official Claude CLI worker and current Codex App mailbox.
  The App uses its existing sign-in; no extra Codex model CLI is required in App mode.
- Two skill templates and a shared launcher: `$relay-codex-app` for Codex App and
  `/relay-claude-code` for Claude Code Local with command-execution access.

## The two directions

| Direction | Sender | Receiver |
| --- | --- | --- |
| Codex → Claude | Bound App task uses `app send --file` | Relay invokes the official Claude CLI; App handles the reply and acknowledges it |
| Claude → Codex | Claude Code Local uses `submit --require-recipient codex --payload` | The real App task runs `app inbox` and `app reply`; Claude reads the original `receipt` |

Both sides must select the same config/instance. Starting the service does not
attach either current conversation automatically. Codex App must be running and
checking its inbox; automatic checks require a heartbeat created by that App task.
Plain cloud chat cannot operate the local service just by reading a skill.

Fresh installations leave Claude unavailable until its own official CLI is
configured and signed in. Saved or queued tasks are not evidence of a successful
model call. Providers never fall back to a fake model. Reply metadata distinguishes
fixtures, CLI-reported model identity and App replies without independent model
attestation.

## Operating boundaries

Relay sends only explicitly supplied task context. It does not copy private App
histories or model credentials, automate Claude consumer-web conversations, or
execute model-generated commands or code. Reviews and proposals remain data for
the user and agents to consider within their authorized work.

The console and broker bind to localhost. Private filesystem permissions and
runtime checks provide local safeguards, not isolation from malicious software
running as the same Windows user. Multiple instances do not multiply account quotas.

## Documentation and validation

- Shared LLM operating guide: [English](docs/AGENT_GUIDE.md) / [繁體中文](docs/AGENT_GUIDE.zh-TW.md)
- [Installation and managed skills](docs/INSTALLATION.md)
- [Independent instances](docs/INSTANCES.md)
- [Codex App binding and recovery](docs/CODEX_APP.md)
- [Configuration and official sign-in](docs/CONFIGURATION.md)
- [Protocol](docs/PROTOCOL.md), [GUI contract](docs/GUI_CONTRACT.md), [operations](docs/OPERATIONS.md)
- [Validation scope](docs/VALIDATION.md), [release notes](docs/RELEASE_NOTES.md)
- [Sharing preparation](docs/SHARING.md), [package notices](NOTICE.md)

Developer checks from the source root:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -t . -v
node tests/test_frontend.mjs
```

Node is needed only for frontend tests. The GUI has no CDN or build step.
Automated fixtures are separate from real-model and second-machine acceptance.
Distributable source archives use an explicit allowlist and SHA-256 manifests;
do not share an installation folder containing runtime, login or task data.
