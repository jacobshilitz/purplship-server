from django.db import transaction

from purpleserver.serializers import save_many_to_many_data, owned_model_serializer
from purpleserver.core.serializers import CustomsData

from purpleserver.manager.serializers.commodity import CommoditySerializer
import purpleserver.manager.models as models


@owned_model_serializer
class CustomsSerializer(CustomsData):

    def __init__(self, instance: models.Customs = None, **kwargs):
        data = kwargs.get('data')

        if data is not None:
            if 'commodities' in data and instance is not None:
                extra = {'partial': True, 'context': self.context}
                save_many_to_many_data('commodities', CommoditySerializer, instance, payload=data, **extra)

        super().__init__(instance, **kwargs)

    @transaction.atomic
    def create(self, validated_data: dict, context: dict, **kwargs) -> models.Customs:
        data = {key: value for key, value in validated_data.items() if key in models.Customs.DIRECT_PROPS}

        instance = models.Customs.objects.create(**data)

        save_many_to_many_data(
            'commodities', CommoditySerializer, instance, payload=validated_data, context=context)

        return instance

    @transaction.atomic
    def update(self, instance: models.Customs, validated_data: dict, **kwargs) -> models.Customs:

        for key, val in validated_data.items():
            if key in models.Customs.DIRECT_PROPS:
                setattr(instance, key, val)

        instance.save()
        return instance
