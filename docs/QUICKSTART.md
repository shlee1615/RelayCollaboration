# Start collaborating in three steps

English | [繁體中文](QUICKSTART.zh-TW.md)

Start here for normal use. The quick-start wizard supports the current Windows source package and requires Python 3.11+, Codex App and the official Claude Code CLI to be installed.

## 1. Double-click QuickStart.cmd

Extract the package and double-click **QuickStart.cmd** in its root. Choose English or Traditional Chinese, then a data folder and instance name. Normally, press Enter to accept the defaults.

The wizard prepares local Python, allocates available ports, creates an independent instance and finds a native Claude executable with a valid Anthropic publisher signature. You do not need to edit JSON or set authentication directories manually.

Data defaults to `local-data` inside the package. If the source is in a Git checkout, choose a data folder outside repository ancestry, such as `D:\RelayData`.

## 2. Complete official Claude login

On first use, the wizard offers to open official Claude login for this instance. Follow that flow. If Claude is missing or cannot be found, enter the full official `claude.exe` path or configure it later.

Once the CLI is configured and the instance is stopped, choose its model: CLI default, Opus 5.5, Fable/Opus/Sonnet/Haiku aliases, or an exact custom ID. Enter preserves the current setting. After selecting a model, enter an effort or use `default` to reset it. A running instance keeps its model; stop it normally before changing it. Choose the Codex App model in the bound App task, including when Claude leads the collaboration.

Each independent instance has its own authentication. Reusing the same data folder and name resumes that environment; a new name creates another independent collaboration. Usage limits remain shared for the same account.

## 3. Paste the connection instructions

The wizard opens the console and displays two instructions with their exact paths already filled in:

- **Codex paragraph:** paste into the participating Codex App task.
- **Claude paragraph:** paste into Claude Code when you want it to dispatch questions to Codex.

The same text is saved as `CONNECT.en.txt` in the instance folder for later copying. Both participants must use the same selected config.

After the console starts, Claude still needs to be ready and the actual Codex task needs to attach before both directions can operate. The wizard never impersonates Codex or releases another task's binding automatically. Use **Manage connections / Unbind** to inspect a conflict, or choose another independent instance.

## Next time

Double-click **QuickStart.cmd** again and use the same data folder and name. It preserves the instance's configured CLI, explicit model, authentication and binding. It does not automatically replace an existing CLI. If a Claude update removes that path, stop the instance normally, then select the new official path with `-Claude` below.

Automatic receiving is still provided by the Codex task's App heartbeat. The generated instructions ask that task to configure five-minute checks when its tool is available. Otherwise, the LLM must report manual receiving as the available mode.

## For LLMs: one startup command

Create or resume an instance in an authorized data folder without manually chaining install, create, init, doctor and start:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\Tools\Relay\scripts\quickstart.ps1' -NoWizard -DataDir 'D:\RelayData' -Name research-a -Language en -Start
```

Replace the package and data paths. This entry returns JSON: verify `config` and `console_url`, then pass `connection_instructions` to the intended LLMs. `ok=true` means environment operations succeeded; check `ready_for_both_directions` and the separate `claude` and `codex` results for readiness.

Add `-Claude 'C:\your-installation\claude.exe'` to explicitly set a path. Changes require the selected instance to be stopped. The original config is backed up; model and effort are retained. Repeating the wizard does not automatically replace an already configured official CLI.

Add `-Login` only when the user has authorized login and interactive input is available. It runs official `auth login` in this instance's own environment, without copying credentials or making model calls. Stop a running instance normally before changing its CLI or signing in.

Omit `-Start` to only prepare and inspect. `-NoDetect` disables CLI discovery. The wizard uses source skills directly and does not install or rebind global skills.

For a stopped instance, add `-Model claude-opus-5-5 -Effort default` to select Claude explicitly; omitted fields stay unchanged. For an existing exact config use `relay.py --config <full-path> model show`. In dual CLI mode, `model set --actor claude|codex` configures each recipient independently. See [model configuration](CONFIGURATION.md).

If the user supplies an existing absolute config path, keep using the direct `relay.py --config <full-path>` entry instead of substituting the wizard's default data folder. See the [complete LLM operating guide](AGENT_GUIDE.md) for advanced operations and troubleshooting.
