"""The three AI tasks the app runs through the fallback chain."""

from app.ai.client import AITask
from app.ai.contexts import AlternativesContext, ChangeContext, PersonalizeContext
from app.ai.fallbacks import alternatives_fallback, interpret_fallback, personalize_fallback
from app.ai.guardrails import check_alternatives, check_intent, check_suggestions
from app.ai.prompts import alternatives, interpret_change, personalize
from app.ai.schemas import Alternatives, ChangeIntent, PlanSuggestions

DAY_S = 86_400

PERSONALIZE: AITask[PersonalizeContext, PlanSuggestions] = AITask(
    name="personalize",
    version=personalize.VERSION,
    system=personalize.SYSTEM,
    output=PlanSuggestions,
    temperature=0.8,
    max_output_tokens=1_500,
    cache_ttl_s=7 * DAY_S,
    build_payload=personalize.build_payload,
    few_shots=personalize.FEW_SHOTS,
    check=check_suggestions,
    fallback=personalize_fallback,
)

INTERPRET_CHANGE: AITask[ChangeContext, ChangeIntent] = AITask(
    name="interpret_change",
    version=interpret_change.VERSION,
    system=interpret_change.SYSTEM,
    output=ChangeIntent,
    temperature=0.1,
    max_output_tokens=600,
    cache_ttl_s=DAY_S,
    build_payload=interpret_change.build_payload,
    few_shots=interpret_change.FEW_SHOTS,
    check=check_intent,
    fallback=interpret_fallback,
)

SUGGEST_ALTERNATIVES: AITask[AlternativesContext, Alternatives] = AITask(
    name="alternatives",
    version=alternatives.VERSION,
    system=alternatives.SYSTEM,
    output=Alternatives,
    temperature=0.6,
    max_output_tokens=700,
    cache_ttl_s=DAY_S,
    build_payload=alternatives.build_payload,
    few_shots=alternatives.FEW_SHOTS,
    check=check_alternatives,
    fallback=alternatives_fallback,
)
