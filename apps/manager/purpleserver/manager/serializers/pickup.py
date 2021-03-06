from purpleserver import serializers

from purpleserver.serializers import SerializerDecorator, owned_model_serializer, save_one_to_one_data
from purpleserver.core.gateway import Pickups, Carriers
from purpleserver.core.datatypes import Confirmation
from purpleserver.core.serializers import (
    Pickup,
    AddressData,
    PickupRequest,
    PickupUpdateRequest,
    PickupCancelRequest,
    StringListField,
)
from purpleserver.manager.serializers import AddressSerializer
import purpleserver.manager.models as models


def shipment_exists(value):
    validation = {key: models.Shipment.objects.filter(tracking_number=key) for key in value}

    if not all(val.exists() for val in validation.values()):
        invalids = [key for key, val in validation.items() if val.exists() is False]
        raise serializers.ValidationError(f"Shipment with the tracking numbers: {invalids} not found", code="invalid")

    if any(val.first().pickup_shipments.exists() for val in validation.values()):
        scheduled = [key for key, val in validation.items() if val.first().pickup_shipments.exists() is True]
        raise serializers.ValidationError(
            f"The following shipments {scheduled} are already scheduled pickups", code="invalid")


def pickup_exists(value):
    validation = {key: models.Pickup.objects.filter(tracking_number=key).exists() for key in value}

    if not all(validation.values()):
        invalids = [key for key, val in validation.items() if val is False]
        raise serializers.ValidationError(f"Shipment with the tracking numbers: {invalids} not found", code="invalid")


def address_exists(value):
    if value is str and not models.Address.objects.filter(pk=value).exists():
        raise serializers.ValidationError(f"Address with id {value} not found: {value}", code="invalid")


class PickupSerializer(PickupRequest):
    parcels = None
    address = AddressData(required=False, validators=[address_exists], help_text="The pickup address")
    tracking_numbers = StringListField(
        required=True, validators=[shipment_exists], help_text="The list of shipments to be picked up")

    def __init__(self, instance: models.Pickup = None, **kwargs):
        if 'data' in kwargs:
            data = kwargs.get('data').copy()

            self._shipments = models.Shipment.objects.filter(tracking_number__in=data.get('tracking_numbers', []))

            if data.get('address') is None and instance is None:
                address = next((AddressData(s.shipper).data for s in self._shipments), None)
            elif data.get('address') is None and instance is not None:
                address = AddressData(instance.address).data
            elif data.get('address') is str:
                address = models.Shipment.objects.get(pk=data.get('address'))
            else:
                address = data.get('address')

            if address is not None:
                data.update(address=address)

            kwargs.update(data=data)

        super().__init__(instance, **kwargs)

    def validate(self, data):
        validated_data = super(PickupRequest, self).validate(data)

        if len(validated_data.get('tracking_numbers', [])) > 1 and validated_data.get('address') is None:
            raise serializers.ValidationError("address must be specified for multi-shipments pickup", code="required")

        return validated_data


@owned_model_serializer
class PickupData(PickupSerializer):
    def create(self, validated_data: dict, context: dict, **kwargs) -> models.Pickup:
        carrier_filter = validated_data["carrier_filter"]
        carrier = next(iter(Carriers.list(**carrier_filter)), None)
        request_data = PickupRequest({
            **validated_data, "parcels": sum([list(s.parcels.all()) for s in self._shipments], [])
        }).data

        response = Pickups.schedule(payload=request_data, carrier=carrier)
        payload = {
            key: value for key, value in Pickup(response.pickup).data.items() if key in models.Pickup.DIRECT_PROPS
        }
        address = save_one_to_one_data('address', AddressSerializer, payload=validated_data, context=context)

        pickup = models.Pickup.objects.create(**{
            **payload,
            "address": address,
            "pickup_carrier": carrier,
            "created_by": validated_data['created_by'],
            "test_mode": response.pickup.test_mode,
            "confirmation_number": response.pickup.confirmation_number,
        })
        pickup.shipments.set(self._shipments)

        return pickup


@owned_model_serializer
class PickupUpdateData(PickupSerializer):
    confirmation_number = serializers.CharField(required=True, help_text="pickup identification number")
    pickup_date = serializers.CharField(required=False, help_text="""
    The expected pickup date
    
    Date Format: YYYY-MM-DD
    """)
    ready_time = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, help_text="The ready time for pickup.")
    closing_time = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, help_text="The closing or late time of the pickup")
    instruction = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="""
    The pickup instruction.
    
    eg: Handle with care.
    """)
    package_location = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="""
    The package(s) location.
    
    eg: Behind the entrance door.
    """)
    tracking_numbers = StringListField(
        required=False, validators=[shipment_exists], help_text="The list of shipments to be picked up")

    def update(self, instance: models.Pickup, validated_data: dict, context: dict, **kwargs) -> models.Tracking:
        request_data = PickupUpdateRequest({
            **PickupUpdateRequest(instance).data,
            **validated_data,
            "address": AddressData({
                **AddressData(instance.address).data,
                **validated_data['address']
            }).data
        }).data

        Pickups.update(payload=request_data, carrier=instance.pickup_carrier)

        data = validated_data.copy()
        for key, val in data.items():
            if key in models.Pickup.DIRECT_PROPS:
                setattr(instance, key, val)
                validated_data.pop(key)

        save_one_to_one_data('address', AddressSerializer, instance, payload=validated_data, context=context)

        instance.save()
        return instance


@owned_model_serializer
class PickupCancelData(serializers.Serializer):
    reason = serializers.CharField(required=False, help_text="The reason of the pickup cancellation")

    def update(self, instance: models.Pickup, validated_data: dict, **kwargs) -> Confirmation:
        request = PickupCancelRequest({
            **PickupCancelRequest(instance).data,
            **validated_data
        })
        response = Pickups.cancel(payload=request.data, carrier=instance.pickup_carrier)
        instance.delete()

        return response.confirmation
