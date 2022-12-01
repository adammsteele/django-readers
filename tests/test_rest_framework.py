from django.test import TestCase
from django_readers import pairs
from django_readers.rest_framework import (
    spec_to_serializer_class,
    SpecMixin,
    WithOutputField,
)
from rest_framework import serializers
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.test import APIRequestFactory
from tests.models import Category, Group, Owner, Widget
from textwrap import dedent


class WidgetListView(SpecMixin, ListAPIView):
    queryset = Widget.objects.all()
    spec = [
        "name",
        {
            "owner": [
                "name",
                {
                    "group": [
                        "name",
                    ]
                },
            ]
        },
    ]


class CategoryDetailView(SpecMixin, RetrieveAPIView):
    queryset = Category.objects.all()
    spec = [
        "name",
        {
            "widget_set": [
                "name",
                {
                    "owner": [
                        "name",
                    ]
                },
            ]
        },
    ]


upper_name = pairs.field("name", transform_value=lambda value: value.upper())


class RESTFrameworkTestCase(TestCase):
    def test_list(self):
        Widget.objects.create(
            name="test widget",
            owner=Owner.objects.create(
                name="test owner", group=Group.objects.create(name="test group")
            ),
        )

        request = APIRequestFactory().get("/")
        view = WidgetListView.as_view()

        with self.assertNumQueries(3):
            response = view(request)

        self.assertEqual(
            response.data,
            [
                {
                    "name": "test widget",
                    "owner": {
                        "name": "test owner",
                        "group": {
                            "name": "test group",
                        },
                    },
                }
            ],
        )

    def test_detail(self):
        category = Category.objects.create(name="test category")
        owner = Owner.objects.create(name="test owner")
        category.widget_set.add(Widget.objects.create(name="test 1", owner=owner))
        category.widget_set.add(Widget.objects.create(name="test 2", owner=owner))

        request = APIRequestFactory().get("/")
        view = CategoryDetailView.as_view()

        with self.assertNumQueries(3):
            response = view(request, pk=str(category.pk))

        self.assertEqual(
            response.data,
            {
                "name": "test category",
                "widget_set": [
                    {
                        "name": "test 1",
                        "owner": {"name": "test owner"},
                    },
                    {
                        "name": "test 2",
                        "owner": {"name": "test owner"},
                    },
                ],
            },
        )


class SpecToSerializerClassTestCase(TestCase):
    def test_basic_spec(self):
        spec = ["name"]

        cls = spec_to_serializer_class("CategorySerializer", Category, spec)

        expected = dedent(
            """\
            CategorySerializer():
                name = CharField(max_length=100, read_only=True)"""
        )
        self.assertEqual(repr(cls()), expected)

    def test_nested_spec(self):
        spec = [
            "name",
            {
                "widget_set": [
                    "name",
                    {
                        "owner": [
                            "name",
                        ]
                    },
                ]
            },
        ]

        cls = spec_to_serializer_class("CategorySerializer", Category, spec)

        expected = dedent(
            """\
            CategorySerializer():
                name = CharField(max_length=100, read_only=True)
                widget_set = WidgetSetSerializer(many=True, read_only=True):
                    name = CharField(allow_null=True, max_length=100, read_only=True, required=False)
                    owner = OwnerSerializer(read_only=True):
                        name = CharField(max_length=100, read_only=True)"""
        )
        self.assertEqual(repr(cls()), expected)

    def test_output_field(self):
        spec = [
            "name",
            {
                "upper_name": WithOutputField(
                    upper_name, output_field=serializers.CharField()
                )
            },
        ]

        cls = spec_to_serializer_class("CategorySerializer", Category, spec)

        expected = dedent(
            """\
            CategorySerializer():
                name = CharField(max_length=100, read_only=True)
                upper_name = CharField(read_only=True)"""
        )
        self.assertEqual(repr(cls()), expected)

    def test_output_field_raises_with_field_class(self):
        with self.assertRaises(TypeError):
            WithOutputField(upper_name, output_field=serializers.CharField)
