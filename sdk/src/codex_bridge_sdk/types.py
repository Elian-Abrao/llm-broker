from __future__ import annotations

from typing import Any, Literal, TypedDict

BridgeReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]
ChatRole = Literal["system", "user", "assistant", "tool_call", "tool_result"]


class ChatMessage(TypedDict, total=False):
    role: ChatRole
    content: str
    name: str
    call_id: str
    arguments: str
    output: str


class StartLoginResult(TypedDict):
    provider: Literal["codex"]
    authUrl: str
    redirectUri: str
    expiresAt: int
    manualFallback: bool


class PublicAuthSession(TypedDict, total=False):
    provider: Literal["codex"]
    accountId: str
    email: str
    planType: str
    expiresAt: int
    updatedAt: int


class ActiveLoginSnapshot(StartLoginResult, total=False):
    startedAt: int


class AuthStateSnapshot(TypedDict, total=False):
    activeLogin: ActiveLoginSnapshot
    session: PublicAuthSession
    isRefreshing: bool


class BridgeOption(TypedDict, total=False):
    id: str
    label: str
    description: str
    recommended: bool


class BridgeHealthResponse(TypedDict):
    ok: bool
    service: str


class BridgeLoginResponse(StartLoginResult):
    instructions: list[str]


class BridgeCodexCapabilitiesResponse(TypedDict, total=False):
    provider: Literal["codex"]
    billingMode: Literal["monthly"]
    requiresAuth: bool
    authenticated: bool
    accountEmail: str
    defaultModel: str
    defaultReasoningEffort: BridgeReasoningEffort
    models: list[BridgeOption]
    reasoningEfforts: list[BridgeOption]


class ToolDefinition(TypedDict, total=False):
    type: str
    name: str
    description: str
    parameters: dict[str, Any]


class BridgeChatRequest(TypedDict, total=False):
    model: str
    messages: list[ChatMessage]
    reasoningEffort: BridgeReasoningEffort
    temperature: float
    metadata: dict[str, str]
    executionMode: str
    tools: list[ToolDefinition]


class BridgeChatResponse(TypedDict):
    requestId: str
    provider: Literal["codex"]
    model: str
    outputText: str


class StreamStatusEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["status"]
    message: str


class StreamDeltaEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["delta"]
    delta: str


class StreamDoneEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["done"]


class StreamErrorEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["error"]
    message: str


class StreamToolCallStartEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["tool_call_start"]
    callId: str
    name: str


class StreamToolCallDeltaEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["tool_call_delta"]
    callId: str
    delta: str


class StreamToolCallDoneEvent(TypedDict):
    requestId: str
    provider: Literal["codex"]
    kind: Literal["tool_call_done"]
    callId: str
    name: str
    arguments: str


StreamEvent = (
    StreamStatusEvent
    | StreamDeltaEvent
    | StreamDoneEvent
    | StreamErrorEvent
    | StreamToolCallStartEvent
    | StreamToolCallDeltaEvent
    | StreamToolCallDoneEvent
)


BridgePermissionProfile = Literal["read-only", "workspace-write", "full-access"]
BridgeApprovalPolicy = Literal["manual", "auto-edit", "auto"]


class AgentToolDescriptor(TypedDict, total=False):
    name: str
    description: str
    requiresWrite: bool
    requiresFullAccess: bool


class AgentActionSnapshot(TypedDict, total=False):
    id: str
    sessionId: str
    tool: str
    input: Any
    status: str
    createdAt: int
    nextRoundIndex: int
    requiresWrite: bool
    requiresFullAccess: bool


class AgentSessionSnapshot(TypedDict, total=False):
    id: str
    mode: str
    model: str
    reasoningEffort: BridgeReasoningEffort
    permissionProfile: BridgePermissionProfile
    approvalPolicy: BridgeApprovalPolicy
    workspaceRoot: str
    cwd: str
    createdAt: int
    updatedAt: int
    messageCount: int
    status: str
    eventSequence: int
    pendingAction: AgentActionSnapshot


class AgentEvent(TypedDict, total=False):
    sessionId: str
    kind: str
    message: str
    eventId: str
    createdAt: int
    sequence: int
    statusCode: int
    tool: str
    metadata: dict[str, Any]
    action: AgentActionSnapshot
    session: AgentSessionSnapshot
    outputText: str
    reason: str


class AgentSessionCreateRequest(TypedDict, total=False):
    mode: str
    model: str
    reasoningEffort: BridgeReasoningEffort
    permissionProfile: BridgePermissionProfile
    approvalPolicy: BridgeApprovalPolicy
    cwd: str


class AgentTurnRequest(TypedDict):
    prompt: str


class AgentSessionResponse(TypedDict):
    session: AgentSessionSnapshot


class AgentTurnResponse(TypedDict):
    session: AgentSessionSnapshot
    events: list[AgentEvent]


class AgentToolsResponse(TypedDict):
    tools: list[AgentToolDescriptor]


JsonObject = dict[str, Any]
