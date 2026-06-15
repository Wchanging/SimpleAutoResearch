# Tool + MCP + Codex Agent Example

This example exercises the V2.6 external-agent path:

- SimpleAutoResearch builds the research/design context.
- `06-code` delegates greenfield implementation to Codex CLI through the
  `agent_handoff/` boundary.
- Codex writes candidate files under `generated_files/`.
- SimpleAutoResearch ingests those files, runs code review, and then validates
  results through the normal run guard.
- The same run can expose read-only experiment tools over MCP stdio.

The example is real, not a fixture-only local demo, but it is bounded for a
developer laptop. It uses local evidence notes to avoid live search flakiness;
the external implementation backend is Codex CLI.

## Run

Edit `configs/codex_greenfield.toml` first:

- keep `[implementation].agent_model = ""` unless you know the exact model name
  supported by your Codex CLI account. An empty value lets Codex use its own
  configured default model;
- set `[implementation].agent_binary` if `codex` is not on `PATH`.
- keep `[implementation].agent_mode = "handoff"` for the current bounded
  external-agent path. `delegated_workspace` is reserved for a future stronger
  external harness path and intentionally fails today.

Then run to the code stage first:

```powershell
uv run simple-ar run --config examples\tool_mcp_codex_agent\configs\codex_greenfield.toml --to-stage code
```

Inspect:

```text
runs/tool-mcp-codex-agent/<run-id>/
  agent_handoff/greenfield-codex/
    instructions.md
    tool_schema.json
    permission_policy.json
    agent_run.json
    stdout.txt
    stderr.txt
    generated_files/
  agent_outputs/greenfield-codex/
    ingestion.json
    generated_files/
  06-code/
    generated_project/
    code_backend.json
    code_review.json
```

If code review passes, continue:

```powershell
uv run simple-ar resume runs\tool-mcp-codex-agent\<run-id> --from-stage run --to-stage report --config examples\tool_mcp_codex_agent\configs\codex_greenfield.toml
```

## MCP Tool Server

After the run reaches `07-run`, expose read-only run tools over MCP stdio:

```powershell
uv run simple-ar tools serve-mcp runs\tool-mcp-codex-agent\<run-id>
```

For clients that accept MCP server JSON, use this shape and replace the run
directory:

```json
{
  "mcpServers": {
    "simple-ar-run-tools": {
      "command": "uv",
      "args": [
        "run",
        "simple-ar",
        "tools",
        "serve-mcp",
        "runs/tool-mcp-codex-agent/<run-id>"
      ]
    }
  }
}
```

You can also smoke-test the tools without an MCP client:

```powershell
uv run simple-ar tools schema --format mcp
uv run simple-ar tools call runs\tool-mcp-codex-agent\<run-id> list_experiment_artifacts
uv run simple-ar tools call runs\tool-mcp-codex-agent\<run-id> search_generated_code --args-json "{""query"": ""run_experiment"", ""max_matches"": 10}"
```
