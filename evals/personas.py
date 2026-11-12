"""Caller personas for the simulated Caller. Defined in `evals/personas.yaml`; pass another file to
`load_personas` (or `--personas` on the eval command) to add or change them."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

DEFAULT_PERSONAS = Path(__file__).parent / "personas.yaml"


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # Instructions to the model on how this Caller talks. Inserted into its system prompt.
    style: str
    # Hangs up after saying this many things, whatever the scenario allows (a short fuse).
    max_turns: int | None = None


def load_personas(path: Path = DEFAULT_PERSONAS) -> dict[str, Persona]:
    personas = [Persona.model_validate(item) for item in yaml.safe_load(path.read_text())]
    return {p.name: p for p in personas}
