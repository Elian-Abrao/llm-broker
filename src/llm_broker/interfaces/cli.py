from __future__ import annotations

import argparse
import json
import platform
import queue
import sys
import threading
import time
import webbrowser
from importlib import metadata
from pathlib import Path

from ..bootstrap.config import DEFAULT_BIND_HOST, DEFAULT_BIND_PORT, load_config
from ..bootstrap.runtime import create_runtime
from ..domain.agent import normalize_approval_policy, normalize_permission_profile
from ..domain.codex import DEFAULT_CODEX_MODEL, normalize_reasoning_effort
from ..domain.errors import BrokerError
from .http.server import run_server
from ..version import PACKAGE_VERSION


def _installed_version() -> str:
    try:
        return metadata.version("codex-bridge")
    except metadata.PackageNotFoundError:
        return PACKAGE_VERSION


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2))


def _print_auth_summary(state: dict[str, object]) -> None:
    session = state.get("session")
    if not isinstance(session, dict):
        print("Authentication: not logged in")
        active_login = state.get("activeLogin")
        if isinstance(active_login, dict):
            print("Login flow: in progress")
            print(f"Auth URL: {active_login.get('authUrl', '')}")
        return

    print("Authentication: active")
    print(f"Email: {session.get('email') or 'unknown'}")
    print(f"Plan: {session.get('planType') or 'unknown'}")
    print(f"Account ID: {session.get('accountId') or 'unknown'}")
    print(f"Expires At: {session.get('expiresAt')}")
    print(f"Updated At: {session.get('updatedAt')}")
    if state.get("isRefreshing"):
        print("Refresh: in progress")


def _print_capabilities(capabilities: dict[str, object]) -> None:
    print(f"Provider: {capabilities.get('provider', 'codex')}")
    print(f"Billing: {capabilities.get('billingMode', 'monthly')}")
    print(f"Authenticated: {'yes' if capabilities.get('authenticated') else 'no'}")
    if capabilities.get("accountEmail"):
        print(f"Account: {capabilities.get('accountEmail')}")
    print(f"Default model: {capabilities.get('defaultModel')}")
    print(f"Default reasoning: {capabilities.get('defaultReasoningEffort')}")
    models = capabilities.get("models")
    if isinstance(models, list):
        print("Models:")
        for item in models:
            if isinstance(item, dict):
                marker = " (recommended)" if item.get("recommended") else ""
                print(f"  - {item.get('id')}{marker}")
    efforts = capabilities.get("reasoningEfforts")
    if isinstance(efforts, list):
        print("Reasoning:")
        for item in efforts:
            if isinstance(item, dict):
                marker = " (recommended)" if item.get("recommended") else ""
                print(f"  - {item.get('id')}{marker}")


def _print_tools(tools: list[dict[str, object]]) -> None:
    print("Tools:")
    for item in tools:
        requirements: list[str] = []
        if item.get("requiresWrite"):
            requirements.append("workspace-write")
        if item.get("requiresFullAccess"):
            requirements.append("full-access")
        suffix = f" [{', '.join(requirements)}]" if requirements else ""
        print(f"  - {item.get('name')}{suffix}")
        description = item.get("description")
        if isinstance(description, str) and description:
            print(f"    {description}")


def _build_doctor_report(config, state: dict[str, object], capabilities: dict[str, object]) -> dict[str, object]:
    auth_store = Path(config.auth_store_path).expanduser()
    session = state.get("session") if isinstance(state, dict) else None
    return {
        "service": "codex-bridge",
        "version": _installed_version(),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "config": {
            "bindHost": config.bind_host,
            "bindPort": config.bind_port,
            "authStorePath": str(auth_store),
            "authStoreExists": auth_store.exists(),
            "keyringEnabled": config.prefer_keyring,
            "codexBaseUrl": config.codex_base_url,
            "userAgent": config.user_agent,
        },
        "auth": {
            "authenticated": isinstance(session, dict),
            "email": session.get("email") if isinstance(session, dict) else None,
            "planType": session.get("planType") if isinstance(session, dict) else None,
            "hasActiveLogin": isinstance(state.get("activeLogin"), dict),
            "isRefreshing": bool(state.get("isRefreshing")),
        },
        "capabilities": {
            "defaultModel": capabilities.get("defaultModel"),
            "defaultReasoningEffort": capabilities.get("defaultReasoningEffort"),
            "modelCount": len(capabilities.get("models", [])) if isinstance(capabilities.get("models"), list) else 0,
            "reasoningCount": len(capabilities.get("reasoningEfforts", []))
            if isinstance(capabilities.get("reasoningEfforts"), list)
            else 0,
        },
    }


def _print_doctor_report(report: dict[str, object]) -> None:
    print(f"codex-bridge {report.get('version')}")
    python_info = report.get("python")
    if isinstance(python_info, dict):
        print(f"Python: {python_info.get('version')} ({python_info.get('executable')})")
    config = report.get("config")
    if isinstance(config, dict):
        print(f"Bind: http://{config.get('bindHost')}:{config.get('bindPort')}")
        print(f"Auth store: {config.get('authStorePath')}")
        print(f"Auth store exists: {'yes' if config.get('authStoreExists') else 'no'}")
        print(f"Keyring enabled: {'yes' if config.get('keyringEnabled') else 'no'}")
        print(f"Codex base URL: {config.get('codexBaseUrl')}")
    auth = report.get("auth")
    if isinstance(auth, dict):
        print(f"Authenticated: {'yes' if auth.get('authenticated') else 'no'}")
        if auth.get("email"):
            print(f"Account: {auth.get('email')}")
        if auth.get("planType"):
            print(f"Plan: {auth.get('planType')}")
        print(f"Active login flow: {'yes' if auth.get('hasActiveLogin') else 'no'}")
    caps = report.get("capabilities")
    if isinstance(caps, dict):
        print(f"Default model: {caps.get('defaultModel')}")
        print(f"Default reasoning: {caps.get('defaultReasoningEffort')}")
        print(f"Advertised models: {caps.get('modelCount')}")
        print(f"Advertised reasoning levels: {caps.get('reasoningCount')}")


def _stream_chat_to_stdout(runtime, payload: dict[str, object]) -> dict[str, object]:
    request_id: str | None = None
    output_parts: list[str] = []
    for event in runtime.chat_service.stream_chat(payload):
        request_id = request_id or str(event.get("requestId"))
        kind = event.get("kind")
        if kind == "status":
            continue
        if kind == "delta" and isinstance(event.get("delta"), str):
            chunk = event["delta"]
            output_parts.append(chunk)
            print(chunk, end="", flush=True)
            continue
        if kind == "error":
            print()
            raise BrokerError(502, str(event.get("message") or "Codex returned an error."))
    if output_parts:
        print()
    return {
        "requestId": request_id or "",
        "provider": "codex",
        "model": str(payload.get("model") or DEFAULT_CODEX_MODEL),
        "outputText": "".join(output_parts),
    }


def _run_interactive_chat(runtime, *, model: str, reasoning: str) -> None:
    active_model = model
    active_reasoning = normalize_reasoning_effort(reasoning)
    messages: list[dict[str, str]] = []

    print("Interactive chat")
    print("Commands: /help /reset /model <name> /reasoning <level> /status /logout /exit")

    while True:
        try:
            prompt = input("codex> ").strip()
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print()
            return

        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return
        if prompt == "/help":
            print("Commands: /help /reset /model <name> /reasoning <level> /status /logout /exit")
            continue
        if prompt == "/reset":
            messages.clear()
            print("Conversation reset.")
            continue
        if prompt.startswith("/model "):
            active_model = prompt.split(" ", 1)[1].strip() or active_model
            print(f"Model set to {active_model}")
            continue
        if prompt.startswith("/reasoning "):
            active_reasoning = normalize_reasoning_effort(prompt.split(" ", 1)[1].strip())
            print(f"Reasoning set to {active_reasoning}")
            continue
        if prompt == "/status":
            print(f"Model: {active_model}")
            print(f"Reasoning: {active_reasoning}")
            print(f"Messages in context: {len(messages)}")
            continue
        if prompt == "/logout":
            runtime.auth_service.logout()
            messages.clear()
            print("Session cleared. Exiting interactive chat.")
            return

        messages.append({"role": "user", "content": prompt})
        response = _stream_chat_to_stdout(
            runtime,
            {
                "model": active_model,
                "reasoningEffort": active_reasoning,
                "messages": messages,
            },
        )
        messages.append({"role": "assistant", "content": str(response["outputText"])})


def _print_agent_session_status(session: dict[str, object]) -> None:
    print(f"Session: {session.get('id')}")
    print(f"Mode: {session.get('mode')}")
    print(f"Model: {session.get('model')}")
    print(f"Reasoning: {session.get('reasoningEffort')}")
    print(f"Permissions: {session.get('permissionProfile')}")
    print(f"Approval policy: {session.get('approvalPolicy')}")
    print(f"Workspace: {session.get('workspaceRoot')}")
    print(f"CWD: {session.get('cwd')}")
    print(f"Messages in context: {session.get('messageCount')}")
    if isinstance(session.get("pendingAction"), dict):
        pending = session["pendingAction"]
        print(f"Pending action: {pending.get('tool')} ({pending.get('id')})")

def _run_agent_turn(runtime, session_id: str, prompt: str, *, interactive_approvals: bool = False) -> dict[str, object]:
    output_parts: list[str] = []
    session_payload: dict[str, object] | None = None
    events: list[dict[str, object]] = []

    def consume(event_stream) -> None:
        nonlocal session_payload
        for event in event_stream:
            events.append(event)
            kind = event.get("kind")
            if kind == "turn.started":
                continue
            if kind == "model.status":
                print(f"[status] {event.get('message')}")
                continue
            if kind == "tool.requested":
                print(f"[agent] {event.get('message')}")
                continue
            if kind == "action.required":
                action = event.get("action")
                if isinstance(action, dict):
                    print(f"[approval] {event.get('message')}")
                    print(f"[approval] tool={action.get('tool')} input={action.get('input')}")
                    if interactive_approvals:
                        try:
                            decision = input("Approve action? [y/N]: ").strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            print()
                            decision = ""
                        if decision in {"y", "yes"}:
                            consume(runtime.agent_service.approve_action(session_id, str(action.get("id") or "")))
                        else:
                            consume(
                                runtime.agent_service.reject_action(
                                    session_id,
                                    str(action.get("id") or ""),
                                    "Rejected from interactive CLI.",
                                )
                            )
                continue
            if kind == "action.approved":
                print(f"[approval] {event.get('message')}")
                continue
            if kind == "action.rejected":
                print(f"[approval] {event.get('message')}")
                continue
            if kind == "tool.started":
                print(f"[tool] {event.get('message')}")
                continue
            if kind == "tool.output" and isinstance(event.get("message"), str):
                print(event["message"])
                continue
            if kind == "tool.completed":
                continue
            if kind == "model.delta" and isinstance(event.get("message"), str):
                chunk = event["message"]
                output_parts.append(chunk)
                print(chunk, end="", flush=True)
                continue
            if kind == "error":
                print()
                raise BrokerError(int(event.get("statusCode") or 500), str(event.get("message") or "Agent runtime failed."))
            if kind == "turn.completed":
                session_payload = event.get("session") if isinstance(event.get("session"), dict) else None

    consume(runtime.agent_service.send_turn(session_id, prompt))

    if output_parts:
        print()

    return {
        "session": session_payload or runtime.agent_service.get_session(session_id).to_dict(),
        "outputText": "".join(output_parts),
        "events": events,
    }


def _run_agent_tool(runtime, session_id: str, tool_name: str, input_payload) -> None:
    for event in runtime.agent_service.execute_tool(session_id, tool_name, input_payload):
        kind = event.get("kind")
        if kind == "tool.started":
            print(f"[tool] {event.get('message')}")
            continue
        if kind == "tool.output" and isinstance(event.get("message"), str):
            print(event["message"])
            continue
        if kind == "tool.completed":
            session = event.get("session")
            if isinstance(session, dict):
                print(f"[tool] Context updated. Messages in context: {session.get('messageCount')}")
            continue
        if kind == "error":
            raise BrokerError(int(event.get("statusCode") or 500), str(event.get("message") or "Tool execution failed."))


def _run_interactive_agent(runtime, session) -> None:
    print("Interactive agent")
    print(
        "Commands: /help /status /permissions [profile] /approvals [policy] /tools /cwd [path] /model <name> /reasoning <level> /read <path> /write <path> <content> /shell <command> /reset /logout /exit"
    )

    while True:
        try:
            prompt = input("agent> ").strip()
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print()
            return

        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            return
        if prompt == "/help":
            print(
                "Commands: /help /status /permissions [profile] /approvals [policy] /tools /cwd [path] /model <name> /reasoning <level> /read <path> /write <path> <content> /shell <command> /reset /logout /exit"
            )
            continue
        if prompt == "/status":
            _print_agent_session_status(session.to_dict())
            continue
        if prompt == "/tools":
            _print_tools(runtime.agent_service.list_tools())
            continue
        if prompt.startswith("/permissions"):
            parts = prompt.split(" ", 1)
            if len(parts) == 1 or not parts[1].strip():
                print(f"Permissions: {session.permission_profile}")
            else:
                session = runtime.agent_service.set_permissions(session.id, parts[1].strip())
                print(f"Permissions set to {session.permission_profile}")
            continue
        if prompt.startswith("/approvals"):
            parts = prompt.split(" ", 1)
            if len(parts) == 1 or not parts[1].strip():
                print(f"Approval policy: {session.approval_policy}")
            else:
                session = runtime.agent_service.set_approval_policy(session.id, parts[1].strip())
                print(f"Approval policy set to {session.approval_policy}")
            continue
        if prompt.startswith("/cwd"):
            parts = prompt.split(" ", 1)
            if len(parts) == 1 or not parts[1].strip():
                print(f"CWD: {session.cwd}")
            else:
                try:
                    session = runtime.agent_service.set_cwd(session.id, parts[1].strip())
                    print(f"CWD set to {session.cwd}")
                except BrokerError as exc:
                    print(f"[error] {exc}")
            continue
        if prompt.startswith("/model "):
            session = runtime.agent_service.set_model(session.id, prompt.split(" ", 1)[1].strip())
            print(f"Model set to {session.model}")
            continue
        if prompt.startswith("/reasoning "):
            session = runtime.agent_service.set_reasoning(session.id, prompt.split(" ", 1)[1].strip())
            print(f"Reasoning set to {session.reasoning_effort}")
            continue
        if prompt == "/reset":
            session = runtime.agent_service.reset_session(session.id)
            print("Conversation reset.")
            continue
        if prompt == "/logout":
            runtime.auth_service.logout()
            session = runtime.agent_service.reset_session(session.id)
            print("Session cleared. Exiting interactive agent.")
            return
        if prompt.startswith("/read "):
            try:
                _run_agent_tool(runtime, session.id, "read_file", prompt.split(" ", 1)[1].strip())
                session = runtime.agent_service.get_session(session.id)
            except BrokerError as exc:
                print(f"[error] {exc}")
            continue
        if prompt.startswith("/write "):
            remainder = prompt.split(" ", 1)[1].strip()
            path, _, content = remainder.partition(" ")
            if not path or not content:
                print("Usage: /write <path> <content>")
                continue
            try:
                _run_agent_tool(runtime, session.id, "write_file", {"path": path, "content": content})
                session = runtime.agent_service.get_session(session.id)
            except BrokerError as exc:
                print(f"[error] {exc}")
            continue
        if prompt.startswith("/shell "):
            try:
                _run_agent_tool(runtime, session.id, "shell", prompt.split(" ", 1)[1].strip())
                session = runtime.agent_service.get_session(session.id)
            except BrokerError as exc:
                print(f"[error] {exc}")
            continue

        try:
            result = _run_agent_turn(runtime, session.id, prompt, interactive_approvals=True)
        except BrokerError as exc:
            print(f"[error] {exc}")
            continue
        session_payload = result.get("session")
        if isinstance(session_payload, dict):
            session = runtime.agent_service.get_session(session.id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-bridge")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output when supported.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the broker.")
    serve_parser.add_argument("--host", default=DEFAULT_BIND_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_BIND_PORT)

    login_parser = subparsers.add_parser("login", help="Start the OAuth login flow.")
    login_parser.add_argument("--no-browser", action="store_true", help="Do not try to open the browser automatically.")
    subparsers.add_parser("logout", help="Clear the local Codex session.")
    subparsers.add_parser("status", help="Print broker auth state.")
    subparsers.add_parser("whoami", help="Print the currently authenticated account, if any.")
    subparsers.add_parser("doctor", help="Print a local diagnostics report.")
    subparsers.add_parser("version", help="Print the installed broker version.")
    subparsers.add_parser("models", help="List advertised models and reasoning levels.")

    chat_parser = subparsers.add_parser("chat", help="Send a one-shot chat request through the broker runtime.")
    chat_parser.add_argument("prompt", nargs="*")
    chat_parser.add_argument("--model", default=DEFAULT_CODEX_MODEL)
    chat_parser.add_argument("--reasoning", default="medium")
    chat_parser.add_argument("--interactive", action="store_true", help="Start a persistent terminal chat session.")
    chat_parser.add_argument("--stream", action="store_true", help="Stream the response directly to stdout.")

    agent_parser = subparsers.add_parser("agent", help="Start an interactive local agent session.")
    agent_parser.add_argument("prompt", nargs="*")
    agent_parser.add_argument("--model", default=DEFAULT_CODEX_MODEL)
    agent_parser.add_argument("--reasoning", default="medium")
    agent_parser.add_argument("--permissions", default="read-only")
    agent_parser.add_argument("--approval-policy", default="manual")
    agent_parser.add_argument("--cwd", default=None)

    return parser


def _collect_manual_callback_input(result_queue: "queue.SimpleQueue[str]", stop_event: threading.Event) -> None:
    if stop_event.wait(5):
        return
    try:
        value = input(
            "Automatic callback not detected yet. Paste the final redirect URL only if the browser did not return to the broker automatically: "
        ).strip()
    except EOFError:
        value = ""
    if not stop_event.is_set():
        result_queue.put(value)


def _wait_for_login_completion(runtime, *, expires_at: int, allow_manual_prompt: bool) -> dict[str, object]:
    manual_input_queue: "queue.SimpleQueue[str]" = queue.SimpleQueue()
    stop_event = threading.Event()
    manual_input_thread: threading.Thread | None = None

    if allow_manual_prompt and sys.stdin.isatty():
        manual_input_thread = threading.Thread(
            target=_collect_manual_callback_input,
            args=(manual_input_queue, stop_event),
            daemon=True,
        )
        manual_input_thread.start()

    try:
        while int(time.time() * 1000) < expires_at:
            while True:
                try:
                    redirect_url = manual_input_queue.get_nowait()
                except queue.Empty:
                    break
                if redirect_url:
                    runtime.auth_service.complete_manual_login(redirect_url)

            state = runtime.auth_service.get_state().to_dict()
            if state.get("session") or not state.get("activeLogin"):
                return state
            time.sleep(0.25)
    finally:
        stop_event.set()
        if manual_input_thread and manual_input_thread.is_alive():
            manual_input_thread.join(timeout=0.1)

    return runtime.auth_service.get_state().to_dict()


def _run_login(*, as_json: bool, open_browser: bool) -> None:
    runtime = create_runtime()
    current = runtime.auth_service.get_state().to_dict()
    if current.get("session"):
        if as_json:
            _print_json(current)
        else:
            print("A Codex session is already active.")
            _print_auth_summary(current)
        return

    login = runtime.auth_service.start_login()
    auth_url = login.auth_url
    redirect_uri = login.redirect_uri
    expires_at = login.expires_at

    opened = False if not open_browser else webbrowser.open(auth_url)
    print("No active Codex session found.")
    if open_browser:
        print("Browser opened automatically." if opened else "Open this URL in your browser:")
    else:
        print("Open this URL in your browser:")
    print(auth_url)
    print(f"Expected redirect: {redirect_uri}")
    print("Waiting for the browser callback. If the local callback succeeds, the terminal will continue automatically.")

    final_state = _wait_for_login_completion(runtime, expires_at=expires_at, allow_manual_prompt=not as_json)
    if not final_state.get("session"):
        raise BrokerError(408, "Login was not completed within the timeout.")
    if as_json:
        _print_json(final_state)
    else:
        print("Login completed.")
        _print_auth_summary(final_state)


def _run_logout(*, as_json: bool) -> None:
    runtime = create_runtime()
    runtime.auth_service.logout()
    if as_json:
        _print_json({"ok": True, "message": "Session cleared."})
    else:
        print("Session cleared.")


def _run_status(*, as_json: bool) -> None:
    runtime = create_runtime()
    state = runtime.auth_service.get_state().to_dict()
    if as_json:
        _print_json(state)
    else:
        _print_auth_summary(state)


def _run_whoami(*, as_json: bool) -> None:
    runtime = create_runtime()
    state = runtime.auth_service.get_state().to_dict()
    session = state.get("session")
    payload = {
        "authenticated": isinstance(session, dict),
        "provider": "codex",
        "email": session.get("email") if isinstance(session, dict) else None,
        "planType": session.get("planType") if isinstance(session, dict) else None,
        "accountId": session.get("accountId") if isinstance(session, dict) else None,
    }
    if as_json:
        _print_json(payload)
    elif payload["authenticated"]:
        print(f"Logged in as {payload['email'] or 'unknown'}")
        print(f"Plan: {payload['planType'] or 'unknown'}")
        print(f"Account ID: {payload['accountId'] or 'unknown'}")
    else:
        print("Not authenticated.")


def _run_doctor(*, as_json: bool) -> None:
    config = load_config()
    runtime = create_runtime(config)
    state = runtime.auth_service.get_state().to_dict()
    capabilities = runtime.chat_service.get_capabilities()
    report = _build_doctor_report(config, state, capabilities)
    if as_json:
        _print_json(report)
    else:
        _print_doctor_report(report)


def _run_version(*, as_json: bool) -> None:
    payload = {"service": "codex-bridge", "version": _installed_version()}
    if as_json:
        _print_json(payload)
    else:
        print(f"{payload['service']} {payload['version']}")


def _run_models(*, as_json: bool) -> None:
    runtime = create_runtime()
    capabilities = runtime.chat_service.get_capabilities()
    if as_json:
        _print_json(capabilities)
    else:
        _print_capabilities(capabilities)


def _run_chat(
    prompt_parts: list[str],
    model: str,
    reasoning: str,
    *,
    as_json: bool,
    interactive: bool,
    stream: bool,
) -> None:
    runtime = create_runtime()
    if interactive:
        if as_json:
            raise BrokerError(400, "`--json` is not supported with `chat --interactive`.")
        _run_interactive_chat(runtime, model=model, reasoning=reasoning)
        return
    if stream and as_json:
        raise BrokerError(400, "`--json` is not supported with `chat --stream`.")

    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        prompt = input("codex> ").strip()
    if not prompt:
        raise BrokerError(400, "Prompt cannot be empty.")

    payload = {
        "model": model,
        "reasoningEffort": normalize_reasoning_effort(reasoning),
        "messages": [{"role": "user", "content": prompt}],
    }
    response = _stream_chat_to_stdout(runtime, payload) if stream else runtime.chat_service.chat(payload)
    if as_json:
        _print_json(response)
    elif not stream:
        print(response["outputText"])


def _run_agent(
    prompt_parts: list[str],
    model: str,
    reasoning: str,
    permission_profile: str,
    approval_policy: str,
    cwd: str | None,
    *,
    as_json: bool,
) -> None:
    runtime = create_runtime()
    session = runtime.agent_service.create_session(
        mode="agent",
        model=model,
        reasoning_effort=reasoning,
        permission_profile=permission_profile,
        approval_policy=approval_policy,
        cwd=cwd,
    )

    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        if as_json:
            raise BrokerError(400, "`--json` is not supported with interactive `agent` sessions.")
        _run_interactive_agent(
            runtime,
            session,
        )
        return

    result = _run_agent_turn(runtime, session.id, prompt, interactive_approvals=False)
    payload = {
        "session": result["session"],
        "outputText": result["outputText"],
        "events": result["events"],
    }
    if as_json:
        _print_json(payload)
    else:
        print()
        _print_agent_session_status(result["session"])


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "serve":
            config = load_config(host=args.host, port=args.port)
            runtime = create_runtime(config)
            run_server(host=config.bind_host, port=config.bind_port, runtime=runtime)
            return

        if args.command == "login":
            _run_login(as_json=args.json, open_browser=not args.no_browser)
            return

        if args.command == "logout":
            _run_logout(as_json=args.json)
            return

        if args.command == "status":
            _run_status(as_json=args.json)
            return

        if args.command == "whoami":
            _run_whoami(as_json=args.json)
            return

        if args.command == "doctor":
            _run_doctor(as_json=args.json)
            return

        if args.command == "version":
            _run_version(as_json=args.json)
            return

        if args.command == "models":
            _run_models(as_json=args.json)
            return

        if args.command == "chat":
            _run_chat(
                args.prompt,
                args.model,
                args.reasoning,
                as_json=args.json,
                interactive=args.interactive,
                stream=args.stream,
            )
            return

        if args.command == "agent":
            _run_agent(
                args.prompt,
                args.model,
                args.reasoning,
                normalize_permission_profile(args.permissions),
                normalize_approval_policy(args.approval_policy),
                args.cwd,
                as_json=args.json,
            )
            return

        parser.error(f"Unsupported command: {args.command}")
    except BrokerError as exc:
        print(str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
