from pathlib import Path

import yaml

from app.core.governance.types import GovernanceConfig


class GovernanceConfigLoader:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> GovernanceConfig:
        with self.path.open("r", encoding="utf-8") as file_handle:
            payload = yaml.safe_load(file_handle) or {}
        return GovernanceConfig.model_validate(payload)
