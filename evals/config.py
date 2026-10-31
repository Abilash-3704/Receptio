"""Eval configuration, read from the environment (EVAL_ prefix; see .env.example).

Prices are configuration, never constants in the code that computes cost. The defaults below are
PLACEHOLDERS so that a dry run can show an order of magnitude; they are not provider prices. Set
the EVAL_PRICE_* variables from the provider's current price list for real numbers. The report says
which it used."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.config import AgentSettings
from evals.metering import PriceBook

PRICE_FIELDS = {
    "price_main_input_per_mtok",
    "price_main_output_per_mtok",
    "price_fast_input_per_mtok",
    "price_fast_output_per_mtok",
}


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EVAL_", extra="ignore")

    # The model that plays the simulated Caller (a cheap one is enough).
    sim_model: str = "claude-haiku-5-5"

    # USD per million tokens. PLACEHOLDER defaults (see the module docstring).
    price_main_input_per_mtok: float = 3.0  # AgentSettings.llm_model
    price_main_output_per_mtok: float = 15.0
    price_fast_input_per_mtok: float = 1.0  # AgentSettings.llm_model_fast and sim_model
    price_fast_output_per_mtok: float = 5.0

    # Hard caps for one real run. Whichever is hit first aborts the run and the report says so.
    max_requests: int = 150
    max_cost_usd: float = 1.0

    # Worst-case assumptions for the dry-run estimate only.
    est_input_tokens_per_request: int = 3000
    est_output_tokens_per_request: int = 150
    est_requests_per_turn: int = 4  # agent steps (tool calls and the reply) plus the Caller

    # Text mode has no audio, so a Call's length is assumed from its turns. PLACEHOLDER.
    seconds_per_caller_turn: float = 20.0

    @property
    def prices_are_placeholders(self) -> bool:
        return not (PRICE_FIELDS & self.model_fields_set)

    def price_book(self, agent: AgentSettings) -> PriceBook:
        main = (self.price_main_input_per_mtok, self.price_main_output_per_mtok)
        fast = (self.price_fast_input_per_mtok, self.price_fast_output_per_mtok)
        per_mtok = {agent.llm_model_fast: fast, self.sim_model: fast, agent.llm_model: main}
        return PriceBook(per_mtok, placeholder=self.prices_are_placeholders)
