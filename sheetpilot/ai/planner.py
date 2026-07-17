"""Orchestration for local rules and privacy-gated planning providers."""

from __future__ import annotations

from sheetpilot.ai.models import (
    ApprovedPlan,
    PlanApproval,
    PlannerResponse,
    PlanningOrigin,
    PlanningRequest,
    PlanningSheetContext,
    PlanProposal,
    plan_digest,
)
from sheetpilot.ai.privacy_filter import (
    DisclosureLevel,
    SampleRows,
    build_disclosed_context,
    disclosure_manifest,
)
from sheetpilot.ai.prompt_builder import build_provider_request
from sheetpilot.ai.provider import (
    PlanningProvider,
    PreparedProviderCall,
    ProviderCallApproval,
)
from sheetpilot.ai.response_parser import parse_planner_response
from sheetpilot.ai.rule_based_parser import parse_local_instruction
from sheetpilot.core.exceptions import InvalidPlanError, SheetPilotError
from sheetpilot.core.operation_registry import OperationRegistry
from sheetpilot.core.plan_schema import OperationPlan
from sheetpilot.core.plan_validator import PlanValidator


class PlanningProviderError(SheetPilotError):
    """A planning provider failed before returning a valid restricted response."""

    code = "planning_provider_error"


def _validate_targets(request: PlanningRequest, response: PlannerResponse) -> None:
    by_source = {source.reference.source_id: source for source in request.sources}
    for step in response.steps:
        try:
            source = by_source[step.target.source_id]
        except KeyError as error:
            raise InvalidPlanError("A plan step targets an unanalysed source.") from error
        if step.target.sheet is None:
            if len(source.sheets) != 1:
                raise InvalidPlanError("A plan step must name a sheet for a multi-sheet source.")
            sheets: tuple[PlanningSheetContext, ...] = source.sheets
        else:
            sheets = tuple(sheet for sheet in source.sheets if sheet.name == step.target.sheet)
            if not sheets:
                raise InvalidPlanError("A plan step targets an unanalysed sheet.")
        for sheet in sheets:
            if sheet.duplicate_headers and step.target.columns:
                raise InvalidPlanError("A plan step cannot target unresolved duplicate headers.")
            unknown_columns = set(step.target.columns) - set(sheet.headers)
            if unknown_columns:
                raise InvalidPlanError("A plan step targets an unanalysed column.")


def _assemble_plan(request: PlanningRequest, response: PlannerResponse) -> OperationPlan:
    return OperationPlan(
        job_id=request.job_id,
        job_name=request.job_name,
        source_files=request.source_references,
        steps=response.steps,
        validations=response.validations,
        output=request.output,
        privacy=request.privacy,
        created_at=request.created_at,
    )


def _proposal(
    request: PlanningRequest,
    response: PlannerResponse,
    registry: OperationRegistry,
    *,
    origin: PlanningOrigin,
    provider_id: str | None = None,
) -> PlanProposal:
    _validate_targets(request, response)
    plan = _assemble_plan(request, response)
    PlanValidator(registry).validate(plan)
    return PlanProposal(
        plan=plan,
        origin=origin,
        provider_id=provider_id,
        plan_digest=plan_digest(plan),
    )


def approve_plan(proposal: PlanProposal, approval: PlanApproval) -> ApprovedPlan:
    """Return an approved wrapper only for an affirmative, matching human decision."""
    if not approval.approved:
        raise InvalidPlanError("The generated plan was not approved.")
    if approval.plan_digest != proposal.plan_digest:
        raise InvalidPlanError("The plan changed after review; approval is no longer valid.")
    return ApprovedPlan(
        plan=proposal.plan,
        plan_digest=proposal.plan_digest,
        approved_at=approval.approved_at,
    )


def approve_provider_disclosure(
    prepared: PreparedProviderCall, *, approved: bool
) -> ProviderCallApproval:
    """Record a decision after displaying the prepared request and manifest."""
    return ProviderCallApproval(
        provider_request_digest=prepared.disclosure.provider_request_digest,
        approved=approved,
    )


class RuleBasedPlanner:
    """Offline planner for a small documented vocabulary of safe operations."""

    def __init__(self, registry: OperationRegistry) -> None:
        self._registry = registry

    def plan(self, request: PlanningRequest) -> PlanProposal:
        response = parse_local_instruction(request)
        return _proposal(
            request,
            response,
            self._registry,
            origin=PlanningOrigin.LOCAL_RULES,
        )


class AIPlanner:
    """Prepare, disclose, approve, and validate provider-backed plan proposals."""

    def __init__(self, registry: OperationRegistry, provider: PlanningProvider) -> None:
        self._registry = registry
        self._provider = provider

    def prepare(
        self,
        request: PlanningRequest,
        *,
        disclosure_level: DisclosureLevel = DisclosureLevel.METADATA,
        samples: SampleRows | None = None,
    ) -> PreparedProviderCall:
        """Prepare but do not dispatch the exact request for a disclosure review."""
        context, sample_count = build_disclosed_context(
            request,
            provider_id=self._provider.provider_id,
            provider_is_remote=self._provider.is_remote,
            level=disclosure_level,
            samples=samples,
        )
        provider_request = build_provider_request(context, self._registry)
        manifest = disclosure_manifest(
            request,
            provider_request,
            provider_id=self._provider.provider_id,
            provider_is_remote=self._provider.is_remote,
            level=disclosure_level,
            sample_row_count=sample_count,
        )
        return PreparedProviderCall(
            planning_request=request,
            provider_request=provider_request,
            disclosure=manifest,
        )

    def generate(
        self,
        prepared: PreparedProviderCall,
        approval: ProviderCallApproval,
    ) -> PlanProposal:
        """Dispatch an approved payload and return an unapproved plan proposal."""
        if prepared.disclosure.provider_id != self._provider.provider_id:
            raise InvalidPlanError("The prepared request belongs to a different provider.")
        if prepared.disclosure.remote != self._provider.is_remote:
            raise InvalidPlanError("The provider location changed after disclosure review.")
        if not approval.approved:
            raise InvalidPlanError("The provider disclosure was not approved.")
        if approval.provider_request_digest != prepared.disclosure.provider_request_digest:
            raise InvalidPlanError(
                "The provider request changed after review; disclosure approval is invalid."
            )
        try:
            response_text = self._provider.generate_plan(prepared.provider_request)
        except Exception as error:
            raise PlanningProviderError(
                "The planning provider could not generate a plan."
            ) from error
        response = parse_planner_response(response_text)
        return _proposal(
            prepared.planning_request,
            response,
            self._registry,
            origin=(
                PlanningOrigin.REMOTE_PROVIDER
                if self._provider.is_remote
                else PlanningOrigin.LOCAL_PROVIDER
            ),
            provider_id=self._provider.provider_id,
        )
