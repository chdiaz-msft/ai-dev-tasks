# GitHub Copilot CLI: Non-Interactive Usage Reference

Research date: 2026-09-19.
CLI version referenced by the bundled help output on this machine: `1.0.87-0` (see `C:\Users\chdiaz\AppData\Local\copilot\pkg\win32-x64\1.0.87-0\`).
This CLI is actively evolving, so flag names and defaults may change between releases; where the docs themselves note version sensitivity this is called out below.

## Summary

The GitHub Copilot CLI is the `copilot` executable, installed from the npm package `@github/copilot` (or via WinGet/Homebrew/install script/binary download).
It supports a genuine non-interactive "programmatic" mode: pass a prompt with `-p PROMPT` / `--prompt PROMPT`, or pipe a prompt on stdin, and the process runs once and exits.
Tool-use approval, which is interactive by default, can be bypassed with `--allow-all-tools`/`--allow-all`/`--yolo` or scoped with `--allow-tool`/`--deny-tool`/`--available-tools`/`--excluded-tools`.
The model is selected with `--model=MODEL` (or the `COPILOT_MODEL` environment variable), custom/persistent instructions come from files such as `.github/copilot-instructions.md`, `AGENTS.md`, and `CLAUDE.md` (no dedicated `--system-prompt-file` flag was found), and output can be quieted with `-s`/`--silent` and/or structured as JSONL with `--output-format=json`.
Authentication for headless/CI use relies on the `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` environment variables (checked in that order of precedence).
The official docs do not publish a documented table of process exit codes for non-interactive mode; this is flagged as a gap below.

An analogous command to `claude -p "prompt" --model x` is:

```bash
copilot -p "prompt" --model claude-sonnet-4.5 --allow-all-tools -s
```

## 1. Executable name and installation

The executable is `copilot`, distributed as the npm package `@github/copilot`.
Documented install methods (all first-party): npm (`npm install -g @github/copilot`, requiring Node.js 22+), WinGet on Windows (`winget install GitHub.Copilot`), Homebrew on macOS/Linux (`brew install --cask copilot-cli`), an install script (`curl -fsSL https://gh.io/copilot-install | bash`), or downloading the executable directly from the `github/copilot-cli` releases page.
Prerelease variants exist for each channel (e.g. `npm install -g @github/copilot@prerelease`, `winget install GitHub.Copilot.Prerelease`, `brew install --cask copilot-cli@prerelease`).
Source: [Installing GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli).

## 2. Passing a prompt non-interactively

Two mechanisms are documented:

- `copilot -p "Explain this file: ./complex.ts"` — the `-p` / `--prompt` command-line option runs the prompt once and exits when done.
- `echo "Explain this file: ./complex.ts" | copilot` — piping a prompt on stdin also triggers non-interactive mode; piped input is ignored if `-p`/`--prompt` is also given.

Source: [Running GitHub Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically).
The programmatic reference table gives the precise flag form `-p PROMPT` and describes it as "Execute a prompt in non-interactive mode. The CLI runs the prompt and exits when done."
Source: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference).
The conceptual "About" page confirms the same syntax and calls this the "programmatic interface," contrasted with the default "interactive interface" started by running bare `copilot`.
Source: [About GitHub Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli).

Quoting guidance from the docs: use single quotes around the prompt to avoid shell interpretation of special characters, and provide precise, unambiguous prompts (file names, function names, exact changes) for better results.
Source: [Running GitHub Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically).

## 3. Model selection flags

`--model=MODEL` (e.g. `--model=gpt-5.4` or `--model=claude-haiku-4.5`) selects the AI model for the run; it is described as useful "for pinning a model in reproducible workflows."
Example from the docs: `copilot -p "What does this project do?" -s --model claude-haiku-4.5`, and for heavier tasks `copilot -p "Fix the race condition in the worker pool" --model gpt-5.3-codex --allow-tool='write, shell'`.
The `COPILOT_MODEL` environment variable sets a model for the duration of a shell session, and a persisted default can be set via the `model` key in `~/.copilot/settings.json` (or `$COPILOT_HOME/settings.json`), e.g. `{ "model": "gpt-5.3-codex", "effortLevel": "low" }`.
Documented model-selection precedence (highest to lowest): a custom agent's own model setting → `--model` command-line option → `COPILOT_MODEL` environment variable → the `model` key in the settings file → the CLI's built-in default.
To see the exact model strings available, the docs direct users to run `/model` in an interactive session or consult [Supported AI models in GitHub Copilot](https://docs.github.com/en/copilot/reference/ai-models/supported-models).
Source: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) ("Choosing a model" section).

## 4. Permission / approval flags for unattended execution

Interactive tool-use approval is the default: any tool that can modify the system (shell commands, file writes, URL fetches) prompts for approval unless permission has been granted via a flag or a saved `permissions-config.json` entry.
Source: [Allowing and denying tool use](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools).

Documented flags (from the programmatic reference table):

- `--allow-all` (alias `--yolo`) — "Allow the CLI all permissions. Equivalent to `--allow-all-tools --allow-all-paths --allow-all-urls`."
- `--allow-all-tools` — "Allow every tool to run without explicit permission for each tool."
- `--allow-all-paths` — "Disable file-path verification entirely. Simpler alternative to `--add-dir` when path restrictions aren't needed."
- `--allow-all-urls` — "Allow access to all URLs without explicit permission for each URL."
- `--allow-tool=TOOL ...` — "Selectively grant permission for a specific tool," e.g. `--allow-tool='shell(git:*)'` or `--allow-tool='write, shell(npm:*), shell(npx:*)'`.
- `--deny-tool=TOOL ...` — deny a specific tool; deny rules take precedence over allow rules even when `--allow-all` is set.
- `--allow-url=URL ...` / `--deny-url=URL ...` — allow or deny access to specific URLs/domains; deny takes precedence.
- `--available-tools=TOOL ...` — restrict the model to only the listed tools (allowlist for what the model is even aware of).
- `--excluded-tools=TOOL ...` — remove specific tools from what the model can see/choose (denylist for awareness), e.g. `copilot --excluded-tools='web_fetch, web_search'`.
- `--add-dir=DIRECTORY` — add a directory to the allowed-paths list (repeatable).
- `--no-ask-user` — "Prevent the agent from pausing to seek additional user input" (useful so a scripted run does not stall waiting for clarification).

Tool "kinds" usable with `--allow-tool`/`--deny-tool`: `shell`, `write`, `read`, `url`, `memory`, and named MCP servers (e.g. `github`), with optional parenthesized filters such as `shell(git:*)`, `write(README.md)`, `url(github.com)`, `github(create_issue)`.

Sources: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) and [Allowing and denying tool use](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools).

The docs' own security guidance: "Always give minimal permissions — use the `--allow-tool=[TOOLS...]` and `--allow-url=[URLs...]` command-line options ... Avoid using overly permissive options (such as `--allow-all`) unless you are working in a sandbox environment."
A `[!CAUTION]` note on the About page states that using an automatic approval option such as `--allow-all-tools` gives Copilot the same file/shell access as the invoking user, without prior approval.
Sources: [Running GitHub Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically) and [About GitHub Copilot CLI](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli).

The `COPILOT_ALLOW_ALL` environment variable ("Set to `true` for full permissions") is the environment-variable equivalent of `--allow-all`.
Source: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference).

## 5. System prompt / custom instructions support

There is no documented `--system-prompt-file` (or similarly named) command-line flag in the programmatic reference or command reference pages fetched for this research; persistent instructions are instead supplied via well-known instruction files that Copilot CLI auto-discovers.
Documented instruction file locations (from the CLI's own bundled help output and the "Adding custom instructions" doc page):

- `.github/copilot-instructions.md` — repository-wide instructions, discovered in standard locations (repo root, cwd, intermediate directories, and directories nested in the path of a file being worked on).
- `.github/instructions/**/*.instructions.md` — modular, path-scoped instructions (`applyTo` matches specific files); discovered in standard locations but not intermediate directories.
- `AGENTS.md` — agent instructions, discovered in standard locations (references the community [agentsmd/agents.md](https://github.com/agentsmd/agents.md) convention).
- `CLAUDE.md` (and `.claude/CLAUDE.md`) — agent instructions, discovered in standard locations.
- `GEMINI.md` — agent instructions, discovered in standard locations.
- `$HOME/.copilot/copilot-instructions.md` — user-level instructions applying across repositories.
- `$HOME/.copilot/instructions/**/*.instructions.md` — modular user-level instructions.
- `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` environment variable — additional comma-separated directories containing `AGENTS.md`/`*.instructions.md` files.
- If `COPILOT_HOME` is set, it replaces `$HOME/.copilot` for the user-level instruction paths above.

Source: [Adding custom instructions for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions).
This matches the CLI's own bundled interactive help text, which lists the same file set under "Copilot respects instructions from these locations," plus the `/instructions` slash command to view/toggle discovered files for a session (interactive-only; there is a non-interactive `copilot instruction list` inspection command, see below).
Source: bundled CLI help output (`copilot help`), version `1.0.87-0`, surfaced via the CLI's own documentation integration.

Multiple applicable instruction files are combined; the CLI de-duplicates identical user-level/repository-wide/agent instruction content but defines no general precedence order between the different file types, so conflicting instructions across files should be avoided.
Source: [Adding custom instructions for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions).

For non-interactive inspection, `copilot instruction list` (optionally `--json`) lists the custom-instruction sources discovered for the current working directory without starting a session.
Source: [GitHub Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference).

Custom agents (`--agent=AGENT`) are a related but distinct mechanism — they can carry their own model and instruction configuration, and take highest precedence in the model-selection order.
Source: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference).

## 6. Output behavior in non-interactive mode

Default output is plain text to stdout, including session metadata/stats and the agent's response, and it shows which model produced the response (visible unless silenced).
The `-s` flag ("silent") "suppress[es] stats and decoration, outputting only the agent's response," described as "ideal for piping output in scripts."
The `--output-format=FORMAT` option sets the format to `text` (default) or `json`; with `json` the CLI emits JSONL (one JSON object per line), "convenient for parsing the agent's output in scripts."
Sources: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) and [Running GitHub Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically) ("Use `-s` (silent) when capturing output... you get clean text").

Additional documented output/logging-adjacent flags:

- `--attachment=PATH ...` — attach a file (image or native document) to the initial prompt; documented as "only valid in non-interactive mode," repeatable.
- `--secret-env-vars=VAR ...` — redact the value of named environment variables in output/logs (the `GITHUB_TOKEN` and `COPILOT_GITHUB_TOKEN` values are redacted by default, per this same table entry).
- `--share=PATH` — export the full session transcript to a Markdown file after non-interactive completion (defaults to `./copilot-session-<ID>.md`).
- `--share-gist` — publish the session transcript as a secret GitHub gist after completion (not available for Enterprise Managed Users or GHE Cloud with data residency).

Source: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference).

**Gap:** I could not find a documented `--log-level`, `--no-color`, or `--banner` flag on any of the fetched primary-source pages (`cli-command-reference`, `cli-programmatic-reference`, `about-copilot-cli`).
The bundled CLI help output (`copilot help`) references a `logging` help topic (`copilot help logging`) and other topics (`billing`, `config`, `commands`, `environment`, `monitoring`, `permissions`, `providers`, `sandbox`), implying such flags/behavior exist, but their exact syntax was not present in the docs pages I was able to fetch — treat any `--log-level`/`--no-color`/`--banner` flag names as unverified from primary sources.
Source: [GitHub Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) (lists `copilot help [TOPIC]` and its topic names, but the fetched page content did not expand the `logging` topic's flags).

## 7. Exit code conventions

**Gap:** none of the primary-source pages fetched for this research (`cli-command-reference`, `cli-programmatic-reference`, `about-copilot-cli`, `run-cli-programmatically`, `allowing-tools`, `install-copilot-cli`, `authenticate-copilot-cli`) documented a table of process exit codes.
No first-party page was found that states "0 on success, non-zero on failure" or enumerates specific codes.
A community discussion thread (`github.com/orgs/community/discussions/206437`) reports a bug where non-interactive Copilot CLI runs can fail all writes/shell commands yet still exit `0`; this is a GitHub-hosted community discussion, not first-party documentation, so it is noted here only as an unverified community report and excluded from the cited facts above — automation scripts should not assume the exit code alone reliably reflects task success and should also inspect stdout/stderr content.
Recommended follow-up: run `copilot help` (or `copilot help monitoring`/`copilot help logging`) directly against the installed binary version being targeted to confirm exit-code behavior, since it was not documented on the fetched docs.github.com pages.

## 8. Headless / CI / automation flags and environment variables

### Authentication

For headless/CI use, Copilot CLI supports three authentication methods, in this order of precedence for environment-variable tokens: `COPILOT_GITHUB_TOKEN` (highest), `GH_TOKEN`, `GITHUB_TOKEN` (lowest) — set one of these and the CLI uses it automatically without prompting.
A fourth, lowest-priority method is falling back to a token from an authenticated `gh` (GitHub CLI) installation, used only when no other credential is found.
Supported token types: OAuth tokens (`gho_` prefix, from `copilot login` or the `gh` app), fine-grained personal access tokens (`github_pat_` prefix, must be a personal-account token with the "Copilot Requests" account permission — organization-owned fine-grained PATs are not supported), and GitHub App user-to-server tokens (`ghu_`, via environment variable).
Classic PATs (`ghp_` prefix) are explicitly **not supported**.
Sources: [Authenticating GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli) and [Installing GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli).

On a remote/headless terminal (SSH, GitHub Codespaces, dev containers) or in CI, `copilot login`'s OAuth flow defaults to the device-code flow rather than the browser flow (override with `--web-flow` or `--device-code`); `--with-token` reads a token from stdin instead of performing OAuth.
Source: [GitHub Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference).

If BYOK (bring-your-own-key LLM provider) is configured, GitHub authentication is not required at all, though `/delegate`, the GitHub MCP server, and GitHub code search remain unavailable without it.
Setting `COPILOT_OFFLINE=true` makes the CLI avoid contacting GitHub's servers entirely (no GitHub auth attempted, only BYOK-provider network calls, telemetry disabled) — full air-gapping requires the BYOK provider itself to be local/isolated.
Source: [Authenticating GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli).

### GitHub Actions example (from the docs)

```yaml
# Workflow step using Copilot CLI
- name: Generate test coverage report
  env:
    COPILOT_GITHUB_TOKEN: ${{ secrets.PERSONAL_ACCESS_TOKEN }}
  run: |
    copilot -p "Run the test suite and produce a coverage summary" \
      -s --allow-tool='shell(npm:*), write' --no-ask-user
```

Source: [Running GitHub Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically) ("CI/CD integration" section), which also links to [Automating tasks with Copilot CLI and GitHub Actions](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/automate-with-actions) (not separately fetched in this research pass).

### Other automation-relevant environment variables

- `COPILOT_ALLOW_ALL` — set to `true` for full permissions (equivalent to `--allow-all`).
- `COPILOT_MODEL` — sets the model for the shell session (equivalent to `--model`).
- `COPILOT_HOME` — sets the CLI configuration directory (default `~/.copilot`); also relocates the user-level instruction file paths.
- `COPILOT_AUTO_UPDATE` — set to `false` to disable automatic updates; documented as "useful in CI and other automated environments where you want to pin the CLI version."
- `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` — additional directories to search for `AGENTS.md`/`*.instructions.md` files.
- `COPILOT_OFFLINE` — set to `true` to avoid all GitHub network calls (see above).

Source: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) and [Authenticating GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli).
The docs note "For full details of environment variables for Copilot CLI, use the command `copilot help environment` in your terminal" — i.e. the complete list is only fully enumerated by the CLI's own runtime help, not the fetched web docs.

### Other flags useful for automation

- `--fleet` — run the prompt in fleet mode (parallel subagents on separate parts of the task); combinable with `-p` for non-interactive automation; not supported in ACP server mode.
- `--agent=AGENT` — delegate the prompt to a named custom agent, e.g. `copilot -p "Review the latest commit" --allow-tool='shell' --agent code-review`.
- `--resume` — from a non-interactive invocation, launches directly into the previous-session picker (interactive) to resume a prior session; described in a GitHub Blog post rather than the reference doc pages.

Sources: [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference) for `--fleet` and `--agent`; [GitHub Copilot CLI for Beginners: Interactive v. non-interactive mode](https://github.blog/ai-and-ml/github-copilot/github-copilot-cli-for-beginners-interactive-v-non-interactive-mode/) (GitHub Blog, first-party) for `--resume` and the general interactive/non-interactive framing, including the canonical minimal example `copilot -p "Quickly summarize what this repository does and the key folders."`.

## Worked examples from the docs

These are quoted directly from [Running GitHub Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically):

```bash
# Generate a commit message
copilot -p 'Write a commit message in plain text for the staged changes' -s \
  --allow-tool='shell(git:*)'

# Summarize a file
copilot -p 'Summarize what src/auth/login.ts does in no more than 100 words' -s

# Capture output in a shell variable
result=$(copilot -p 'What version of Node.js does this project require? \
  Give the number only. No other text.' -s)
echo "Required Node version: $result"

# Use in a conditional
if copilot -p 'Does this project have any TypeScript errors? Reply only YES or NO.' -s \
  | grep -qi "no"; then
  echo "No type errors found."
else
  echo "Type errors detected."
fi
```

## Gaps / Uncertainties

- **Exit codes**: No primary source documents a specific exit-code contract (e.g. "0 = success, 1 = error") for `copilot` in non-interactive mode.
  A non-official community discussion (`github.com/orgs/community/discussions/206437`) reports that failed tool/shell operations in non-interactive runs can still yield exit code `0`; this is unverified against first-party docs and should be treated as a possible reliability caveat, not a documented fact.
  A script replacing `claude -p ...` should not rely solely on `$?` and may want to also grep stdout for failure indicators.
- **`--log-level`, `--no-color`, `--banner`**: Referenced only indirectly (via the `copilot help logging`/`copilot help monitoring` topic names in the command reference's topic list); exact flag syntax was not found on the fetched docs.github.com pages and should be confirmed by running `copilot help` (or `copilot help logging`) against the exact installed CLI version before relying on them in a script.
- **`--system-prompt-file` or equivalent**: No such flag exists in the documentation reviewed; the supported mechanism for injecting persistent/system-level instructions is the instruction-file convention (`.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, user-level `$HOME/.copilot/copilot-instructions.md`, and `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`), not a single-file CLI flag.
- **Version drift**: The CLI is explicitly described by GitHub as evolving; the docs pages fetched are undated snapshots of the current docs.github.com content (accessed 2026-09-19), and flag availability may differ from older or newer CLI releases (e.g. the local bundled version is `1.0.87-0`). Where possible, confirm behavior with `copilot --version` and `copilot help` against the CLI version actually installed in the automation environment.
- **`copilot help environment`**: The docs point to this in-CLI command as the authoritative, complete list of environment variables; only a subset (`COPILOT_ALLOW_ALL`, `COPILOT_MODEL`, `COPILOT_HOME`, `COPILOT_AUTO_UPDATE`, `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`, `COPILOT_OFFLINE`) was documented on the web pages fetched in this research pass.
- **GitHub Actions automation page**: [Automating tasks with Copilot CLI and GitHub Actions](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/automate-with-actions) was linked from the programmatic-usage doc but not separately fetched/verified in this research pass; consult it directly for more Actions-specific guidance.
