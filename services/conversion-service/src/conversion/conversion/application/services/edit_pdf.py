"""``PDF_EDIT``: gather what the plan needs, decrypt its passwords, and hand it to the editor (IDP-03 ch. 3.5).

The plan travels in ``options.editPlan``. Its first input is the document (``self``); pages it takes from other
documents come as further inputs, matched by the document id in their storage key. Images for ``SIGNATURE`` and
``INSERT_IMAGE`` are fetched from storage by key. Passwords are decrypted here, just before use, and live only in
memory (IDP-03 ch. 3.5: never logged, never stored).
"""

import dataclasses
from pathlib import Path

from conversion.conversion.application.services.run_job import JobInput, JobOutput, document_id_of
from conversion.conversion.domain.edit_plan import SELF, EditPlan, Protect
from conversion.conversion.domain.edit_plan_parser import parse_edit_plan
from conversion.conversion.domain.exceptions import InvalidPlanError, LimitExceededError
from conversion.conversion.domain.ports import EditJob, ObjectStoragePort, PdfEditorPort, SecretsPort
from conversion.conversion.domain.values import ObjectRef

EDIT_PLAN_OPTION = "editPlan"


class EditHandler:
    def __init__(
        self,
        editor: PdfEditorPort,
        storage: ObjectStoragePort,
        secrets: SecretsPort,
        *,
        max_image_bytes: int,
    ) -> None:
        self._editor = editor
        self._storage = storage
        self._secrets = secrets
        self._max_image_bytes = max_image_bytes

    def run(self, job: JobInput) -> JobOutput:
        plan = parse_edit_plan(job.request.options.get(EDIT_PLAN_OPTION))
        documents = self._documents(job, plan)
        images = {key: self._image(key, job.work_dir / f"image-{n}") for n, key in enumerate(sorted(plan.image_keys))}
        outputs, pages = self._editor.edit(
            EditJob(plan=self._decrypted(plan), documents=documents, images=images), job.output_dir
        )
        return JobOutput(files=outputs, page_count=pages)

    def _documents(self, job: JobInput, plan: EditPlan) -> dict[str, Path]:
        documents = {SELF: job.files[0]}
        for path, source in zip(job.files[1:], job.request.inputs[1:], strict=True):
            document_id = document_id_of(source.ref.key)
            if document_id is not None:
                documents[document_id] = path
        missing = plan.document_ids - set(documents)
        if missing:
            raise InvalidPlanError(
                f"The plan takes pages from documents that weren't sent: {', '.join(sorted(missing))}."
            )
        return documents

    def _image(self, image_key: str, destination: Path) -> Path:
        bucket, _, key = image_key.partition("/")
        self._storage.download(ObjectRef(bucket=bucket, key=key), destination)
        if destination.stat().st_size > self._max_image_bytes:
            raise LimitExceededError(f"An inserted image is larger than {self._max_image_bytes:,} bytes.")
        return destination

    def _decrypted(self, plan: EditPlan) -> EditPlan:
        operations = tuple(
            dataclasses.replace(
                op,
                user_password=self._secrets.decrypt(op.user_password) if op.user_password else "",
                owner_password=self._secrets.decrypt(op.owner_password),
            )
            if isinstance(op, Protect)
            else op
            for op in plan.operations
        )
        password = self._secrets.decrypt(plan.password) if plan.password else None
        return dataclasses.replace(plan, operations=operations, password=password)
