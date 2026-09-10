"""REST API for researcher donation management."""
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.decorators import ratelimit
from rest_framework import authentication as drf_authentication, serializers, status, viewsets
from rest_framework.decorators import (
    action, api_view, authentication_classes, permission_classes as perm_classes,
)
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from donations.audit import audit
from donations.models import Donation, GoogleDonation, TikTokDonation, TikTokExportDonation, ResearcherToken
from donations.researcher_auth.sessions import create_session, resolve_session, revoke_session
from donations.researcher_auth.tokens import resolve_researcher_token


SOURCE_TYPE_MODEL_MAP = {
    'google_portability': GoogleDonation,
    'tiktok_portability': TikTokDonation,
    'tiktok_export': TikTokExportDonation,
}


class IsResearcherAuthenticated(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, ResearcherToken)


class DonationCreateSerializer(serializers.Serializer):
    """Parameters for creating a new donation."""
    source_type = serializers.ChoiceField(
        choices=list(SOURCE_TYPE_MODEL_MAP.keys()),
        help_text="Data source: 'google_portability', 'tiktok_export' or 'tiktok_portability'.",
    )
    data_start_date = serializers.DateField(
        required=False,
        help_text="Only include data from this date onward (YYYY-MM-DD). Optional.",
    )
    data_end_date = serializers.DateField(
        required=False,
        help_text="Only include data up to this date (YYYY-MM-DD). Optional.",
    )
    requested_data_types = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text="Data types to collect, e.g. ['youtube_history', 'search']. "
                  "Empty means all available. Google options: youtube_history, discover, "
                  "google_lens, google_play_games, google_play_store, image_search, search, video_search.",
    )


class DonationSerializer(serializers.ModelSerializer):
    token = serializers.SerializerMethodField()
    donation_url = serializers.SerializerMethodField()

    class Meta:
        model = Donation
        fields = ['id', 'token', 'source_type', 'status', 'created_at', 'data_start_date', 'data_end_date', 'requested_data_types', 'donation_url']
        read_only_fields = fields

    def get_token(self, obj):
        # Tokens are stored hashed; the raw value is only available on the
        # instance returned from create(). For list/retrieve responses the
        # original token cannot be recovered.
        return getattr(obj, '_raw_token', None)

    def get_donation_url(self, obj):
        raw = getattr(obj, '_raw_token', None)
        if raw is None:
            return None
        path = reverse('donation-entry', kwargs={'donation_token': raw})
        request = self.context.get('request')
        return request.build_absolute_uri(path) if request else path


class DataQuerySerializer(serializers.Serializer):
    data_type = serializers.CharField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, default=1000, min_value=1, max_value=1000)
    offset = serializers.IntegerField(required=False, default=0, min_value=0)


class DonationViewSet(viewsets.GenericViewSet):
    permission_classes = [IsResearcherAuthenticated]
    serializer_class = DonationSerializer
    throttle_scope = "researcher"
    
    def get_serializer_class(self):
        if self.action == 'create':
            return DonationCreateSerializer
        return DonationSerializer

    def get_queryset(self):
        return Donation.objects.filter(researcher=self.request.auth)

    def list(self, request):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def create(self, request):
        create_serializer = DonationCreateSerializer(data=request.data)
        create_serializer.is_valid(raise_exception=True)
        source_type = create_serializer.validated_data['source_type']
        model_class = SOURCE_TYPE_MODEL_MAP[source_type]
        create_kwargs = {'researcher': request.auth}
        for field in ('data_start_date', 'data_end_date', 'requested_data_types'):
            value = create_serializer.validated_data.get(field)
            if value:
                create_kwargs[field] = value
        donation = model_class.objects.create(**create_kwargs)
        serializer = DonationSerializer(donation, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        donation = self.get_object()
        donation = donation.get_subclass()
        serializer = self.get_serializer(donation)
        return Response(serializer.data)

    def destroy(self, request, pk=None):
        donation = self.get_object()
        donation = donation.get_subclass()
        audit('donation_deleted', request, donation, actor=f"researcher:{request.auth.pk}")
        if hasattr(donation, 'revoke'):
            donation.revoke()
        donation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='can-delete')
    def can_delete(self, request, pk=None):
        """Signal that a verified copy exists and the donation may be released.

        This does not delete anything: it starts a shorter retention clock,
        after which the service deletes the donation itself. Deleting now is
        what ``DELETE`` is for.
        """
        donation = self.get_object()
        audit('can_delete_confirmed', request, donation, actor=f"researcher:{request.auth.pk}")
        if donation.can_delete_at is None:
            donation.can_delete_at = timezone.now()
            donation.save(update_fields=['can_delete_at'])
        return Response({
            'can_delete_at': donation.can_delete_at,
            'delete_after_days': settings.CAN_DELETE_RETENTION_DAYS,
        })

    @action(detail=True, methods=['get'], url_path='data')
    def data(self, request, pk=None):
        donation = self.get_object()
        donation = donation.get_subclass()

        query_serializer = DataQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        params = query_serializer.validated_data

        data_type = params.get('data_type')
        audit('data_read', request, donation, actor=f"researcher:{request.auth.pk}",
              data_type=data_type or 'list', offset=params.get('offset'), limit=params.get('limit'))
        if not data_type:
            return Response({'data_types': donation.get_data_types()})

        available = donation.get_data_types()
        if data_type not in available:
            return Response({'count': 0, 'data': []})

        count = donation.count_rows(
            data_type,
            start_date=params.get('start_date'),
            end_date=params.get('end_date'),
        )
        rows = donation.fetch_data(
            data_type,
            limit=params.get('limit', 1000),
            offset=params.get('offset', 0),
            start_date=params.get('start_date'),
            end_date=params.get('end_date'),
        )
        return Response({'count': count, 'data': rows})


class SessionLoginSerializer(serializers.Serializer):
    """Parameters for exchanging a researcher token for a session."""
    token = serializers.CharField(help_text="The researcher's static API token.")


def _session_login(request):
    """POST /api/session/: exchange a researcher token for a session token."""
    serializer = SessionLoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    token = resolve_researcher_token(serializer.validated_data['token'])
    if token is None:
        return Response({'detail': 'Invalid or expired token.'}, status=status.HTTP_401_UNAUTHORIZED)
    raw_key, session = create_session(token)
    audit('session_login', request, actor=f"researcher:{token.pk}")
    return Response({'session_token': raw_key, 'expires_at': session.expires_at})


def _session_logout(request):
    """DELETE /api/session/: end the session named in the Authorization header."""
    auth_header = drf_authentication.get_authorization_header(request).split()
    if len(auth_header) != 2:
        return Response(status=status.HTTP_401_UNAUTHORIZED)
    session = resolve_session(auth_header[1].decode())
    if session is None:
        return Response(status=status.HTTP_401_UNAUTHORIZED)
    revoke_session(session)
    return Response(status=status.HTTP_204_NO_CONTENT)


@ratelimit(key="ip", rate="10/m", block=True)
@api_view(['POST', 'DELETE'])
@authentication_classes([])
@perm_classes([AllowAny])
def session_view(request):
    """Issue (POST) or revoke (DELETE) a researcher API session.

    No DRF authentication class runs here: POST authenticates by exchanging
    the researcher token in the request body, and DELETE resolves the
    session itself from the Authorization header.
    """
    if request.method == 'POST':
        return _session_login(request)
    return _session_logout(request)


def _serializer_fields_info(serializer_class):
    """Extract field info from a serializer class for documentation."""
    fields = []
    for name, field in serializer_class().get_fields().items():
        fields.append({
            'name': name,
            'type': type(field).__name__,
            'required': field.required,
            'help_text': str(field.help_text) if field.help_text else '',
        })
    return fields


@ratelimit(key="ip", rate="30/m", block=True)
@api_view(['GET'])
@perm_classes([AllowAny])
def api_docs(request):
    """Public API documentation. No authentication required."""
    return Response({
        'authentication': {
            'method': 'Token',
            'header': 'Authorization: Token <session_token>',
            'description': (
                'Exchange your researcher API token for a session token at '
                'POST /api/session/, then send it as this header on every '
                'other endpoint. The researcher token itself is never accepted '
                'as credentials. Sessions expire; log in again to get a new one.'
            ),
        },
        'endpoints': {
            'POST /api/session/': {
                'description': 'Exchange a researcher token for a session token.',
                'parameters': _serializer_fields_info(SessionLoginSerializer),
                'response': "{'session_token': ..., 'expires_at': ...}",
            },
            'DELETE /api/session/': {
                'description': 'End the session named in the Authorization header.',
            },
            'POST /api/donations/': {
                'description': 'Create a new donation.',
                'parameters': _serializer_fields_info(DonationCreateSerializer),
            },
            'GET /api/donations/': {
                'description': 'List all donations for the authenticated researcher.',
                'response': 'Array of donation objects.',
            },
            'GET /api/donations/{id}/': {
                'description': 'Get donation details.',
                'response_fields': _serializer_fields_info(DonationSerializer),
            },
            'DELETE /api/donations/{id}/': {
                'description': 'Revoke and delete a donation immediately.',
            },
            'POST /api/donations/{id}/can-delete/': {
                'description': (
                    'Signal that a verified copy of the data is held and the donation may '
                    'be released. Does not delete: it starts a shorter retention clock, '
                    'after which the service deletes the donation itself.'
                ),
            },
            'GET /api/donations/{id}/data/': {
                'description': 'Query processed donation data. Without data_type parameter, returns available data types.',
                'parameters': _serializer_fields_info(DataQuerySerializer),
            },
        },
    })
