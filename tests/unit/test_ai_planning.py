from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from sheetpilot.ai.models import (
    PlanApproval,
    PlanningColumnContext,
    PlanningRequest,
    PlanningSheetContext,
    PlanningSourceContext,
)
from sheetpilot.ai.planner import (
    AIPlanner,
    RuleBasedPlanner,
    approve_plan,
    approve_provider_disclosure,
)
from sheetpilot.ai.privacy_filter import DisclosureLevel, PrivacyConsentError
from sheetpilot.ai.provider import ProviderCallApproval, ProviderRequest
from sheetpilot.core.exceptions import InvalidPlanError
from sheetpilot.core.plan_schema import (
    OutputSettings,
    PrivacyMetadata,
    PrivacyMode,
    SourceReference,
)
from sheetpilot.operations.registry import build_default_registry


class FakeProvider:
    def __init__(self, response: str, *, remote: bool = True) -> None:
        self.provider_id = "test-provider"
        self.is_remote = remote
        self.response = response
        self.calls: list[ProviderRequest] = []

    def generate_plan(self, request: ProviderRequest) -> str:
        self.calls.append(request)
        return self.response


def make_request(
    instruction: str = "Trim and collapse spaces in Name then sort by Created descending",
    *,
    privacy: PrivacyMetadata | None = None,
) -> PlanningRequest:
    source_id = uuid4()
    source = PlanningSourceContext(
        reference=SourceReference(
            source_id=source_id,
            file_name="Client ada@example.com.xlsx",
            sha256="a" * 64,
            sheet_names=["Data"],
        ),
        sheets=(
            PlanningSheetContext(
                name="Data",
                used_rows=20,
                used_columns=5,
                columns=(
                    PlanningColumnContext(name="Name", inferred_types=("text",)),
                    PlanningColumnContext(name="Email", inferred_types=("text",)),
                    PlanningColumnContext(name="Password", inferred_types=("text",)),
                    PlanningColumnContext(name="Created", inferred_types=("date",)),
                    PlanningColumnContext(name="Amount", inferred_types=("number",)),
                ),
            ),
        ),
    )
    return PlanningRequest(
        job_name="Client cleanup",
        instruction=instruction,
        sources=(source,),
        output=OutputSettings(output_name="cleaned", format="xlsx"),
        privacy=privacy or PrivacyMetadata(),
        default_source_id=source_id,
        default_sheet="Data",
    )


def response_json(
    request: PlanningRequest,
    *,
    column: str = "Name",
    operation: str = "text.clean",
    parameters: dict[str, object] | None = None,
) -> str:
    source_id = request.sources[0].reference.source_id
    return json.dumps(
        {
            "schema_version": "1.0",
            "steps": [
                {
                    "step_id": "clean-1",
                    "operation": operation,
                    "parameters": parameters
                    or {"columns": [column], "actions": [{"kind": "trim"}]},
                    "target": {
                        "source_id": str(source_id),
                        "sheet": "Data",
                        "columns": [column],
                    },
                    "risk_level": "low",
                    "destructive": False,
                    "confirmation_required": False,
                    "explanation": "Trim selected text values.",
                    "depends_on": [],
                }
            ],
            "validations": [],
        }
    )


def ai_request(instruction: str = "Trim Name") -> PlanningRequest:
    return make_request(
        instruction,
        privacy=PrivacyMetadata(
            mode=PrivacyMode.AI_ASSISTED,
            metadata_upload_consent=True,
        ),
    )


def test_local_rule_parser_builds_valid_chained_plan_and_requires_approval() -> None:
    proposal = RuleBasedPlanner(build_default_registry()).plan(make_request())

    assert [step.operation for step in proposal.plan.steps] == ["text.clean", "rows.sort"]
    assert proposal.plan.steps[1].depends_on == [proposal.plan.steps[0].step_id]
    assert proposal.requires_human_approval

    with pytest.raises(InvalidPlanError, match="not approved"):
        approve_plan(
            proposal,
            PlanApproval(plan_digest=proposal.plan_digest, approved=False),
        )
    approved = approve_plan(
        proposal,
        PlanApproval(plan_digest=proposal.plan_digest, approved=True),
    )
    assert approved.plan == proposal.plan


def test_local_rule_parser_marks_removal_destructive() -> None:
    proposal = RuleBasedPlanner(build_default_registry()).plan(
        make_request("Remove duplicates using Email")
    )
    step = proposal.plan.steps[0]
    assert step.operation == "duplicates.handle"
    assert step.destructive
    assert step.confirmation_required


def test_local_rule_parser_rejects_unknown_or_ambiguous_clauses() -> None:
    planner = RuleBasedPlanner(build_default_registry())
    with pytest.raises(InvalidPlanError, match="could not safely interpret"):
        planner.plan(make_request("Email the finished workbook to the customer"))


def test_remote_provider_is_blocked_in_local_mode_without_a_call() -> None:
    request = make_request("Trim Name")
    provider = FakeProvider(response_json(request))
    planner = AIPlanner(build_default_registry(), provider)

    with pytest.raises(PrivacyConsentError, match="Local mode"):
        planner.prepare(request)
    assert provider.calls == []


def test_provider_call_requires_digest_bound_disclosure_approval() -> None:
    request = ai_request()
    provider = FakeProvider(response_json(request))
    planner = AIPlanner(build_default_registry(), provider)
    prepared = planner.prepare(request)

    denied = approve_provider_disclosure(prepared, approved=False)
    with pytest.raises(InvalidPlanError, match="not approved"):
        planner.generate(prepared, denied)
    with pytest.raises(InvalidPlanError, match="changed after review"):
        planner.generate(
            prepared,
            ProviderCallApproval(provider_request_digest="b" * 64, approved=True),
        )
    assert provider.calls == []

    proposal = planner.generate(
        prepared,
        approve_provider_disclosure(prepared, approved=True),
    )
    assert len(provider.calls) == 1
    assert proposal.requires_human_approval
    assert proposal.plan.job_id == request.job_id
    assert proposal.plan.source_files == request.source_references
    assert proposal.plan.output == request.output


def test_provider_receives_redacted_metadata_without_client_filename() -> None:
    request = ai_request("Trim Name for ada@example.com or +91 98765 43210")
    provider = FakeProvider(response_json(request))
    prepared = AIPlanner(build_default_registry(), provider).prepare(request)

    context = json.loads(prepared.provider_request.context_json)
    serialized = prepared.provider_request.context_json
    assert context["sources"][0]["file_label"] == "source_1.xlsx"
    assert "Client ada@example.com.xlsx" not in serialized
    assert "ada@example.com" not in serialized
    assert "98765 43210" not in serialized
    assert "[REDACTED]" in serialized


def test_anonymized_samples_do_not_disclose_cell_values() -> None:
    request = ai_request()
    source_id = request.sources[0].reference.source_id
    provider = FakeProvider(response_json(request))
    prepared = AIPlanner(build_default_registry(), provider).prepare(
        request,
        disclosure_level=DisclosureLevel.ANONYMIZED_SAMPLES,
        samples={
            source_id: {
                "Data": [{"Name": "Ada Lovelace", "Email": "ada@example.com", "Amount": 123.5}]
            }
        },
    )

    payload = prepared.provider_request.context_json
    assert "Ada Lovelace" not in payload
    assert "ada@example.com" not in payload
    assert "123.5" not in payload
    assert "<text:length=12>" in payload
    assert "[REDACTED]" in payload
    assert prepared.disclosure.sample_row_count == 1
    assert not prepared.disclosure.includes_raw_values


def test_raw_samples_require_separate_consent_and_redact_sensitive_columns() -> None:
    request = ai_request()
    source_id = request.sources[0].reference.source_id
    provider = FakeProvider(response_json(request))
    samples = {source_id: {"Data": [{"Name": "Ada", "Password": "do-not-send"}]}}
    with pytest.raises(PrivacyConsentError, match="Raw-data upload consent"):
        AIPlanner(build_default_registry(), provider).prepare(
            request,
            disclosure_level=DisclosureLevel.RAW_SAMPLES,
            samples=samples,
        )

    consented = make_request(
        "Trim Name",
        privacy=PrivacyMetadata(
            mode=PrivacyMode.AI_ASSISTED,
            metadata_upload_consent=True,
            raw_data_upload_consent=True,
        ),
    )
    consented_id = consented.sources[0].reference.source_id
    prepared = AIPlanner(build_default_registry(), FakeProvider(response_json(consented))).prepare(
        consented,
        disclosure_level=DisclosureLevel.RAW_SAMPLES,
        samples={consented_id: {"Data": [{"Name": "Ada", "Password": "do-not-send"}]}},
    )
    assert "Ada" in prepared.provider_request.context_json
    assert "do-not-send" not in prepared.provider_request.context_json
    assert "[REDACTED]" in prepared.provider_request.context_json
    assert prepared.disclosure.includes_raw_values


@pytest.mark.parametrize(
    ("column", "operation", "parameters", "message"),
    [
        ("Missing", "text.clean", None, "unanalysed column"),
        (
            "Name",
            "text.clean",
            {"columns": ["Name"], "actions": [{"kind": "trim"}], "code": "print(1)"},
            "forbidden",
        ),
        ("Name", "unknown.operation", {}, "Unknown operation"),
    ],
)
def test_provider_plan_still_passes_target_and_registry_validation(
    column: str,
    operation: str,
    parameters: dict[str, object] | None,
    message: str,
) -> None:
    request = ai_request()
    provider = FakeProvider(
        response_json(request, column=column, operation=operation, parameters=parameters)
    )
    planner = AIPlanner(build_default_registry(), provider)
    prepared = planner.prepare(request)
    with pytest.raises(InvalidPlanError, match=message):
        planner.generate(prepared, approve_provider_disclosure(prepared, approved=True))


def test_provider_markdown_and_extra_plan_metadata_are_rejected() -> None:
    request = ai_request()
    for response in (
        f"```json\n{response_json(request)}\n```",
        json.dumps({"schema_version": "1.0", "steps": [], "validations": [], "job_name": "x"}),
    ):
        provider = FakeProvider(response)
        planner = AIPlanner(build_default_registry(), provider)
        prepared = planner.prepare(request)
        with pytest.raises(InvalidPlanError):
            planner.generate(prepared, approve_provider_disclosure(prepared, approved=True))


def test_plan_approval_rejects_a_different_digest() -> None:
    proposal = RuleBasedPlanner(build_default_registry()).plan(make_request("Sort by Created"))
    with pytest.raises(InvalidPlanError, match="changed after review"):
        approve_plan(proposal, PlanApproval(plan_digest="f" * 64, approved=True))


def test_local_provider_can_run_offline_without_upload_consent() -> None:
    request = make_request("Trim Name")
    provider = FakeProvider(response_json(request), remote=False)
    planner = AIPlanner(build_default_registry(), provider)
    prepared = planner.prepare(request)
    proposal = planner.generate(
        prepared,
        approve_provider_disclosure(prepared, approved=True),
    )
    assert proposal.origin == "local_provider"
    assert len(provider.calls) == 1


def test_samples_cannot_reference_unanalysed_sources() -> None:
    request = ai_request()
    provider = FakeProvider(response_json(request))
    with pytest.raises(PrivacyConsentError, match="unanalysed source"):
        AIPlanner(build_default_registry(), provider).prepare(
            request,
            disclosure_level=DisclosureLevel.ANONYMIZED_SAMPLES,
            samples={UUID(int=0): {"Data": [{"Name": "Ada"}]}},
        )
