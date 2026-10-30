import abc
import string
import typing

import pydantic
import pydantic_core
from pydantic_ai import ToolOutput

from conciergent.reply import (
    BulletList,
    Card,
    Carousel,
    Fact,
    FactLabel,
    FactList,
    FactValue,
    Reply,
    Section,
    SectionText,
)


_FREE_INSTRUCTIONS = (
    'End each turn with exactly one reply, either plain text, a single reply_card, or a single reply_carousel, never a mix. '
    'Use plain text only for a short one-line answer with no list, entity, link, or follow-up. '
    'Use reply_card for anything richer, a synthesized answer, a status report, a single entity, or a plain list, '
    'and keep the whole message inside the card rather than writing text beside it. '
    'Use reply_carousel for a small set of distinct items that each deserve their own card and action, '
    'giving every option a suggestion or link so it can be chosen. '
    'Put a URL in a card link button instead of writing it inline, and offer next steps as suggestions.'
)
_CARD_TOOL_NAME = 'reply_card'
_SHAPED_INSTRUCTIONS = (
    'End the turn by calling {tool} once. '
    'Fill only its fields, since the layout, the fixed titles, and the order are set for you. '
    'Leave out an optional section or value when there is nothing for it, and write nothing outside the card.'
)
_SHAPED_DESCRIPTION = (
    'Send the reply as one card. Fill only the fields, since the layout and every fixed text are set for you.'
)
# The content a section may hold, in the order a card renders them.
ContentKind = typing.Literal['text', 'bullets', 'facts']


def _check_field_name(name: str) -> str:
    # A name becomes a field of the agent's output model, so it must not shadow one of the model's own members.
    if hasattr(pydantic.BaseModel, name):
        raise ValueError(f'{name!r} is reserved, pick another name')
    return name


# A section or placeholder name, which becomes a field the agent fills.
FieldName = typing.Annotated[
    str, pydantic.Field(pattern=r'^[a-z][a-z0-9_]*$'), pydantic.AfterValidator(_check_field_name)
]


def _template_names(template: str) -> set[str]:
    """Return the placeholder names in ``template``, rejecting anything but a plain ``{name}``."""
    names: set[str] = set()
    for _, name, format_spec, conversion in string.Formatter().parse(template):
        if name is None:
            continue
        if not name.isidentifier() or format_spec or conversion:
            raise ValueError(f'{template!r}: write a placeholder as a plain name in braces, such as {{date}}')
        names.add(name)
    return names


class _Shape(pydantic.BaseModel):
    # A misspelled key is an error, not a shape that silently renders otherwise.
    model_config = pydantic.ConfigDict(extra='forbid', frozen=True)


def _check_pattern(pattern: str) -> str:
    # Compile it with the same engine that checks the agent's value.
    try:
        pydantic.TypeAdapter(typing.Annotated[str, pydantic.Field(pattern=pattern)])
    except pydantic_core.SchemaError as error:
        raise ValueError(f'not a valid pattern: {pattern!r}') from error
    return pattern


# A regular expression a value the agent writes must match, or the agent is asked to write it again.
Pattern = typing.Annotated[str, pydantic.AfterValidator(_check_pattern)]


class PlaceholderShape(_Shape):
    """A gap in a displayed template, which the agent fills."""

    # What the agent writes in the gap, which is guidance only and never displayed.
    description: typing.Annotated[str, pydantic.Field(min_length=1)]
    pattern: Pattern | None = None


def _check_placeholders(templates: dict[str, str | None], placeholders: dict[str, PlaceholderShape]) -> None:
    used = set[str]().union(*(_template_names(template) for template in templates.values() if template))
    if undeclared := used - placeholders.keys():
        raise ValueError(f'placeholders {sorted(undeclared)} are used but not declared under `placeholders`')
    if unused := placeholders.keys() - used:
        raise ValueError(f'placeholders {sorted(unused)} are declared but no template uses them')


def _placeholder_fields(
    templates: dict[str, str | None], placeholders: dict[str, PlaceholderShape]
) -> dict[str, typing.Any]:
    """Return a field per placeholder, telling the agent which displayed text the value goes into."""
    fields: dict[str, typing.Any] = {}
    for name, placeholder in placeholders.items():
        used_in = {
            where: template for where, template in templates.items() if template and name in _template_names(template)
        }
        uses = ' and '.join(f'the {where} "{template}"' for where, template in used_in.items())
        description = f'{placeholder.description} It fills {{{name}}} in {uses}.'
        if any(template != f'{{{name}}}' for template in used_in.values()):
            description += ' The rest of that text is added for you, so write only this part.'
        fields[name] = (str, pydantic.Field(min_length=1, pattern=placeholder.pattern, description=description))
    return fields


def _optional(field_type: typing.Any) -> typing.Any:
    return field_type | None


def _assembling_model(
    name: str, assemble: typing.Callable[[pydantic.BaseModel], object], *, doc: str | None = None, **fields: typing.Any
) -> type[pydantic.BaseModel]:
    """Create an output model for the agent that assembles its part of the reply as it validates.

    Assembling checks the limits only the filled result shows, such as a header's length with its placeholders in.
    A part that breaks one goes back to the agent as a retry instead of failing the turn.
    """

    def check(output: pydantic.BaseModel) -> pydantic.BaseModel:
        assemble(output)
        return output

    validators = {'assemble': pydantic.model_validator(mode='after')(check)}
    # The stubs type validators as plain callables, while create_model takes the decorated ones at runtime.
    return pydantic.create_model(name, __doc__=doc, __validators__=typing.cast(typing.Any, validators), **fields)


class FactLabelShape(_Shape):
    """A fixed fact label, whose value the agent writes."""

    label: FactLabel
    # What the agent writes as the value, when the label alone does not say, which is guidance only.
    description: str | None = None
    pattern: Pattern | None = None
    # An optional value the agent leaves out drops its row.
    required: bool = True


class SectionShape(_Shape):
    """A declared section of a shaped card, with a fixed or templated title and the content the agent writes."""

    name: FieldName
    title: str | None = None
    placeholders: dict[FieldName, PlaceholderShape] = pydantic.Field(default_factory=dict)
    # Optional when labels are set, since fixed labels always hold facts.
    content: list[ContentKind] | None = None
    labels: list[FactLabelShape] | None = None
    description: str | None = None
    required: bool = True
    # Repeat the section once per item the agent finds, each with its own title placeholders.
    repeated: bool = False

    @pydantic.field_validator('content', mode='before')
    @classmethod
    def _content_as_list(cls, value: typing.Any) -> typing.Any:
        return [value] if isinstance(value, str) else value

    @pydantic.field_validator('labels', mode='before')
    @classmethod
    def _labels_from_strings(cls, value: typing.Any) -> typing.Any:
        # A plain string is shorthand for a required label.
        if not isinstance(value, list):
            return value
        return [{'label': item} if isinstance(item, str) else item for item in typing.cast(list[typing.Any], value)]

    @pydantic.model_validator(mode='after')
    def _check(self) -> typing.Self:
        if self.content is None and self.labels is None:
            raise ValueError(f'section {self.name!r} needs `content`, or `labels` for a section of facts')
        kinds = self.kinds
        if not kinds or len(set(kinds)) != len(kinds):
            raise ValueError(f'section {self.name!r} lists each kind of content once')
        if 'bullets' in kinds and 'facts' in kinds:
            raise ValueError(f'section {self.name!r} holds either bullets or facts, not both')
        if self.labels is not None:
            if 'facts' not in kinds:
                raise ValueError(f'section {self.name!r} has labels, which need facts in its content')
            labels = [label.label for label in self.labels]
            if not labels or len(set(labels)) != len(labels):
                raise ValueError(f'section {self.name!r} needs at least one label, each named once')
        if reserved := self.placeholders.keys() & {'text', 'bullets', 'facts'}:
            raise ValueError(f'section {self.name!r} cannot name a placeholder {sorted(reserved)}')
        _check_placeholders({'section title': self.title}, self.placeholders)
        return self

    @property
    def kinds(self) -> list[ContentKind]:
        """The content the section holds, which fixed labels make facts when ``content`` is left out."""
        return self.content if self.content is not None else ['facts']

    def output_model(self) -> type[pydantic.BaseModel]:
        """The model the agent fills for one instance of this section."""
        # One kind of content is required, while with several the agent fills any of them.
        single = len(self.kinds) == 1
        fields = _placeholder_fields({'section title': self.title}, self.placeholders)
        if 'text' in self.kinds:
            fields['text'] = (SectionText, ...) if single else (_optional(SectionText), None)
        if 'bullets' in self.kinds:
            fields['bullets'] = (BulletList, pydantic.Field(min_length=1)) if single else (BulletList, [])
        if 'facts' in self.kinds:
            if self.labels is not None:
                fields['facts'] = (self._labelled_facts_model(), ...)
            else:
                fields['facts'] = (FactList, pydantic.Field(min_length=1)) if single else (FactList, [])
        return _assembling_model(f'{_class_name(self.name)}Section', self.assemble, doc=self._guidance(), **fields)

    def _guidance(self) -> str:
        """Tell the agent where the section shows and what to write, since its title and order are set for it."""
        if self.title is None:
            parts = [f'The "{self.name}" section, shown without a title.']
        elif self.placeholders:
            parts = [f'A section titled "{self.title}", with its placeholders filled from the fields here.']
        else:
            parts = [f'The section titled "{self.title}". The title is added for you.']
        if self.repeated:
            parts.append(
                'Give one item per thing it lists.' if self.required else 'Give one item per thing it lists, or none.'
            )
        elif not self.required:
            parts.append('Leave it out when there is nothing for it.')
        if self.description:
            parts.append(self.description)
        return ' '.join(parts)

    def _labelled_facts_model(self) -> type[pydantic.BaseModel]:
        # Labels are free text, so each value gets a positional field that names its label.
        fields: dict[str, typing.Any] = {}
        for index, label in enumerate(self.labels or [], start=1):
            parts = [f'The value shown beside the label "{label.label}", which is added for you.']
            if label.description:
                parts.append(label.description)
            if not label.required:
                parts.append('Leave it out when there is no value, which drops the row.')
            field_type = FactValue if label.required else _optional(FactValue)
            fields[f'value_{index}'] = (
                field_type,
                pydantic.Field(... if label.required else None, pattern=label.pattern, description=' '.join(parts)),
            )
        return pydantic.create_model(
            f'{_class_name(self.name)}Facts', __doc__='One value per fixed label, in the order shown.', **fields
        )

    def assemble(self, output: pydantic.BaseModel) -> Section:
        values = dict(output)
        title = self.title.format_map(values) if self.title else None
        facts = values.get('facts') or []
        if isinstance(facts, pydantic.BaseModel):
            # Fixed labels came back as one value per label, in order, and a value left out drops its row.
            labels = self.labels or []
            pairs = zip(labels, dict(facts).values(), strict=True)
            facts = [Fact(label=label.label, value=value) for label, value in pairs if value is not None]
        return Section(title=title, text=values.get('text'), bullets=values.get('bullets') or [], facts=facts)


class CardShape(_Shape):
    """The declared shape of a push's reply card, with templated text around sections the agent fills."""

    header: typing.Annotated[str, pydantic.Field(min_length=1)]
    subtitle: str | None = None
    footnote: str | None = None
    placeholders: dict[FieldName, PlaceholderShape] = pydantic.Field(default_factory=dict)
    sections: typing.Annotated[list[SectionShape], pydantic.Field(min_length=1, max_length=6)]

    @pydantic.model_validator(mode='after')
    def _check(self) -> typing.Self:
        names = [section.name for section in self.sections]
        if len(set(names)) != len(names):
            raise ValueError('each section needs its own name')
        if clashing := self.placeholders.keys() & set(names):
            raise ValueError(f'{sorted(clashing)} name both a placeholder and a section')
        _check_placeholders(self._templates(), self.placeholders)
        return self

    def _templates(self) -> dict[str, str | None]:
        return {'header': self.header, 'subtitle': self.subtitle, 'footnote': self.footnote}

    def output_model(self) -> type[pydantic.BaseModel]:
        """The model the agent fills for one card."""
        fields = _placeholder_fields(self._templates(), self.placeholders)
        for section in self.sections:
            section_model = section.output_model()
            if section.repeated:
                minimum = 1 if section.required else 0
                fields[section.name] = (
                    list[section_model],
                    pydantic.Field([] if minimum == 0 else ..., min_length=minimum),
                )
            elif section.required:
                fields[section.name] = (section_model, ...)
            else:
                fields[section.name] = (_optional(section_model), None)
        return _assembling_model('ShapedCard', self.assemble, doc=self._guidance(), **fields)

    def _guidance(self) -> str:
        return (
            f'A card headed "{self.header}". '
            'Fill only these fields: the headings, titles, labels, and fixed text are added around them in a set order, '
            'so never write them into a field.'
        )

    def assemble(self, output: pydantic.BaseModel) -> Card:
        values = dict(output)
        sections: list[Section] = []
        for section in self.sections:
            value = values[section.name]
            items = value if section.repeated else [value]
            sections.extend(section.assemble(item) for item in items if item is not None)
        return Card(
            header=self.header.format_map(values),
            subtitle=self.subtitle.format_map(values) if self.subtitle else None,
            footnote=self.footnote.format_map(values) if self.footnote else None,
            sections=sections,
        )


def _class_name(name: str) -> str:
    return ''.join(part.capitalize() for part in name.split('_'))


class ReplyFormat(abc.ABC):
    """The shape a turn's reply takes, the outputs the agent may end the turn with and how each becomes a reply.

    The chat context supplies one per turn, so the runner never branches on how a reply is shaped.
    """

    @property
    @abc.abstractmethod
    def outputs(self) -> list[typing.Any]:
        """The output types the agent may end the turn with, as Pydantic AI output specs."""

    @property
    @abc.abstractmethod
    def instructions(self) -> str:
        """The system instructions telling the agent how to end the turn."""

    @abc.abstractmethod
    def to_reply(self, output: typing.Any) -> Reply:
        """Turn the agent's final output, one of ``outputs``, into the reply the surface sends."""


class FreeReplyFormat(ReplyFormat):
    """The agent picks plain text, a card, or a carousel, and lays out a card itself."""

    @property
    @typing.override
    def outputs(self) -> list[typing.Any]:
        return [str, ToolOutput(Card, name=_CARD_TOOL_NAME), ToolOutput(Carousel, name='reply_carousel')]

    @property
    @typing.override
    def instructions(self) -> str:
        return _FREE_INSTRUCTIONS

    @typing.override
    def to_reply(self, output: typing.Any) -> Reply:
        return output


class CardReplyFormat(ReplyFormat):
    """The agent fills the fields of a declared card shape, and the card is assembled in its fixed order.

    The shape's model is the turn's only reply output, so the turn cannot end in plain text or another layout.
    """

    def __init__(self, shape: CardShape) -> None:
        self._shape = shape
        self._output = shape.output_model()

    @property
    @typing.override
    def outputs(self) -> list[typing.Any]:
        return [ToolOutput(self._output, name=_CARD_TOOL_NAME, description=_SHAPED_DESCRIPTION)]

    @property
    @typing.override
    def instructions(self) -> str:
        return _SHAPED_INSTRUCTIONS.format(tool=_CARD_TOOL_NAME)

    @typing.override
    def to_reply(self, output: typing.Any) -> Reply:
        return self._shape.assemble(output)
