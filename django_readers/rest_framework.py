from copy import deepcopy
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import cached_property
from django_readers import specs
from rest_framework import serializers
from rest_framework.utils import model_meta


def spec_to_serializer_class(serializer_name, model, spec, is_root=True):
    field_builder = serializers.ModelSerializer()
    info = model_meta.get_field_info(model)

    def snake_case_to_capfirst(snake_case):
        return "".join(part.title() for part in snake_case.split("_"))

    fields = {}
    for item in spec:
        if isinstance(item, str):
            item = {item: item}
        if isinstance(item, dict):
            for name, child_spec in item.items():
                if isinstance(child_spec, str):
                    field_class, field_kwargs = field_builder.build_field(
                        child_spec,
                        info,
                        model,
                        0,
                    )
                    if name != child_spec:
                        field_kwargs["source"] = child_spec
                    field_kwargs.setdefault("read_only", True)
                    fields[name] = field_class(**field_kwargs)
                elif isinstance(child_spec, list):
                    rel_info = info.relations[name]
                    capfirst = snake_case_to_capfirst(name)
                    child_serializer = spec_to_serializer_class(
                        f"{capfirst}Serializer",
                        rel_info.related_model,
                        child_spec,
                        is_root=False,
                    )
                    fields[name] = child_serializer(
                        read_only=True,
                        many=rel_info.to_many,
                    )
                elif isinstance(child_spec, dict):
                    if len(child_spec) != 1:
                        raise ValueError(
                            "Aliased relationship spec must contain only one key"
                        )
                    relationship_name, relationship_spec = next(
                        iter(child_spec.items())
                    )
                    rel_info = info.relations[relationship_name]
                    capfirst = snake_case_to_capfirst(relationship_name)
                    child_serializer = spec_to_serializer_class(
                        f"{capfirst}Serializer",
                        rel_info.related_model,
                        relationship_spec,
                        is_root=False,
                    )
                    fields[name] = child_serializer(
                        read_only=True,
                        many=rel_info.to_many,
                        source=relationship_name,
                    )
                elif isinstance(child_spec, WithOutputField):
                    # We copy the field so its _creation_counter is correct and
                    # it appears in the right order in the resulting serializer
                    output_field = deepcopy(child_spec.output_field)
                    output_field._kwargs["read_only"] = True
                    fields[name] = output_field
                else:
                    fields[name] = serializers.ReadOnlyField()

    if is_root:
        fields["to_representation"] = lambda self, instance: self.context["project"](
            instance
        )
    return type(serializer_name, (serializers.Serializer,), fields)


class SpecMixin:
    spec = None

    def get_spec(self):
        if self.spec is None:
            raise ImproperlyConfigured("SpecMixin requires spec or get_spec")
        return self.spec

    def get_reader_pair(self):
        return specs.process(self.get_spec())

    @cached_property
    def reader_pair(self):
        return self.get_reader_pair()

    @property
    def prepare(self):
        return self.reader_pair[0]

    @property
    def project(self):
        return self.reader_pair[1]

    def get_queryset(self):
        queryset = super().get_queryset()
        return self.prepare(queryset)

    def get_serializer_class(self):
        name = self.__class__.__name__.replace("View", "") + "Serializer"
        model = getattr(getattr(self, "queryset", None), "model", None)
        return spec_to_serializer_class(name, model, self.spec)

    def get_serializer_context(self):
        return {"project": self.project, **super().get_serializer_context()}


class WithOutputField:
    def __init__(self, pair, *, output_field):
        if not isinstance(output_field, serializers.Field):
            raise TypeError("output_field must be an instance of Field")
        self.pair = pair
        self.output_field = output_field
