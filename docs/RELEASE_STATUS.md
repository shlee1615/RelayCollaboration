# Source snapshot status — 2026-10-07

This GitHub publication contains the current **0.5.0 source line**, including independent instances, bilingual GUI and agent guides, QuickStart, model selection and the September CLI parser patch. It is a source snapshot, not a new executable release or a certification for every provider version.

## Known compatibility issue

The latest recorded official Claude CLI 2.1.286 run passed authenticated preflight but was rejected by the no-tools output gate with `capabilities_present_in_no_tools_profile` on 2026-10-03. That request was handled as failed; it was not an accepted review. The issue remains unresolved in this snapshot. Do not weaken the gate or treat doctor success as end-to-end acceptance.

The September 30 patch accepted only exact known built-in plugin metadata and passed 30 focused tests; two real Relay reviews then succeeded. This history does not certify newer CLI output. Provider versions and account model availability require fresh validation.

## Validation boundaries

Publication-time verification on 2026-10-07: 313 Python tests ran in a clean,
short-path Windows source extraction: 310 passed, 2 skipped and 1 failed
(`test_two_live_instances_bidirectional_cli_and_independent_stop`, at the reverse
dispatch receipt query). All 15 frontend tests passed. The Python suite is not
fully green; this source snapshot is published as a prerelease with that unresolved
test explicitly disclosed. A deeper extraction also failed both bidirectional
fixture branches; the same bidirectional test passed in the short-path extraction.
Use short program/data paths and keep fixture runtime outside Git ancestry.

The September 23 full regression recorded 309 Python passes / 2 skips and 15 frontend passes. Release-time verification is recorded separately in the GitHub release notes. Fixtures do not establish model availability, new-machine acceptance, or macOS/Linux support. Private development logs, requests and receipts are excluded from this publication.

The historical Windows 0.5.0 EXE was built September 7 and does not include subsequent source changes. It is not included as a current binary in this source release. Use Python 3.11+ and QuickStart.cmd for this snapshot.

## Scope and licensing

This is a local Windows collaboration tool. It does not implement the separate SmartCluster project, WAN transport, autonomous leader failover or automatic implementation of model proposals. Public packaging excludes credentials, instance/runtime databases, user histories, local configuration and imported private references.

No project open-source license has been selected. NOTICE.md is not a license grant. Third-party tools retain their own terms and require each user's installation and official login.

## Documentation

Start with [English README](../README.md), [Traditional Chinese README](../README.zh-TW.md), or the [complete Traditional Chinese manual](USER_MANUAL.zh-TW.md). The [English agent guide](AGENT_GUIDE.md) covers setup, both directions, receipts and recovery in detail.
