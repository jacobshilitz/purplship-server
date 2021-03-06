from django.urls import path
from django.conf import settings
from drf_yasg import views, openapi, generators, inspectors
from rest_framework import permissions

APP_VERSION = getattr(settings, 'VERSION', '')
APP_NAME = getattr(settings, 'APP_NAME', 'Purplship')
EMAIL_SUPPORT = getattr(settings, 'EMAIL_SUPPORT', 'hello@purplship.com')

SCHEMA_VIEW_DESCRIPTION = f"""
## API Reference

{APP_NAME} is an open source multi-carrier shipping API that simplifies the integration of logistic carrier services.
        
The {APP_NAME} API is organized around REST. Our API has predictable resource-oriented URLs, accepts JSON-encoded 
request bodies, returns JSON-encoded responses, and uses standard HTTP response codes, authentication, and verbs.

The {APP_NAME} API differs for every account as we release new versions.
These docs are customized to your version of the API.


## Versioning

When backwards-incompatible changes are made to the API, a new, dated version is released. 
The current version is `{settings.VERSION}`. 

Read our API changelog and to learn more about backwards compatibility.

As a precaution, use API versioning to check a new API version before committing to an upgrade.


## Pagination

All top-level API resources have support for bulk fetches via "list" API methods. For instance, you can list addresses, 
list shipments, and list trackers. These list API methods share a common structure, taking at least these 
two parameters: limit, and offset.

{APP_NAME} utilizes offset-based pagination via the offset and limit parameters.
Both parameters take a number as value (see below) and return objects in reverse chronological order. 
The offset parameter returns objects listed after an index. 
The limit parameter take a limit on the number of objects to be returned from 1 to 100.


```json
{{
    "next": "/v1/shipments?limit=25&offset=50",
    "previous": "/v1/shipments?limit=25&offset=25",
    "results": [
    ]
}}
```

## Environments

The {APP_NAME} API offer the possibility to create and retrieve certain objects in `test_mode`.
In development, it is therefore possible to add carrier connections, get live rates, 
buy labels, create trackers and schedule pickups in `test_mode`.

"""

AUTHENTICATION_DESCRIPTION = """
For client-side code, we encourage the use of JSON Web Tokens (JWT) to authenticate your app.
The JWT tokens changes for every new session and have an expiration timestamp.

To authenticate via JWT access key, use `-H "Authorization: Bearer eyJ0eXAxxx...xxxaS86FjLH6U"`.
"""


class OpenAPISchemaGenerator(generators.OpenAPISchemaGenerator):
    def get_schema(self, request=None, public=False):
        """Generate a :class:`.Swagger` object with custom tags"""

        swagger = super().get_schema(request, public)
        swagger.tags = [
            {
                "name": "API",
                "description": AUTHENTICATION_DESCRIPTION
            },
            {
                "name": "Addresses",
                "description": f"""
                This is an object representing your a {APP_NAME} shipping address.
                You can retrieve all addresses related to your {APP_NAME} account.
                
                Address objects are linked to your shipment history, and can be used for recurring shipping
                to / from the same locations.
                """
            },
            {
                "name": "Carriers",
                "description": f"""
                This is an object representing your a {APP_NAME} carrier account connectsions.
                You can retrieve all configured connections available to your {APP_NAME} account.
                
                The `carrier_id` is a nickname you assign to your connection.
                """
            },
            {
                "name": "Customs",
                "description": f"""
                This is an object representing your a {APP_NAME} shipping customs declaration.
                You can retrieve all customs declarations used historically with your {APP_NAME} account shipments.
                """
            },
            {
                "name": "Parcels",
                "description": f"""
                This is an object representing your a {APP_NAME} shipping parcel.
                
                Parcel objects are linked to your shipment history, and can be used for recurring shipping
                using the same packaging.
                """
            },
            {
                "name": "Shipments",
                "description": f"""
                This is an object representing your a {APP_NAME} shipment.
                
                A Shipment guides you through process of preparing and purchasing a label for an order.
                
                A Shipment transitions through multiple statuses throughout its lifetime as the package
                shipped makes its journey to it's destination.
                """
            },
            {
                "name": "Trackers",
                "description": f"""
                This is an object representing your a {APP_NAME} shipment tracker.
                
                A shipment tracker is an object attached to a shipment by it's tracking number.
                The tracker provide the latest tracking status and events associated with a shipment 
                """
            },
            {
                "name": "Pickups",
                "description": f"""
                This is an object representing your a {APP_NAME} pickup booking.
                You can retrieve all pickup booked historically for your {APP_NAME} account shipments.
                """
            },
            {
                "name": "Webhooks",
                "description": f"""
                This is an object representing your a {APP_NAME} webhook.
                
                You can configure webhook endpoints via the API to be notified about events that happen in your 
                {APP_NAME} account. 
                """
            },
            {
                "name": "Proxy",
                "description": f"""
                In some scenarios, all we need is to send request to a carrier using the {APP_NAME} unified API.

                The Proxy API comes handy for that as it turn {APP_NAME} into a simple middleware that converts and 
                validate your request and simply forward it to the shipping carrier server.
                
                > **Note**
                >
                > When using the proxy API, no objects are created in the {APP_NAME} system.
                """
            },
        ]

        return swagger


class SwaggerAutoSchema(inspectors.SwaggerAutoSchema):
    def get_operation(self, operation_keys=None):
        operation = super().get_operation(operation_keys)

        return openapi.Operation(
            operation.operation_id,
            **{k: v for k,v in operation.items() if k != operation.operation_id},
            **{'x-code-samples': self.overrides.get('code_examples')}
        )


swagger_info = openapi.Info(
    title=f"{APP_NAME} API",
    default_version=APP_VERSION,
    description=SCHEMA_VIEW_DESCRIPTION,
    contact=openapi.Contact(email=EMAIL_SUPPORT),
)

schema_view = views.get_schema_view(
    swagger_info,
    public=True,
    permission_classes=(permissions.AllowAny,),
    generator_class=OpenAPISchemaGenerator,
)

urlpatterns = [
    path('shipping-openapi.json', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path(settings.OPEN_API_PATH, schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
]
