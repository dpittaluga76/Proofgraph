from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

STRATEGY_CATALOG_VERSION = "opportunity_strategies_v1"
MECHANISM_TAG_VOCAB_VERSION = "opportunity_mechanisms_v1"
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]


class StrategyTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: Slug
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    required_signals: tuple[str, ...] = Field(min_length=1)
    failure_conditions: tuple[str, ...] = Field(min_length=1)
    default_research_queries: tuple[str, ...] = Field(min_length=1, max_length=5)


_STRATEGY_DATA = (
    (
        "repackage_validated_demand",
        "Repackage validated demand",
        "Serve proven demand with a narrower audience, workflow, or delivery model.",
        ("buyers already spend for the outcome", "a reachable underserved segment exists"),
        ("demand depends on one temporary event", "the segment has no distinct workflow"),
        ("pricing page", "buyer complaints", "market alternatives"),
    ),
    (
        "productize_recurring_service",
        "Productize recurring service work",
        "Convert a repeated, expensive service workflow into software.",
        ("customers already pay for the outcome", "the workflow repeats", "inputs are structured"),
        ("work requires bespoke expert judgment", "delivery depends on physical labor"),
        ("service pricing", "manual workflow", "consulting package", "job description"),
    ),
    (
        "replace_critical_spreadsheet",
        "Replace a critical spreadsheet",
        "Turn a fragile spreadsheet used for recurring operational decisions into a product.",
        ("the sheet is business critical", "multiple people update it", "errors have a real cost"),
        ("usage is one-off", "the spreadsheet is already a sufficient low-risk solution"),
        ("spreadsheet template", "excel workflow", "manual reconciliation"),
    ),
    (
        "unbundle_valuable_feature",
        "Unbundle a valuable feature",
        "Extract a high-value capability from a broad suite for a focused buyer.",
        ("buyers mention one feature disproportionately", "suite pricing blocks adoption"),
        ("the feature has no standalone workflow", "platform access makes delivery impossible"),
        ("feature reviews", "pricing complaints", "alternative tools"),
    ),
    (
        "rebundle_fragmented_workflow",
        "Rebundle a fragmented workflow",
        "Combine several handoffs and tools into one coherent operational workflow.",
        ("users stitch together multiple tools", "handoffs create delay or data loss"),
        ("the tools serve unrelated owners", "integration cost exceeds workflow value"),
        ("workflow checklist", "integration request", "tool stack"),
    ),
    (
        "commercialize_open_source",
        "Commercialize an open-source project",
        "Add hosting, governance, compliance, or operations around useful open source.",
        ("the project has sustained adoption", "teams struggle to operate it"),
        ("license forbids the model", "maintainers reject the proposed ecosystem role"),
        ("github issues", "deployment guide", "managed hosting"),
    ),
    (
        "move_enterprise_capability_downmarket",
        "Move an enterprise capability downmarket",
        "Deliver a simpler enterprise capability to smaller teams with lower complexity.",
        (
            "small teams share the underlying obligation",
            "enterprise tools are too costly or complex",
        ),
        ("requirements demand enterprise services", "small teams lack budget or urgency"),
        ("enterprise pricing", "small business workaround", "compliance requirement"),
    ),
    (
        "automate_mandatory_work",
        "Automate mandatory work",
        "Reduce recurring work imposed by regulation, customers, platforms, or internal policy.",
        ("the work is unavoidable", "the same evidence or answers recur", "delay has a cost"),
        ("requirements change too unpredictably", "automation would create unacceptable liability"),
        ("required questionnaire", "compliance checklist", "audit preparation"),
    ),
    (
        "prevent_expensive_failure",
        "Prevent an expensive failure",
        "Detect or prevent a recurring failure whose expected cost supports a budget.",
        ("the failure is measurable", "buyers already fund prevention or remediation"),
        ("the event is too rare to justify action", "reliable detection is unavailable"),
        ("incident cost", "postmortem", "insurance requirement"),
    ),
    (
        "remove_scarce_expert_bottleneck",
        "Remove a scarce-expert bottleneck",
        (
            "Encode repeatable parts of expert work so specialists review exceptions instead "
            "of every case."
        ),
        ("expert queues delay work", "a repeatable first-pass decision exists"),
        ("all cases require expert judgment", "delegation is legally prohibited"),
        ("specialist backlog", "review checklist", "consulting rate"),
    ),
    (
        "exploit_disliked_pricing_model",
        "Exploit a disliked pricing model",
        "Offer a credible alternative where incumbent pricing conflicts with customer value.",
        ("pricing complaints recur", "usage and value can be priced differently"),
        ("complaints do not cause switching", "incumbent economics are unavoidable"),
        ("pricing complaints", "seat based pricing", "switching alternative"),
    ),
    (
        "build_ecosystem_infrastructure",
        "Build infrastructure around a growing ecosystem",
        (
            "Provide tooling, operations, security, or distribution for an expanding platform "
            "ecosystem."
        ),
        ("ecosystem adoption is growing", "participants repeat an unsolved enabling workflow"),
        ("the platform will absorb the capability", "the ecosystem is shrinking"),
        ("developer growth", "ecosystem tooling", "platform roadmap"),
    ),
    (
        "convert_operational_data_to_decisions",
        "Convert operational data into decisions",
        "Turn accumulated workflow data into recurring, actionable operational decisions.",
        ("useful data is already produced", "a recurring decision changes outcomes"),
        ("data quality is insufficient", "the decision has no accountable owner"),
        ("reporting workflow", "manual analysis", "decision cadence"),
    ),
    (
        "marketplace_workflow_to_saas",
        "Turn a marketplace participant workflow into SaaS",
        "Productize recurring work performed by one side of an established marketplace.",
        (
            "participants repeat the workflow across transactions",
            "the marketplace does not solve it",
        ),
        ("platform terms prohibit the integration", "participants have no off-platform workflow"),
        ("seller workflow", "marketplace tools", "participant fees"),
    ),
)

STRATEGY_TEMPLATES = tuple(
    StrategyTemplate(
        id=item[0],
        title=item[1],
        description=item[2],
        required_signals=item[3],
        failure_conditions=item[4],
        default_research_queries=item[5],
    )
    for item in _STRATEGY_DATA
)
STRATEGY_BY_ID = {strategy.id: strategy for strategy in STRATEGY_TEMPLATES}
MECHANISM_TAG_VOCABULARY = frozenset(
    {
        *STRATEGY_BY_ID,
        "repeated_work",
        "reviewer_control",
    }
)

if len(STRATEGY_TEMPLATES) != 14 or len(STRATEGY_BY_ID) != 14:
    raise RuntimeError(
        "The versioned opportunity strategy catalog must contain exactly 14 entries."
    )
