"""The per-type catalogue of Text model parameters, checked against the real AG2."""

from typing import get_args

import pytest
from ag2.config.gemini.gemini_client import ThinkingLevel
from ag2.config.openai.openai_client import ReasoningEffort

from assistant.agent import model_config
from assistant.config import Config
from assistant.llm_configs import TYPES, LlmConfigStore
from assistant.model_params import _REGISTRY, params_for

OFFERED = [(ctype, param) for ctype in TYPES for param in params_for(ctype)]


def test_every_type_states_what_it_offers():
    """A type registered empty is an answer; a type missing from the catalogue is not."""
    assert set(_REGISTRY) == set(TYPES)


def test_reasoning_levels_are_the_pinned_ag2_s_own():
    by_id = {(ctype, p.id): p for ctype, p in OFFERED}
    assert by_id["openai", "reasoning_effort"].choices == get_args(ReasoningEffort)
    assert by_id["gemini", "thinking_level"].choices == get_args(ThinkingLevel)


@pytest.mark.parametrize(("ctype", "param"), OFFERED, ids=lambda v: getattr(v, "id", v))
def test_every_offered_parameter_is_one_the_provider_configuration_takes(ctype, param, paths):
    """AG2 raises on a constructor name it does not have, so an id that drifted fails here."""
    value = param.choices[0]
    entry = LlmConfigStore(paths).save_config(
        {"name": "M", "type": ctype, "model": "m", "params": {param.id: value}}
    )
    cfg = Config.for_paths(paths)
    LlmConfigStore(paths).derive_onto(cfg, entry)
    assert getattr(model_config(cfg), param.id) == value
