import logging
import importlib
from django.db.models import Q
from django.db.utils import ProgrammingError
from django.conf import settings
from django.contrib.auth import mixins, get_user_model
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.middleware import AuthenticationMiddleware as BaseAuthenticationMiddleware
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication as BaseTokenAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication as BaseJWTAuthentication

logger = logging.getLogger(__name__)
UserModel = get_user_model()


class TokenAuthentication(BaseTokenAuthentication):

    def get_model(self):
        if self.model is not None:
            return self.model
        from purpleserver.user.models import Token
        return Token

    def authenticate(self, request):
        auth = super().authenticate(request)
        if auth is not None:
            _, token = auth
            request.org = token.organization

        return auth


class JWTAuthentication(BaseJWTAuthentication):

    def authenticate(self, request):
        auth = super().authenticate(request)

        if auth is not None:
            user, validated_token = auth
            request.org = self._get_organization(validated_token, user)

        return auth

    def _get_organization(self, validated_token, user):
        """
        Attempts to find and return an organization using the given validated token.
        """
        if settings.MULTI_ORGANIZATIONS:
            try:
                from purpleserver.orgs.models import Organization

                org_id = validated_token.get('org_id')
                orgs = Organization.objects.filter(users__id=user.id)
                org = orgs.filter(id=org_id).first() if org_id is not None else orgs.filter(is_active=True).first()

                if org is not None and not org.is_active:
                    raise exceptions.AuthenticationFailed(_('Organization is inactive'), code='organization_inactive')

                if org is None and org_id is not None:
                    raise exceptions.AuthenticationFailed(
                        _('No active organization found with the given credentials'), code='organization_invalid')

                return org
            except ProgrammingError as e:
                pass

        return None


class LoginRequiredMixin(mixins.LoginRequiredMixin):
    """Verify that the current user is authenticated."""
    def dispatch(self, request, *args, **kwargs):
        try:
            auth = JWTAuthentication().authenticate(request) or TokenAuthentication().authenticate(request)

            if auth is not None:
                user, *_ = auth
                request.user = user
                return super(mixins.AccessMixin, self).dispatch(request, *args, **kwargs)

        finally:
            return super().dispatch(request, *args, **kwargs)


class AuthenticationMiddleware(BaseAuthenticationMiddleware):
    def process_response(self, request, response):
        if getattr(request, 'org', None) is not None:
            response.set_cookie('org_id', getattr(request.org, 'id', None))

        return response

    def process_request(self, request):
        super().process_request(request)

        if hasattr(request, 'user'):
            request.org = self._get_organization(request)

    def _get_organization(self, request):
        """
        Attempts to find and return an organization using the given validated token.
        """
        if settings.MULTI_ORGANIZATIONS:
            try:
                from purpleserver.orgs.models import Organization
                org_id = request.META.get('HTTP_X_ORG_ID') or request.COOKIES.get('org_id')
                orgs = Organization.objects.filter(users__id=request.user.id)
                org = orgs.filter(id=org_id).first() if org_id is not None else orgs.filter(is_active=True).first()

                # org was found but is not active
                if (org is not None) and (not org.is_active):
                    raise exceptions.AuthenticationFailed(_('Organization is inactive'), code='organization_inactive')

                # org id has been passed, but org is None and the are no existing org id affiliated with user
                if (org_id is not None) and (org is None) and (not orgs.filter(id=org_id).exists()):
                    request.COOKIES.pop('org_id')

                return org
            except ProgrammingError as e:
                pass

        return None
