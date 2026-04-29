"""REST API for researcher donation management."""
from django.urls import reverse
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes as perm_classes
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken


SOURCE_TYPE_MODEL_MAP = {
    'google_portability': GoogleDonation,
    'tiktok_portability': TikTokDonation,
}


class IsResearcherAuthenticated(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, ResearcherToken)


class DonationCreateSerializer(serializers.Serializer):
    """Parameters for creating a new donation."""
    source_type = serializers.ChoiceField(
        choices=list(SOURCE_TYPE_MODEL_MAP.keys()),
        help_text="Data source: 'google_portability' or 'tiktok_portability'.",
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
    limit = serializers.IntegerField(required=False, default=1000)
    offset = serializers.IntegerField(required=False, default=0)


class DonationViewSet(viewsets.GenericViewSet):
    permission_classes = [IsResearcherAuthenticated]
    serializer_class = DonationSerializer
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
        if hasattr(donation, 'revoke'):
            donation.revoke()
        donation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'], url_path='data')
    def data(self, request, pk=None):
        donation = self.get_object()
        donation = donation.get_subclass()

        query_serializer = DataQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        params = query_serializer.validated_data

        data_type = params.get('data_type')
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


@api_view(['GET'])
@perm_classes([AllowAny])
def api_docs(request):
    """Public API documentation. No authentication required."""
    return Response({
        'authentication': {
            'method': 'Token',
            'header': 'Authorization: Token <researcher_token>',
            'description': 'All endpoints except this one require a researcher API token.',
        },
        'endpoints': {
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
                'description': 'Revoke and delete a donation.',
            },
            'GET /api/donations/{id}/data/': {
                'description': 'Query processed donation data. Without data_type parameter, returns available data types.',
                'parameters': _serializer_fields_info(DataQuerySerializer),
            },
        },
    })
