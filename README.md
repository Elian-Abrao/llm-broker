# llm-broker

A local HTTP broker that centralizes authentication and chat access for multiple LLM providers.

## What It Is

`llm-broker` is a lightweight local Python service that sits between your application and LLM providers. It handles:

- OAuth PKCE login flows and local session persistence
- Automatic token refresh
- Unified chat and streaming endpoints across providers
- A terminal-first CLI for login, chat, and agent workflows
- A local agent runtime with sessions, permission profiles, and tool execution

Applications integrate over HTTP instead of re-implementing OAuth, callback handling, token refresh, and provider-specific request formats.

**Currently supported providers:**

| Provider | Backend | Auth |
|---|---|---|
| `codex` | OpenAI Codex via `chatgpt.com/backend-api/codex` | OAuth2 PKCE (OpenAI) |
| `gemini_cli` | Gemini Code Assist via `cloudcode-pa.googleapis.com/v1internal` | OAuth2 PKCE (Google) |

## Architecture

The package follows a strict 5-layer architecture:

```
Domain          entities, ports (interfaces), errors, policies
App             application services (AuthService, ChatService, AgentService)
Infra           HTTP gateways, OAuth adapters, storage, tools
Interfaces      CLI (argparse) and HTTP server (stdlib)
Bootstrap       config loading and runtime composition
```

Provider implementations live under `infra/providers/`:

```
src/llm_broker/infra/providers/
  codex/
    http_gateway.py       LLMGatewayPort — streaming SSE via ChatGPT backend
  gemini_cli/
    http_gateway.py       LLMGatewayPort — Code Assist `loadCodeAssist` + content generation
    auth_gateway.py       OAuthGatewayPort — Google OAuth2 PKCE
```

## Project Structure

```
src/llm_broker/
  app/                    application services and use cases
  bootstrap/              config and runtime wiring
  domain/                 entities, ports, agent policies
  infra/
    auth/                 PKCE helpers, JWT claims, callback server
    providers/
      codex/              Codex (ChatGPT) LLM gateway
      gemini_cli/         Gemini CLI LLM and OAuth gateways
    storage/              session persistence (file + keyring)
    tools/                filesystem and shell tools
  interfaces/
    cli.py                argparse CLI entrypoint
    http/                 stdlib HTTP server and route handlers
tests/
  unit/                   pure unit tests (no I/O)
  integration/            HTTP API and runtime wiring tests
  e2e/                    CLI smoke tests
sdk/                      standalone Python SDK package
docs/                     architecture, ADRs, publishing guide
```

## Setup

```bash
pip install -e .
```

With OS keyring support (recommended for token storage):

```bash
pip install -e '.[secure]'
```

Start the broker:

```bash
python -m llm_broker
# or via the installed script:
llm-broker serve
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `CODEX_BRIDGE_PORT` | `47831` | Port the broker listens on |
| `CODEX_BRIDGE_HOST` | `127.0.0.1` | Host the broker binds to |
| `GEMINI_CLIENT_ID` | _(required for Gemini)_ | Google OAuth2 client ID — find it in the [Gemini CLI public repo](https://github.com/google-gemini/gemini-cli) |
| `GEMINI_CLIENT_SECRET` | _(required for Gemini)_ | Google OAuth2 client secret — same source |
| `CODEX_BRIDGE_DISABLE_KEYRING` | unset | Set to `1` to disable OS keyring and fall back to plain file storage |

## API Endpoints

All endpoints are prefixed with `/v1`.

### Health and Discovery

```
GET  /v1/health                          → {"ok": true, "service": "codex-bridge"}
GET  /v1/providers                       → list all registered providers with capabilities
GET  /v1/providers/{provider}/options    → provider capabilities, models, and auth state
```

### Authentication

```
GET  /v1/auth/state                      → current auth state (optionally ?provider=...)
POST /v1/auth/login                      → start OAuth login flow
     body: {"provider": "codex" | "gemini_cli"}
     response: {"authUrl": "...", "manualFallback": true, ...}

POST /v1/auth/logout
     body: {"provider": "codex" | "gemini_cli"}

POST /v1/auth/complete                   → manual fallback: paste the redirect URL
     body: {"provider": "...", "redirectUrl": "http://localhost:...?code=...&state=..."}
```

### Chat

```
POST /v1/chat
     body: {
       "provider": "codex" | "gemini_cli",
       "model": "gpt-5.4" | "gemini-2.5-flash" | ...,
       "messages": [{"role": "user", "content": "..."}],
       "providerParams": {...}
     }
     response: {"outputText": "...", "provider": "...", ...}

POST /v1/chat/stream                     → same body, responds with SSE stream
```

### Agent Sessions

```
GET  /v1/agent/tools
POST /v1/agent/sessions
     body: {"permissionProfile": "read-only"|"workspace-write"|"full-access",
            "approvalPolicy": "manual"|"auto-edit"|"auto",
            "model": "...", "cwd": "..."}

GET  /v1/agent/sessions/{session_id}
POST /v1/agent/sessions/{session_id}/turns
     body: {"prompt": "..."}
POST /v1/agent/sessions/{session_id}/reset
POST /v1/agent/sessions/{session_id}/permissions
     body: {"permissionProfile": "..."}
POST /v1/agent/sessions/{session_id}/approval-policy
     body: {"approvalPolicy": "..."}
POST /v1/agent/sessions/{session_id}/actions/{action_id}/approve
POST /v1/agent/sessions/{session_id}/actions/{action_id}/reject
     body: {"reason": "..."}
```

## Streaming Event Format

`POST /v1/chat/stream` returns a Server-Sent Events (SSE) stream. Each event has `kind` as the SSE event type and a JSON data payload:

```
event: status
data: {"kind": "status", "message": "Connecting..."}

event: delta
data: {"kind": "delta", "delta": "Hello"}

event: tool_call_start
data: {"kind": "tool_call_start", "call_id": "...", "name": "..."}

event: tool_call_delta
data: {"kind": "tool_call_delta", "call_id": "...", "arguments_delta": "..."}

event: tool_call_done
data: {"kind": "tool_call_done", "call_id": "...", "name": "...", "arguments": "..."}

event: done
data: {"kind": "done", "requestId": "..."}

event: error
data: {"kind": "error", "message": "..."}
```

## Adding a New Provider

1. Create `src/llm_broker/infra/providers/{name}/` with an `__init__.py`.
2. Implement `LLMGatewayPort` (from `domain/ports.py`) in `http_gateway.py` — the key method is `stream_chat(*, request_id, session, model, messages, tools, provider_params)`.
3. If the provider needs its own auth, implement `OAuthGatewayPort` and `AuthService`; otherwise reuse an existing one.
4. Register the provider in `src/llm_broker/bootstrap/runtime.py` by creating a `ProviderEntry` and calling `registry.register(entry)`.

## CLI

```bash
llm-broker login [--provider codex|gemini_cli]
llm-broker serve
llm-broker status
llm-broker models
llm-broker models --provider gemini_cli
llm-broker whoami
llm-broker doctor
llm-broker chat "Your prompt here"
llm-broker chat --provider gemini_cli "Seu prompt"
llm-broker chat --stream "Your prompt"
llm-broker chat --interactive
llm-broker agent
llm-broker version
```

Use `--json` for machine-readable output on most commands.

Notes:

- `llm-broker models --provider gemini_cli` shows the Gemini-specific defaults and available models.
- The current Gemini default is `gemini-2.5-flash`, which is a safer default for individual Code Assist accounts than `gemini-2.5-pro`.

## Development

Run tests:

```bash
pytest tests/unit/ -q
pytest tests/integration/ -q
```

Run from source without installing:

```bash
PYTHONPATH=src python -m llm_broker serve
```

## Documentation

- [Architecture](./docs/ARCHITECTURE.md)
- [Architecture Decision Record](./docs/adr/0001-layered-architecture.md)
- [Testing](./docs/TESTING.md)
- [Publishing](./docs/PUBLISHING.md)
- [SDK](./sdk/README.md)
