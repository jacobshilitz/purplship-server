import logging
from typing import Optional
from django.db import transaction
from rest_framework.reverse import reverse
from rest_framework.serializers import Serializer, CharField, ChoiceField, BooleanField

from purplship.core.utils import DP, DF
from purpleserver.core.gateway import Shipments, Carriers
from purpleserver.serializers import (
    SerializerDecorator,
    owned_model_serializer,
    save_one_to_one_data,
    save_many_to_many_data
)
import purpleserver.core.datatypes as datatypes
from purpleserver.providers.models import Carrier, MODELS
from purpleserver.core.serializers import (
    SHIPMENT_STATUS,
    ShipmentStatus,
    ShipmentData,
    Shipment,
    Payment,
    Rate,
    ShippingRequest,
    ShipmentCancelRequest,
    LABEL_TYPES,
    LabelType,
    Message,
    PlainDictField,
)
from purpleserver.manager.serializers.address import AddressSerializer
from purpleserver.manager.serializers.customs import CustomsSerializer
from purpleserver.manager.serializers.parcel import ParcelSerializer
from purpleserver.manager.serializers.rate import RateSerializer
import purpleserver.manager.models as models

logger = logging.getLogger(__name__)


@owned_model_serializer
class ShipmentSerializer(ShipmentData):
    status = ChoiceField(required=False, choices=SHIPMENT_STATUS)
    selected_rate_id = CharField(required=False)
    rates = Rate(many=True, required=False)
    label = CharField(required=False, allow_blank=True, allow_null=True)
    tracking_number = CharField(required=False, allow_blank=True, allow_null=True)
    shipment_identifier = CharField(required=False, allow_blank=True, allow_null=True)
    selected_rate = Rate(required=False, allow_null=True)
    tracking_url = CharField(required=False, allow_blank=True, allow_null=True)
    test_mode = BooleanField(required=False)
    meta = PlainDictField(required=False, allow_null=True)
    messages = Message(many=True, required=False)

    @transaction.atomic
    def create(self, validated_data: dict, context: dict, **kwargs) -> models.Shipment:
        test = validated_data.get('test')
        carrier_ids = validated_data.get('carrier_ids', [])
        carriers = Carriers.list(carrier_ids=carrier_ids, test=test, context=context) if any(carrier_ids) else []

        # Get live rates
        rate_response: datatypes.RateResponse = SerializerDecorator[RateSerializer](
            data=validated_data, context=context).save(test=test).instance

        shipment_data = {
            **{
                key: value for key, value in validated_data.items()
                if key in models.Shipment.DIRECT_PROPS and value is not None
            },
            'customs': save_one_to_one_data('customs', CustomsSerializer, payload=validated_data, context=context),
            'shipper': save_one_to_one_data('shipper', AddressSerializer, payload=validated_data, context=context),
            'recipient': save_one_to_one_data('recipient', AddressSerializer, payload=validated_data, context=context),
        }

        shipment = models.Shipment.objects.create(**{
            **shipment_data,
            'rates': DP.to_dict(rate_response.rates),
            'messages': DP.to_dict(rate_response.messages),
            'test_mode': all([r.test_mode for r in rate_response.rates]),
        })
        shipment.carriers.set(carriers)

        save_many_to_many_data('parcels', ParcelSerializer, shipment, payload=validated_data, context=context)

        return shipment

    @transaction.atomic
    def update(self, instance: models.Shipment, validated_data: dict, context: dict) -> models.Shipment:
        changes = []
        data = validated_data.copy()

        for key, val in data.items():
            if key in models.Shipment.DIRECT_PROPS:
                setattr(instance, key, val)
                changes.append(key)
                validated_data.pop(key)

            if key in models.Shipment.RELATIONAL_PROPS and val is None:
                prop = getattr(instance, key)
                changes.append(key)
                # Delete related data from database if payload set to null
                if hasattr(prop, 'delete'):
                    prop.delete()
                    setattr(instance, key, None)
                    validated_data.pop(key)

        if validated_data.get('customs') is not None:
            changes.append('customs')
            save_one_to_one_data('customs', CustomsSerializer, instance, payload=validated_data, context=context)

        if 'selected_rate' in validated_data:
            selected_rate = validated_data.get('selected_rate', {})
            carrier = Carrier.objects.filter(carrier_id=selected_rate.get('carrier_id')).first()
            instance.test_mode = selected_rate.get('test_mode', instance.test_mode)

            instance.selected_rate = {**selected_rate, **({'carrier_ref': carrier.id} if carrier is not None else {})}
            instance.selected_rate_carrier = carrier
            changes += ['selected_rate', 'selected_rate_carrier']

        instance.save(update_fields=changes)

        if 'carrier_ids' in validated_data:
            carrier_ids = validated_data.get('carrier_ids', [])
            carriers = (
                Carriers.list(carrier_ids=carrier_ids, created_by=instance.created_by)
                if any(carrier_ids) else instance.carriers
            )
            instance.carriers.set(carriers)

        return instance


@owned_model_serializer
class ShipmentPurchaseData(Serializer):
    selected_rate_id = CharField(required=True, help_text="The shipment selected rate.")
    label_type = ChoiceField(required=False, choices=LABEL_TYPES, default=LabelType.PDF.name, help_text="The shipment label file type.")
    payment = Payment(required=False, help_text="The payment details")


@owned_model_serializer
class ShipmentValidationData(Shipment):
    rates = Rate(many=True, required=True)
    payment = Payment(required=True)

    def create(self, validated_data: dict, **kwargs) -> datatypes.Shipment:
        return Shipments.create(
            ShippingRequest(validated_data).data,
            resolve_tracking_url=(
                lambda tracking_number, carrier_name: reverse(
                    "purpleserver.manager:shipment-tracker",
                    kwargs=dict(tracking_number=tracking_number, carrier_name=carrier_name)
                )
            )
        )


class ShipmentCancelSerializer(Shipment):

    def update(self, instance: models.Shipment, validated_data: dict, **kwargs) -> datatypes.ConfirmationResponse:
        if instance.status == ShipmentStatus.purchased.value:
            response = Shipments.cancel(
                payload=ShipmentCancelRequest(instance).data,
                carrier=instance.selected_rate_carrier
            )
        else:
            response = datatypes.ConfirmationResponse(
                messages=[],
                confirmation=datatypes.Confirmation(
                    operation="Cancel Shipment",
                    carrier_name="None Selected",
                    carrier_id="None Selected",
                    success=True,
                )
            )

        instance.status = ShipmentStatus.cancelled.value
        instance.save(update_fields=['status'])
        remove_shipment_tracker(instance)

        return response


def reset_related_shipment_rates(shipment: Optional[models.Shipment]):
    if shipment is not None:
        shipment.selected_rate = None
        shipment.rates = []
        shipment.messages = []
        shipment.save()


def remove_shipment_tracker(shipment: models.Shipment):
    if any(shipment.tracker.all()):
        shipment.tracker.all().delete()




def create_shipment_tracker(shipment: Optional[models.Shipment], context):
    rate_provider = ((shipment.meta or {}).get('rate_provider') or shipment.carrier_name)
    carrier = shipment.selected_rate_carrier

    if (rate_provider != shipment.carrier_name) and rate_provider in MODELS:
        carrier = MODELS[rate_provider].access_by(context).filter(test=shipment.test_mode).first()

    if carrier is not None:
        try:
            models.Tracking.objects.create(
                tracking_number=shipment.tracking_number,
                events=[DP.to_dict(datatypes.TrackingEvent(
                    date=DF.fdate(shipment.updated_at),
                    description="Label created and ready for shipment",
                    location="",
                    code="CREATED",
                    time=DF.ftime(shipment.updated_at)
                ))],
                delivered=False,
                test_mode=shipment.test_mode,
                tracking_carrier=carrier,
                created_by=shipment.created_by,
                shipment=shipment,
            )
            logger.info(f"Successfully added a tracker to the shipment {shipment.id}")
        except Exception as e:
            logger.exception("Failed to create new label tracker", e)
