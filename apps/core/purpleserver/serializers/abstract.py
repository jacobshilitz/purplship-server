import pydoc
import logging
import importlib
from typing import Generic, Type, Optional, Union, TypeVar, Any, NamedTuple
from django.db import models
from django.forms.models import model_to_dict
from rest_framework import serializers

logger = logging.getLogger(__name__)
T = TypeVar('T')


class AbstractSerializer:
    def create(self, validated_data, **kwargs):
        super().create(validated_data)

    def update(self, instance, validated_data, **kwargs):
        super().update(instance, validated_data, **kwargs)


class Context(NamedTuple):
    user: Any
    org: Any = None

    def __getitem__(self, item):
        return getattr(self, item)


class Serializer(serializers.Serializer, AbstractSerializer):
    pass


class ModelSerializer(serializers.ModelSerializer, AbstractSerializer):
    def create(self, data: dict, **kwargs):
        return self.Meta.model.objects.create(**data)

    def update(self, instance, data: dict, **kwargs):
        for name, value in data.items():
            if name != 'created_by':
                setattr(instance, name, value)

        instance.save()
        return instance


"""
Custom serializer utilities functions
"""

def PaginatedResult(serializer_name: str, content_serializer: Type[Serializer]):
    return type(serializer_name, (Serializer,), dict(
        next=serializers.URLField(required=False),
        previous=serializers.URLField(required=False),
        results=content_serializer(many=True)
    ))


class _SerializerDecoratorInitializer(Generic[T]):

    def __getitem__(self, serializer_type: Type[Serializer]):
        class Decorator:
            def __init__(self, instance=None, data: Union[str, dict] = None, **kwargs):
                self._instance = instance

                if data is None and instance is None:
                    self._serializer = None

                else:
                    self._serializer: serializer_type = (
                        serializer_type(data=data, **kwargs) if instance is None else
                        serializer_type(instance, data=data, **{**kwargs, 'partial': True})
                    )

                    self._serializer.is_valid(raise_exception=True)

            @property
            def data(self) -> Optional[dict]:
                return self._serializer.validated_data if self._serializer is not None else None

            @property
            def instance(self):
                return self._instance

            def save(self, **kwargs) -> 'Decorator':
                if self._serializer is not None:
                    self._instance = self._serializer.save(**kwargs)

                return self

        return Decorator


SerializerDecorator = _SerializerDecoratorInitializer()


def owned_model_serializer(serializer: Type[Serializer]):

    class MetaSerializer(serializer):
        def __init__(self, *args, **kwargs):
            if 'context' in kwargs:
                context = kwargs.get('context')
                user = getattr(context, 'user', None)
                org = getattr(context, 'org', None)

                if (importlib.util.find_spec('purpleserver.orgs') is not None) and (org is None):
                    import purpleserver.orgs.models as orgs
                    org = orgs.Organization.objects.filter(users__id=getattr(user, 'id', None)).first()

                self.__context: Context = Context(user, org)
            else:
                self.__context: Context = getattr(self, '__context', None)

            super().__init__(*args, **kwargs)

        def create(self, data: dict, **kwargs):
            payload = {'created_by': self.__context.user, **data}

            try:
                instance = super().create(payload, context=self.__context)

                # Link to organization if supported
                if hasattr(instance, 'org') and self.__context.org is not None and not instance.org.exists():
                    instance.link = instance.__class__.link.related.related_model.objects.create(
                        org=self.__context.org, item=instance
                    )
                    instance.save()
            except Exception as e:
                logger.exception(e)
                raise e

            return instance

        def update(self, instance, data: dict, **kwargs):
            payload = {k:v for k, v in data.items()}

            return super().update(instance, payload, context=self.__context)

    return type(serializer.__name__, (MetaSerializer,), {})


def save_many_to_many_data(
        name: str,
        serializer: Type[ModelSerializer],
        parent: models.Model,
        payload: dict = None,
        **kwargs):

    if not any((key in payload for key in [name])):
        return None

    collection_data = payload.get(name)
    collection = getattr(parent, name)

    if collection_data is None and any(collection.all()):
        for item in collection.all():
            item.delete()

    for data in collection_data:
        item_instance = (
            collection.filter(id=data.pop('id')).first()
            if 'id' in data else None
        )

        if item_instance is None:
            item = SerializerDecorator[serializer](data=data, **kwargs).save().instance
        else:
            item = SerializerDecorator[serializer](
                instance=item_instance, data=data, partial=True, **kwargs).save().instance

        getattr(parent, name).add(item)


def save_one_to_one_data(
        name: str,
        serializer: Type[ModelSerializer],
        parent: models.Model = None,
        payload: dict = None,
        **kwargs):

    if name not in payload:
        return None

    data = payload.get(name)
    instance = getattr(parent, name, None)

    if data is None and instance is not None:
        instance.delete()
        setattr(parent, name, None)

    if instance is None:
        new_instance = SerializerDecorator[serializer](data=data, **kwargs).save().instance
        parent and setattr(parent, name, new_instance)
        return new_instance

    return SerializerDecorator[serializer](instance=instance, data=data, partial=True, **kwargs).save().instance


def allow_model_id(model_paths: []):

    def _decorator(serializer: Type[Serializer]):
        class ModelIdSerializer(serializer):
            def __init__(self, *args, **kwargs):
                for param, model_path in model_paths:
                    content = kwargs.get('data', {}).get(param)
                    values = content if isinstance(content, list) else [content]
                    model = pydoc.locate(model_path)

                    if any([isinstance(val, str) for val in values]):
                        new_content = []
                        for value in values:
                            if isinstance(value, str) and (model is not None):
                                data = model_to_dict(model.objects.get(pk=value))
                                ('id' in data) and data.pop('id')
                                new_content.append(data)

                        kwargs.update(data={
                            **kwargs['data'],
                            param: new_content if isinstance(content, list) else next(iter(new_content))
                        })

                super().__init__(*args, **kwargs)

        return type(serializer.__name__, (ModelIdSerializer,), {})

    return _decorator


def make_fields_optional(serializer: Type[ModelSerializer]):
    _name = f"Partial{serializer.__name__}"

    class _Meta(serializer.Meta):
        extra_kwargs = {
            **getattr(serializer.Meta, 'extra_kwargs', {}),
            **{field.name: {'required': False} for field in serializer.Meta.model._meta.fields}
        }

    return type(_name, (serializer,), dict(Meta=_Meta))
