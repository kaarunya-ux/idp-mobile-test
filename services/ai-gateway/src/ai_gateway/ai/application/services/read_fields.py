"""Use case: field-read (IDP-03 ch. 4.2). Reads a document's fields from its page images in one model call.

Two forms. The batch form is what extraction-service's AI fallback sends: the document type, its pages (a card's two
sides are stacked into one image) and the fields still unsettled. The single form reads one field from a crop, with
the OCR engines' readings as hints. The standard card types have tuned questions (``card_profiles``); any other type
gets a generic question built from the field labels.
"""

from collections.abc import Callable, Mapping, Sequence
from functools import partial

from ai_gateway.ai.application.answer_models import SingleFieldAnswer, reader, schema_of, values_reader, values_schema
from ai_gateway.ai.application.runner import ModelCallRunner
from ai_gateway.ai.domain.answers import card_values
from ai_gateway.ai.domain.card_profiles import PROFILES, CardProfile
from ai_gateway.ai.domain.models import (
    Answer,
    Caller,
    FieldReadQuery,
    FieldSpec,
    FieldValue,
    ImageRef,
    ModelRequest,
    ModelRole,
    Task,
)
from ai_gateway.ai.domain.ports import ImageStorePort, PromptsPort
from ai_gateway.ai.domain.prompt_text import data_block

PROMPT = "field-read"


class ReadFieldsService:
    def __init__(
        self,
        *,
        runner: ModelCallRunner,
        images: ImageStorePort,
        prompts: PromptsPort,
        profiles: Mapping[str, CardProfile] = PROFILES,
    ) -> None:
        self._runner = runner
        self._images = images
        self._prompts = prompts
        self._profiles = profiles

    async def read(self, caller: Caller, query: FieldReadQuery) -> Answer[list[FieldValue]]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load(query.pages, stack=True)
        keys = [f.key for f in query.fields]
        profile = self._profiles.get(query.document_type or "")
        hints = _readings(query.readings)
        read: Callable[[str], dict[str, str | None]]
        if profile is not None:
            text, schema, read = profile.full_prompt + hints, profile.schema, partial(card_values, profile=profile)
        else:
            text, schema, read = _generic_question(query.fields) + hints, values_schema(keys), values_reader(keys)
        request = ModelRequest(
            role=prompt.model or ModelRole.VL,
            system=prompt.system,
            text=text,
            images=images,
            schema=schema,
            schema_name="fields",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
        )
        answer = await self._runner.run(caller, Task.FIELD_READ, prompt, request, read)
        values = [FieldValue(key=key, value=answer.value.get(key)) for key in keys]  # a key it doesn't know: None
        return Answer(value=values, provenance=answer.provenance)

    async def read_one(
        self, caller: Caller, crop: ImageRef, field: FieldSpec, readings: Mapping[str, str | None]
    ) -> Answer[FieldValue]:
        prompt = self._prompts.get(PROMPT)
        images = await self._images.load([crop], stack=False)
        text = (
            'The image is a crop around one field, described in the data block "field". Read its value. Answer '
            'with a JSON object: "value" (the text, or null), "confidence" (0 to 1, how sure you are) and "reason" '
            "(a few words: why, or what made it hard).\n"
            + data_block("field", _describe(field))
            + _readings({field.key: readings})
        )
        request = ModelRequest(
            role=prompt.model or ModelRole.VL,
            system=prompt.system,
            text=text,
            images=images,
            schema=schema_of(SingleFieldAnswer),
            schema_name="field",
            temperature=prompt.temperature,
            max_tokens=prompt.max_tokens,
        )
        answer = await self._runner.run(caller, Task.FIELD_READ, prompt, request, reader(SingleFieldAnswer))
        found = answer.value
        value = FieldValue(key=field.key, value=found.value, confidence=found.confidence, reason=found.reason)
        return Answer(value=value, provenance=answer.provenance)


def _generic_question(fields: Sequence[FieldSpec]) -> str:
    keys = ", ".join(f.key for f in fields)  # keys are checked at the API: lower snake_case only
    return (
        'Read the fields described in the data block "fields". Answer with a JSON object with exactly these keys: '
        f"{keys}. Each value is the text as printed, or null.\n" + data_block("fields", [_describe(f) for f in fields])
    )


def _describe(field: FieldSpec) -> dict[str, str]:
    described = {"key": field.key, "label": field.label, "type": field.type, "rule": field.rule}
    return {name: value for name, value in described.items() if value}


def _readings(readings: Mapping[str, Mapping[str, str | None]]) -> str:
    """What the OCR engines read, as hints. Empty when there are none."""
    useful = {key: dict(by_slot) for key, by_slot in readings.items() if any(by_slot.values())}
    return "\nOCR readings, possibly wrong:\n" + data_block("ocr_readings", useful) if useful else ""
