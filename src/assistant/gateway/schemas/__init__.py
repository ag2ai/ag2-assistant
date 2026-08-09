"""Response models for the gateway's JSON routes.

One module per domain, mirroring ``web/src/schemas/`` file for file, so the
front-end schema paired with a body is found by name. A model's module follows
its zod twin, not the rollout phase it landed in.

Naming: a whole HTTP response body takes the ``Response`` suffix
(``ProfileListResponse``); an entity nested inside a body takes ``Out``
(``UsageRowOut``).

Models inherit BaseModel directly: the model IS the contract, so a key it does
not declare does not reach the client. Completeness rests on the zod gate (a
field declared in zod and missing here fails CI), on the front end stripping
what its own schema omits, and on the per-phase body tests — not on letting
undeclared keys through.
"""

from .chat import (
    ChatListResponse,
    ChatRowOut,
    MessageResponse,
    TranscriptMessageOut,
    TranscriptResponse,
)
from .permission import TaskRulesResponse
from .primitives import ERROR_RESPONSES, ErrorBody, Ok
from .settings import (
    HealthChannelOut,
    HealthCheckOut,
    HealthMcpServerOut,
    ProfileHealthResponse,
)
from .system import (
    CatalogModelOut,
    CodingAgentOut,
    CodingAgentsResponse,
    CodingCatalogResponse,
    FsDirOut,
    FsListingErrorOut,
    FsListingOkOut,
    FsMkdirResponse,
    HealthResponse,
    HealthStateOut,
    IdentitySeededResponse,
    MemoryDocResponse,
    StatusRowOut,
    UsageResponse,
    UsageRollupResponse,
    UsageRowOut,
    UsageTotalsOut,
)
from .task import (
    InquiryListResponse,
    InquiryOut,
    NewTaskEnvelopeResponse,
    RunDetailEnvelopeResponse,
    RunDetailOut,
    RunListResponse,
    RunOut,
    RunStatusOut,
    ScheduleOut,
    TaskEnvelopeResponse,
    TaskListResponse,
    TaskOut,
    TaskWithRunsOut,
)

__all__ = [
    "ERROR_RESPONSES",
    "CatalogModelOut",
    "ChatListResponse",
    "ChatRowOut",
    "CodingAgentOut",
    "CodingAgentsResponse",
    "CodingCatalogResponse",
    "ErrorBody",
    "FsDirOut",
    "FsListingErrorOut",
    "FsListingOkOut",
    "FsMkdirResponse",
    "HealthChannelOut",
    "HealthCheckOut",
    "HealthMcpServerOut",
    "HealthResponse",
    "HealthStateOut",
    "IdentitySeededResponse",
    "InquiryListResponse",
    "InquiryOut",
    "MemoryDocResponse",
    "MessageResponse",
    "NewTaskEnvelopeResponse",
    "Ok",
    "ProfileHealthResponse",
    "RunDetailEnvelopeResponse",
    "RunDetailOut",
    "RunListResponse",
    "RunOut",
    "RunStatusOut",
    "ScheduleOut",
    "StatusRowOut",
    "TaskEnvelopeResponse",
    "TaskListResponse",
    "TaskOut",
    "TaskRulesResponse",
    "TaskWithRunsOut",
    "TranscriptMessageOut",
    "TranscriptResponse",
    "UsageResponse",
    "UsageRollupResponse",
    "UsageRowOut",
    "UsageTotalsOut",
]
