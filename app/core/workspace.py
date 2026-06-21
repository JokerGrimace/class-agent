"""
Workspace management aligned with OpenClaw's workspace model.

Key concepts:
- Workspace dir holds bootstrap files as agent's "memory"
- workspace-state.json tracks bootstrap lifecycle
- Templates only for initial seeding (writeFileIfMissing)
- BOOTSTRAP.md lifecycle: created → agent follows → agent deletes → marked complete
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict


WORKSPACE_STATE_FILE = "workspace-state.json"

BOOTSTRAP_STABLE_FILES = [
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "TOOLS.md",
    "HEARTBEAT.md",
]

BOOTSTRAP_DYNAMIC_FILES = [
    "MEMORY.md",
]

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def _load_template(filename: str) -> str:
    template_path = TEMPLATES_DIR / filename
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return ""


def _write_file_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceState:
    def __init__(self, data: Optional[Dict] = None):
        d = data or {}
        self.bootstrap_seeded_at: Optional[str] = d.get("bootstrapSeededAt")
        self.setup_completed_at: Optional[str] = d.get("setupCompletedAt")
        self.agent_name: Optional[str] = d.get("agentName")
        self.agent_emoji: Optional[str] = d.get("agentEmoji")

    def to_dict(self) -> Dict:
        return {
            "bootstrapSeededAt": self.bootstrap_seeded_at,
            "setupCompletedAt": self.setup_completed_at,
            "agentName": self.agent_name,
            "agentEmoji": self.agent_emoji,
        }

    @property
    def is_bootstrap_pending(self) -> bool:
        return bool(self.bootstrap_seeded_at and not self.setup_completed_at)

    @property
    def is_fresh_workspace(self) -> bool:
        return not self.bootstrap_seeded_at and not self.setup_completed_at


class Workspace:
    def __init__(self, workspace_dir: str):
        self.dir = Path(workspace_dir)
        self.state_path = self.dir / WORKSPACE_STATE_FILE
        self._state: Optional[WorkspaceState] = None

    @property
    def state(self) -> WorkspaceState:
        if self._state is None:
            self._state = self._load_state()
        return self._state

    def _load_state(self) -> WorkspaceState:
        if self.state_path.exists():
            try:
                return WorkspaceState(json.loads(self.state_path.read_text()))
            except Exception:
                pass
        return WorkspaceState()

    def _save_state(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state.to_dict(), indent=2))

    def ensure_workspace(self) -> None:
        """Aligns with OpenClaw's ensureAgentWorkspace()"""
        self.dir.mkdir(parents=True, exist_ok=True)

        for filename in BOOTSTRAP_STABLE_FILES:
            template = _load_template(filename)
            if template:
                _write_file_if_missing(self.dir / filename, template)

        bootstrap_path = self.dir / "BOOTSTRAP.md"
        bootstrap_exists = bootstrap_path.exists()

        if self.state.is_fresh_workspace and not bootstrap_exists:
            template = _load_template("BOOTSTRAP.md")
            if template:
                _write_file_if_missing(bootstrap_path, template)
                self.state.bootstrap_seeded_at = _now_iso()
                self._save_state()

    def mark_bootstrap_complete(self) -> None:
        """Agent calls this after completing BOOTSTRAP.md workflow"""
        bootstrap_path = self.dir / "BOOTSTRAP.md"
        if bootstrap_path.exists():
            bootstrap_path.unlink()
        self.state.setup_completed_at = _now_iso()
        self._save_state()

    def get_context_files(self) -> Dict[str, str]:
        """Read bootstrap files from workspace (the agent's memory)"""
        result = {}

        all_files = BOOTSTRAP_STABLE_FILES + BOOTSTRAP_DYNAMIC_FILES

        bootstrap_path = self.dir / "BOOTSTRAP.md"
        if bootstrap_path.exists() and self.state.is_bootstrap_pending:
            all_files = all_files + ["BOOTSTRAP.md"]

        for filename in all_files:
            file_path = self.dir / filename
            if file_path.exists() and file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    result[filename.lower().replace(".md", "")] = content
                except Exception:
                    pass

        return result

    @property
    def is_bootstrap_pending(self) -> bool:
        bootstrap_path = self.dir / "BOOTSTRAP.md"
        return bootstrap_path.exists()
