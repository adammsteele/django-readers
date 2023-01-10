from copy import deepcopy
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import cached_property
from django_readers import specs
from django_readers.utils import SpecVisitor
from rest_framework import serializers
from rest_framework.utils import model_meta


class ProjectionSerializer:
    def __init__(self, data=None, many=False, context=None):
        self.many = many
        self._data = data
        self.context = context

    @property
    def data(self):
        project = self.context["view"].project
        if self.many:
            return [project(item) for item in self._data]
        return project(self._data)


class SpecMixin:
    spec = None

    def get_spec(self):
        if self.spec is None:
            raise ImproperlyConfigured("SpecMixin requires spec or get_spec")
        return self.spec

    def _preprocess_spec(self, spec):
        visitor = _CallWithRequestVisitor(self.request)
        return visitor.visit(spec)

    def get_reader_pair(self):
        return specs.process(self._preprocess_spec(self.get_spec()))

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
        return ProjectionSerializer


class _CallWithRequestVisitor(SpecVisitor):
    def __init__(self, request):
        self.request = request

    def visit_callable(self, fn):
        return fn(self.request)


class _SpecToSerializerVisitor(SpecVisitor):
    def __init__(self, model, name):
        self.model = model
        self.name = name
        self.field_builder = serializers.ModelSerializer()
        self.info = model_meta.get_field_info(model)
        self.fields = {}

    def _snake_case_to_capfirst(self, snake_case):
        return "".join(part.title() for part in snake_case.split("_"))

    def _prepare_field(self, field):
        # We copy the field so its _creation_counter is correct and
        # it appears in the right order in the resulting serializer.
        # We also force it to be read_only
        field = deepcopy(field)
        field._kwargs["read_only"] = True
        return field

    def visit_str(self, item):
        return self.visit_dict_item_str(item, item)

    def visit_dict_item_str(self, key, value):
        # This is a model field name. First, check if the
        # field has been explicitly overridden
        if hasattr(value, "out"):
            field = self._prepare_field(value.out)
            self.fields[str(key)] = field
            return key, field

        # No explicit override, so we can use ModelSerializer
        # machinery to figure out which field type to use
        field_class, field_kwargs = self.field_builder.build_field(
            value,
            self.info,
            self.model,
            0,
        )
        if key != value:
            field_kwargs["source"] = value
        field_kwargs.setdefault("read_only", True)
        self.fields[key] = field_class(**field_kwargs)
        return key, value

    def visit_dict_item_list(self, key, value):
        # This is a relationship, so we recurse and create
        # a nested serializer to represent it
        rel_info = self.info.relations[key]
        capfirst = self._snake_case_to_capfirst(key)
        child_serializer = spec_to_serializer_class(
            f"{self.name}{capfirst}",
            rel_info.related_model,
            value,
        )
        self.fields[key] = child_serializer(
            read_only=True,
            many=rel_info.to_many,
        )
        return key, value

    def visit_dict_item_dict(self, key, value):
        # This is an aliased relationship, so we basically
        # do the same as the previous case, but handled
        # slightly differently to set the `source` correctly
        relationship_name, relationship_spec = next(iter(value.items()))
        rel_info = self.info.relations[relationship_name]
        capfirst = self._snake_case_to_capfirst(relationship_name)
        child_serializer = spec_to_serializer_class(
            f"{self.name}{capfirst}",
            rel_info.related_model,
            relationship_spec,
        )
        self.fields[key] = child_serializer(
            read_only=True,
            many=rel_info.to_many,
            source=relationship_name,
        )
        return key, value

    def visit_dict_item_tuple(self, key, value):
        # The output field has been explicity configured in the spec.
        if hasattr(value, "out"):
            field = self._prepare_field(value.out)
            self.fields[key] = field
        else:
            # Fallback case: we don't know what field type to use
            self.fields[key] = serializers.ReadOnlyField()
        return key, value

    visit_dict_item_callable = visit_dict_item_tuple

    def visit_tuple(self, item):
        if hasattr(item, "out"):
            # This must be a projector pair, so `out` is actually a
            # dictionary mapping field names to Fields
            for name, field in item.out.items():
                field = self._prepare_field(field)
                self.fields[name] = field
        return item

    visit_callable = visit_tuple


def spec_to_serializer_class(name_prefix, model, spec):
    visitor = _SpecToSerializerVisitor(model, name_prefix)
    visitor.visit(spec)

    return type(
        f"{name_prefix}Serializer",
        (serializers.Serializer,),
        {
            "Meta": type("Meta", (), {"model": model}),
            **visitor.fields,
        },
    )


def serializer_class_for_view(view):
    name_prefix = view.__class__.__name__.replace("View", "")
    if hasattr(view, "model"):
        model = view.model
    else:
        model = getattr(getattr(view, "queryset", None), "model", None)
    return spec_to_serializer_class(name_prefix, model, view.spec)


class PairWithOutAttribute(tuple):
    out = None


class StringWithOutAttribute(str):
    out = None


def out(field_or_dict):
    if isinstance(field_or_dict, dict):
        if not all(
            isinstance(item, serializers.Field) for item in field_or_dict.values()
        ):
            raise TypeError("Each value must be an instance of Field")
    elif not isinstance(field_or_dict, serializers.Field):
        raise TypeError("Must be an instance of Field")

    class ShiftableDecorator:
        def __call__(self, item):
            if isinstance(item, str):
                item = StringWithOutAttribute(item)
            if isinstance(item, tuple):
                item = PairWithOutAttribute(item)
            item.out = field_or_dict
            return item

        def __rrshift__(self, other):
            return self(other)

    return ShiftableDecorator()
