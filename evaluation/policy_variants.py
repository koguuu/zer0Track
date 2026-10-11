"""Raja: investigation-policy experiments, not the backend's acceptance policy."""

import copy

# Proposed evaluation controls. The team's bridge must map them into its actual
# agent configuration and reject unsupported controls, never silently ignore them.
POLICY_VARIANTS = {
    "strict": {"name": "strict", "max_tool_calls": 1, "investigate_unknown": True,
               "investigate_location": False, "investigate_context": False},
    "balanced": {"name": "balanced", "max_tool_calls": 2, "investigate_unknown": True,
                 "investigate_location": True, "investigate_context": False},
    "loose": {"name": "loose", "max_tool_calls": 3, "investigate_unknown": True,
              "investigate_location": True, "investigate_context": True},
}


def policy_config(value):
    if isinstance(value, str):
        if value not in POLICY_VARIANTS:
            raise ValueError("Choose strict, balanced or loose.")
        return copy.deepcopy(POLICY_VARIANTS[value])
    if not isinstance(value, dict) or set(value) != set(POLICY_VARIANTS["balanced"]):
        raise ValueError("Policy config must have the documented investigation fields only.")
    if not isinstance(value["name"], str) or not value["name"]:
        raise ValueError("Policy name must be nonempty text.")
    if type(value["max_tool_calls"]) is not int or not 0 <= value["max_tool_calls"] <= 3:
        raise ValueError("Tool budget must be an integer from zero to three.")
    if any(type(value[key]) is not bool for key in ("investigate_unknown", "investigate_location", "investigate_context")):
        raise ValueError("Investigation triggers must be booleans.")
    return copy.deepcopy(value)


def run_variant(dataset, policy_config, *, pipeline=None, mode="rules_and_llm"):
    """Assigned interface. Core identity/integrity/location checks stay mandatory."""
    from evaluation.run_eval import run_configuration
    return run_configuration(dataset, mode, pipeline=pipeline, policy=policy_config)
