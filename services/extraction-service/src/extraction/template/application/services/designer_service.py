"""Use cases of the template designer (IDP-03 ch. 2.9 and 2.10): samples, AI suggestions, test runs, and the mobile
template bundle.

Samples and test runs are recorded here and done by a pipeline worker (``DesignerTasksPort``), because the API
image has no OCR. Suggestions read the prepared sample's page and OCR words and ask ai-gateway's ``template-suggest``;
they are returned for the designer, not saved. The same visibility rules as templates apply (SEC-05).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from extraction.core.clock import Clock
from extraction.core.ids import new_id
from extraction.template.application.commands import AddSamplesCommand, Caller
from extraction.template.application.services.template_service import (
    UnitFactory,
    visible_template,
    writable_template,
)
from extraction.template.domain.designer import (
    MAX_SAMPLES_PER_VERSION,
    Bundle,
    Sample,
    SampleFile,
    SampleStatus,
    TestRun,
    TestRunStatus,
    check_expected,
    check_sample_files,
    definition_hash,
)
from extraction.template.domain.exceptions import (
    DefinitionInvalidError,
    SampleNotReadyError,
    SamplesMissingError,
    TestRunNotFoundError,
)
from extraction.template.domain.models import Template, VersionState
from extraction.template.domain.ports import (
    DesignerTasksPort,
    SampleStoragePort,
    Suggestion,
    TemplateQuery,
    TemplateSuggesterPort,
)

BUNDLE_FORMAT = "idp-template-bundle.v1"
_BUNDLE_LIMIT = 500


class DesignerService:
    # every collaborator is a port or settings
    def __init__(
        self,
        units: UnitFactory,
        clock: Clock,
        *,
        tasks: DesignerTasksPort,
        storage: SampleStoragePort,
        suggester: TemplateSuggesterPort,
        bundle_thresholds: Mapping[str, Any],
    ) -> None:
        self._units = units
        self._clock = clock
        self._tasks = tasks
        self._storage = storage
        self._suggester = suggester
        self._thresholds = dict(bundle_thresholds)

    # --- Samples ------------------------------------------------------------------------------------------

    async def add_samples(self, command: AddSamplesCommand) -> list[Sample]:
        async with self._units() as unit:
            template = writable_template(await unit.templates.get(command.template_id), command.caller)
            version = template.version(command.version)
            keys = {f["key"] for f in version.definition.get("fields", [])}
            existing = await unit.samples.for_version(template.id, command.version)
            if len(existing) + len(command.samples) > MAX_SAMPLES_PER_VERSION:
                raise DefinitionInvalidError([("samples", f"at most {MAX_SAMPLES_PER_VERSION} per version")])
            added = []
            for files, expected in command.samples:
                check_sample_files(list(files), account_id=template.account_id)
                check_expected(dict(expected), keys)
                sample = Sample(
                    id=new_id(),
                    template_id=template.id,
                    version=command.version,
                    files=tuple(files),
                    expected=dict(expected),
                    status=SampleStatus.PENDING,
                    created_at=self._clock.now(),
                )
                await unit.samples.add(sample)
                added.append(sample)
            await unit.commit()
        for sample in added:  # after the commit (TX-03); a lost task leaves the sample PENDING, shown as such
            await self._tasks.prepare_sample(sample.id)
        return added

    async def list_samples(self, caller: Caller, template_id: UUID, version: int) -> Sequence[Sample]:
        async with self._units() as unit:
            template = visible_template(await unit.templates.get(template_id), caller)
            template.version(version)
            return await unit.samples.for_version(template.id, version)

    # --- Suggestions --------------------------------------------------------------------------------------

    async def suggest(self, caller: Caller, template_id: UUID, version: int, sample_id: UUID | None) -> Suggestion:
        samples = await self.list_samples(caller, template_id, version)
        chosen = next((s for s in samples if sample_id is None or s.id == sample_id), None)
        if chosen is None and sample_id is not None:
            raise SampleNotReadyError
        if chosen is None or chosen.status is not SampleStatus.READY or not chosen.page_key or not chosen.words_key:
            raise SampleNotReadyError
        async with self._units() as unit:
            template = visible_template(await unit.templates.get(template_id), caller)
        words = await self._storage.read_words(chosen.words_key)
        page = SampleFile(bucket=chosen.files[0].bucket, key=chosen.page_key)
        return await self._suggester.suggest(page, words, template.account_id or caller.account_id)

    # --- Test runs ----------------------------------------------------------------------------------------

    async def start_test_run(self, caller: Caller, template_id: UUID, version: int) -> TestRun:
        async with self._units() as unit:
            template = writable_template(await unit.templates.get(template_id), caller)
            definition = template.version(version).definition
            ready = [
                s
                for s in await unit.samples.for_version(template.id, version)
                if s.status is SampleStatus.READY and s.expected
            ]
            if not ready:
                raise SamplesMissingError
            run = TestRun(
                id=new_id(),
                template_id=template.id,
                version=version,
                status=TestRunStatus.QUEUED,
                definition_hash=definition_hash(definition),
                samples=[{"sampleId": str(s.id), "expected": s.expected} for s in ready],
                created_at=self._clock.now(),
            )
            await unit.test_runs.add(run)
            await unit.commit()
        await self._tasks.run_test(run.id)
        return run

    async def get_test_run(self, caller: Caller, template_id: UUID, version: int, run_id: UUID) -> TestRun:
        async with self._units() as unit:
            template = visible_template(await unit.templates.get(template_id), caller)
            run = await unit.test_runs.get(run_id)
        if run is None or run.template_id != template.id or run.version != version:
            raise TestRunNotFoundError
        return run

    # --- Mobile bundle ------------------------------------------------------------------------------------

    async def bundle(self, caller: Caller) -> Bundle:
        """Every published template the account can use, with the thresholds the phone must apply. The version is
        a hash of the content, so it changes exactly when a template is published, retired or the settings change."""
        query = TemplateQuery(
            account_id=caller.account_id,
            scope=None,
            document_type=None,
            text=None,
            state=VersionState.PUBLISHED,
            after=None,
            limit=_BUNDLE_LIMIT,
        )
        async with self._units() as unit:
            found = await unit.templates.find(query)
        templates = [_bundled(t) for t in found if t.current_version is not None]
        rules = sorted(
            {str((f.get("rule") or {}).get("id")) for t in templates for f in t["definition"]["fields"]} - {"None"}
        )
        content = {"templates": templates, "rules": rules, "thresholds": self._thresholds}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[:20]
        return Bundle(version=digest, body={"format": BUNDLE_FORMAT, "bundleVersion": digest, **content})


def _bundled(template: Template) -> dict[str, Any]:
    number = template.current_version
    version = template.version(number) if number is not None else template.versions[-1]
    return {
        "id": str(template.id),
        "version": version.number,
        "name": template.name,
        "documentType": template.document_type_code,
        "scope": _scope(template),
        "definition": version.definition,
    }


def _scope(template: Template) -> str:
    return "STANDARD" if template.is_standard else "ACCOUNT"
