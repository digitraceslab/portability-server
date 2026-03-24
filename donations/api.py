"""REST API for researcher donation management."""
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
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
    source_type = serializers.ChoiceField(choices=list(SOURCE_TYPE_MODEL_MAP.keys()))


class DonationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Donation
        fields = ['id', 'token', 'source_type', 'status', 'created_at', 'data_start_date', 'data_end_date']
        read_only_fields = fields


class DataQuerySerializer(serializers.Serializer):
    data_type = serializers.CharField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, default=1000)
    offset = serializers.IntegerField(required=False, default=0)


class DonationViewSet(viewsets.GenericViewSet):
    permission_classes = [IsResearcherAuthenticated]
    serializer_class = DonationSerializer

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
        donation = model_class.objects.create(researcher=request.auth)
        serializer = self.get_serializer(donation)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        donation = self.get_object()
        donation = donation.get_subclass()
        serializer = self.get_serializer(donation)
        return Response(serializer.data)

    def destroy(self, request, pk=None):
        donation = self.get_object()
        donation = donation.get_subclass()
        if hasattr(donation, 'revoke_before_delete'):
            donation.revoke_before_delete()
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
