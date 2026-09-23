from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    cfg: dict
    corpus_dir: Path

    def secret(self, name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise SystemExit(f"{name} is not set. Add it to {ROOT / '.env'} (see .env.example).")
        return value

    def path(self, name: str) -> Path:
        p = Path(self.secret(name)).expanduser()
        p = p if p.is_absolute() else ROOT / p
        if not p.exists():
            raise SystemExit(f"{name} points to {p}, which does not exist.")
        return p


def load() -> Settings:
    load_dotenv(ROOT / ".env")
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    corpus = Path(os.getenv("CORPUS_DIR") or cfg["corpus"]["dir"]).expanduser()
    return Settings(cfg=cfg, corpus_dir=corpus if corpus.is_absolute() else ROOT / corpus)
