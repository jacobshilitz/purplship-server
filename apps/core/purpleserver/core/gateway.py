import uuid
import logging
from datetime import datetime
from typing import List, Callable, Dict, Any

from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError

import purplship
from purplship.core.utils import DP

from purpleserver.serializers import Context
from purpleserver.providers import models
from purpleserver.core.models import get_access_filter
from purpleserver.core import datatypes, serializers, exceptions, validators
from purpleserver.core.utils import identity, post_processing, upper

logger = logging.getLogger(__name__)


class Carriers:
    @staticmethod
    def list(**kwargs) -> List[models.Carrier]:
        list_filter: Dict[str: Any] = kwargs
        query = tuple()

        # Check if the system_only flag is not specified and there is a provided user, get the users carriers
        if not list_filter.get('system_only') and 'context' in list_filter:
            access = get_access_filter(list_filter.get('context'))
            if len(access) > 0:
                query += (Q(created_by__isnull=True) | access,)
        elif list_filter.get('system_only') is True:
            query += (Q(created_by__isnull=True),)

        # Check if the test filter is specified then set it otherwise return all carriers prod and test mode
        if list_filter.get('test') is not None:
            query += (Q(test=list_filter['test']), )

        # Check if the active flag is specified and return all active carrier is active is not set to false
        if list_filter.get('active') is not None:
            active = False if list_filter['active'] is False else True
            query += (Q(active=active), )

        # Check if a specific carrier_id is provide, to add it to the query
        if 'carrier_id' in list_filter:
            query += (Q(carrier_id=list_filter['carrier_id']), )

        # Check if a list of carrier_ids are provided, to add the list to the query
        if any(list_filter.get('carrier_ids', [])):
            query += (Q(carrier_id__in=list_filter['carrier_ids']), )

        if 'carrier_name' in list_filter:
            carrier_name = list_filter['carrier_name']
            if carrier_name not in models.MODELS.keys():
                raise NotFound(f"No configurations for the following carrier: '{carrier_name}'")

            carriers = [
                setting.carrier_ptr for setting in models.MODELS[carrier_name].objects.filter(*query)
            ]
        else:
            carriers = models.Carrier.objects.filter(*query)

        return carriers


class Address:
    @staticmethod
    def validate(payload: dict) -> datatypes.AddressValidation:
        # Currently only support GoogleGeocode validation. Refactor this for other methods
        validation = validators.GoogleGeocode.validate(datatypes.Address(**payload))

        if validation.success is False:
            raise ValidationError(detail="Invalid Address")

        return validation


class Shipments:
    @staticmethod
    def create(payload: dict, resolve_tracking_url: Callable[[datatypes.Shipment], str] = None, carrier: models.Carrier = None) -> datatypes.Shipment:
        selected_rate = next(
            (datatypes.Rate(**rate) for rate in payload.get('rates') if rate.get('id') == payload.get('selected_rate_id')),
            None
        )

        if selected_rate is None:
            raise NotFound(
                f'Invalid selected_rate_id "{payload.get("selected_rate_id")}" \n '
                f'Please select one of the following: [ {", ".join([r.get("id") for r in payload.get("rates")])} ]'
            )

        carrier = carrier or models.Carrier.objects.get(carrier_id=selected_rate.carrier_id).data
        request = datatypes.ShipmentRequest(**{**DP.to_dict(payload), 'service': selected_rate.service})
        gateway = purplship.gateway[carrier.carrier_name].create(carrier.dict())

        # The request is wrapped in identity to simplify mocking in tests
        shipment, messages = identity(lambda: purplship.Shipment.create(request).from_(gateway).parse())

        if shipment is None:
            raise exceptions.PurplShipApiException(
                detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_400_BAD_REQUEST)

        def process_meta(parent) -> dict:
            return {
                **(parent.meta or {}),
                'rate_provider': ((parent.meta or {}).get('rate_provider') or carrier.carrier_name).lower(),
                'service_name': upper((parent.meta or {}).get('service_name') or selected_rate.service)
            }

        def process_selected_rate() -> dict:
            rate = (
                {**DP.to_dict(shipment.selected_rate), 'id': f'rat_{uuid.uuid4().hex}', 'test_mode': carrier.test}
                if shipment.selected_rate is not None else
                DP.to_dict(selected_rate)
            )
            return {**rate, 'meta': process_meta(shipment.selected_rate or selected_rate)}

        def process_tracking_url(rate: datatypes.Rate) -> str:
            rate_provider = (rate.get('meta') or {}).get('rate_provider')
            if (rate_provider not in models.MODELS) and ((shipment.meta or {}).get('tracking_url') is not None):
                return shipment.meta['tracking_url']

            if resolve_tracking_url is not None:
                url = resolve_tracking_url(shipment.tracking_number, rate_provider or rate.carrier_name)
                return f"{url}{'?test' if carrier.test else ''}"

            return ''

        shipment_rate = process_selected_rate()

        return datatypes.Shipment(**{
            **payload,
            **DP.to_dict(shipment),
            "id": f"shp_{uuid.uuid4().hex}",
            "test_mode": carrier.test,
            "selected_rate": shipment_rate,
            "service": shipment_rate["service"],
            "selected_rate_id": shipment_rate["id"],
            "tracking_url": process_tracking_url(shipment_rate),
            "status": serializers.ShipmentStatus.purchased.value,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f%z"),
            "messages": messages,
            "meta": process_meta(shipment)
        })

    @staticmethod
    def cancel(payload: dict, carrier_filter: dict = None, carrier: models.Carrier = None) -> datatypes.ConfirmationResponse:
        carrier = carrier or next(iter(Carriers.list(**{**(carrier_filter or {}), 'active': True})), None)

        if carrier is None:
            raise NotFound('No active carrier connection found to process the request')

        request = purplship.Shipment.cancel(datatypes.ShipmentCancelRequest(**payload))
        gateway = purplship.gateway[carrier.data.carrier_name].create(carrier.data.dict())

        # The request call is wrapped in identity to simplify mocking in tests
        confirmation, messages = (
            identity(lambda: request.from_(gateway).parse())
            if 'cancel_shipment' in gateway.features else
            (
                datatypes.Confirmation(
                    carrier_name=gateway.settings.carrier_name,
                    carrier_id=gateway.settings.carrier_id,
                    success=True,
                    operation="Safe cancellation allowed"
                ),
                []
            )
        )

        if confirmation is None:
            raise exceptions.PurplShipApiException(detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_400_BAD_REQUEST)

        return datatypes.ConfirmationResponse(
            confirmation=confirmation,
            messages=messages
        )

    @staticmethod
    def track(payload: dict, carrier_filter: dict = None, carrier: models.Carrier = None) -> datatypes.TrackingResponse:
        carrier = carrier or next(iter(Carriers.list(**{**(carrier_filter or {}), 'active': True})), None)

        if carrier is None:
            raise NotFound('No active carrier connection found to process the request')

        request = purplship.Tracking.fetch(datatypes.TrackingRequest(**DP.to_dict(payload)))
        gateway = purplship.gateway[carrier.data.carrier_name].create(carrier.data.dict())

        # The request call is wrapped in identity to simplify mocking in tests
        results, messages = identity(lambda: request.from_(gateway).parse())

        if not any(results or []):
            raise exceptions.PurplShipApiException(detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_404_NOT_FOUND)

        def process_pending_state(details: datatypes.Tracking):
            return (
                len(details.events) == 0 or
                (len(details.events) == 1 and details.events[0].code == 'CREATED')
            )

        return datatypes.TrackingResponse(
            tracking=(datatypes.Tracking(**{
                **DP.to_dict(results[0]),
                'id': f'trk_{uuid.uuid4().hex}',
                'test_mode': carrier.test,
                'pending': process_pending_state(results[0])
            }) if any(results) else None),
            messages=messages
        )


class Pickups:
    @staticmethod
    def schedule(payload: dict, carrier_filter: dict = None, carrier: models.Carrier = None) -> datatypes.PickupResponse:
        carrier = carrier or next(iter(Carriers.list(**{**(carrier_filter or {}), 'active': True})), None)

        if carrier is None:
            raise NotFound('No active carrier connection found to process the request')

        request = purplship.Pickup.schedule(datatypes.PickupRequest(**DP.to_dict(payload)))
        gateway = purplship.gateway[carrier.data.carrier_name].create(carrier.data.dict())

        # The request call is wrapped in identity to simplify mocking in tests
        pickup, messages = identity(lambda: request.from_(gateway).parse())

        if pickup is None:
            raise exceptions.PurplShipApiException(detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_400_BAD_REQUEST)

        return datatypes.PickupResponse(
            pickup=datatypes.Pickup(**{
                **payload,
                **DP.to_dict(pickup),
                'id': f'pck_{uuid.uuid4().hex}',
                'test_mode': carrier.test,
            }),
            messages=messages
        )

    @staticmethod
    def update(payload: dict, carrier_filter: dict = None, carrier: models.Carrier = None) -> datatypes.PickupResponse:
        carrier = carrier or next(iter(Carriers.list(**{**(carrier_filter or {}), 'active': True})), None)

        if carrier is None:
            raise NotFound('No active carrier connection found to process the request')

        request = purplship.Pickup.update(datatypes.PickupUpdateRequest(**DP.to_dict(payload)))
        gateway = purplship.gateway[carrier.data.carrier_name].create(carrier.data.dict())

        # The request call is wrapped in identity to simplify mocking in tests
        pickup, messages = identity(lambda: request.from_(gateway).parse())

        if pickup is None:
            raise exceptions.PurplShipApiException(detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_400_BAD_REQUEST)

        return datatypes.PickupResponse(
            pickup=datatypes.Pickup(**{
                **payload,
                **DP.to_dict(pickup),
                'test_mode': carrier.test,
            }),
            messages=messages
        )

    @staticmethod
    def cancel(payload: dict, carrier_filter: dict = None, carrier: models.Carrier = None) -> datatypes.ConfirmationResponse:
        carrier = carrier or next(iter(Carriers.list(**{**(carrier_filter or {}), 'active': True})), None)

        if carrier is None:
            raise NotFound('No active carrier connection found to process the request')

        request = purplship.Pickup.cancel(datatypes.PickupCancelRequest(**DP.to_dict(payload)))
        gateway = purplship.gateway[carrier.data.carrier_name].create(carrier.data.dict())

        # The request call is wrapped in identity to simplify mocking in tests
        confirmation, messages = (
            identity(lambda: request.from_(gateway).parse())
            if 'cancel_shipment' in gateway.features else
            (
                datatypes.Confirmation(
                    carrier_name=gateway.settings.carrier_name,
                    carrier_id=gateway.settings.carrier_id,
                    success=True,
                    operation="Safe cancellation allowed"
                ),
                []
            )
        )

        if confirmation is None:
            raise exceptions.PurplShipApiException(detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_400_BAD_REQUEST)

        return datatypes.ConfirmationResponse(
            confirmation=confirmation,
            messages=messages
        )


@post_processing(methods=['fetch'])
class Rates:
    post_process_functions: List[Callable] = []

    @staticmethod
    def fetch(payload: dict, context: Context = None, test: bool = None) -> datatypes.RateResponse:
        request = purplship.Rating.fetch(datatypes.RateRequest(**DP.to_dict(payload)))

        carrier_settings_list = [
            carrier.data for carrier in
            Carriers.list(carrier_ids=payload.get('carrier_ids', []), active=True, context=context, test=test)
        ]
        gateways = [
            purplship.gateway[c.carrier_name].create(c.dict()) for c in carrier_settings_list
        ]
        compatible_gateways = [g for g in gateways if 'get_rates' in g.features]

        if len(compatible_gateways) == 0:
            raise NotFound("No active carrier connection found to process the request")

        # The request call is wrapped in identity to simplify mocking in tests
        rates, messages = identity(lambda: request.from_(*compatible_gateways).parse())

        if not any(rates) and any(messages):
            raise exceptions.PurplShipApiException(
                detail=datatypes.ErrorResponse(messages=messages), status_code=status.HTTP_400_BAD_REQUEST)

        def process_rate(rate: datatypes.Rate) -> datatypes.Rate:
            carrier = next((c for c in carrier_settings_list if c.carrier_id == rate.carrier_id))
            meta = {
                **(rate.meta or {}),
                'rate_provider': ((rate.meta or {}).get('rate_provider') or rate.carrier_name).lower(),
                'service_name': upper((rate.meta or {}).get('service_name') or rate.service)
            }

            return datatypes.Rate(**{
                **DP.to_dict(rate),
                'id': f'rat_{uuid.uuid4().hex}',
                'carrier_ref': carrier.id,
                'test_mode': carrier.test,
                'meta': meta
            })

        rates: List[datatypes.Rate] = sorted(map(process_rate, rates), key=lambda rate: rate.total_charge)

        return datatypes.RateResponse(rates=rates, messages=messages)
