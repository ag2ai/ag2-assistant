"""What a route module needs from the app it lives in.

Collaborators are built per create_app() call — tests stand up dozens of apps
over different Paths, and module-level state is forbidden outright
(tests/test_no_global_defaults.py). So a route module cannot import them; it
receives them, and closes over this object in its build_router() factory. That
also keeps a move mechanical: handler bodies reference the same names they did
inside create_app.
"""

from dataclasses import dataclass
from pathlib import Path

from assistant.codex_auth import CodexAuth
from assistant.coding.detect import BridgeEndpoint
from assistant.coding.model_catalog import ModelCatalog
from assistant.connections import ConnectionStore
from assistant.gateway.profile_manager import ProfileManager
from assistant.integrations.google_auth import GoogleAuth
from assistant.live_configs import LiveConfigStore
from assistant.llm_configs import LlmConfigStore
from assistant.pairing import PairingStore
from assistant.paths import Paths
from assistant.peers import PeerStore
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore


@dataclass(frozen=True)
class GatewayDeps:
    """Collaborators create_app builds once and hands to every route module.

    Every field is a store or client from ``assistant.*``; none of those modules
    import the gateway, so naming their types here costs no import cycle.
    """

    manager: ProfileManager
    paths: Paths
    registry: ProfileRegistry
    secret_store: SecretStore
    llm_store: LlmConfigStore
    live_store: LiveConfigStore
    connection_store: ConnectionStore
    pairing_store: PairingStore
    peer_store: PeerStore
    codex: CodexAuth
    google: GoogleAuth
    catalog: ModelCatalog
    # None means local subprocess mode — no host bridge stands in for spawns.
    acp_bridge: BridgeEndpoint | None
    search_path: list[Path]
    allowed_origins: set[str]
