"""Views for participant-facing donation flow."""
import uuid

from django.core.paginator import Paginator
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from donations.models import Donation, GoogleDonation, TikTokDonation, Participant


def _get_donation(donation_token):
    """Get the most specific donation subclass for a donation token."""
    donation = get_object_or_404(Donation, token=donation_token)
    # Try to get the specific subclass
    try:
        return donation.googledonation
    except GoogleDonation.DoesNotExist:
        pass
    try:
        return donation.tiktokdonation
    except TikTokDonation.DoesNotExist:
        pass
    return donation


@require_http_methods(["GET", "POST"])
def donation_landing(request, donation_token):
    """Status overview page with participant token handling."""
    donation = _get_donation(donation_token)
    token_error = None

    if request.method == 'POST':
        token_input = request.POST.get('participant_token_input', '')
        try:
            token_uuid = uuid.UUID(token_input)
        except (ValueError, AttributeError):
            token_error = 'Please enter a valid token.'
        else:
            participant, created = Participant.objects.get_or_create(token=token_uuid)
            donation.participant = participant
            donation.save()
            return redirect('donation-landing', donation_token=donation_token)

    # Prepopulate with existing participant token or generate new one
    if donation.participant:
        prepopulated_token = str(donation.participant.token)
    else:
        prepopulated_token = str(uuid.uuid4())

    return render(request, 'donations/landing.html', {
        'donation': donation,
        'donation_token': donation_token,
        'prepopulated_token': prepopulated_token,
        'token_error': token_error,
    })


@require_http_methods(["GET", "POST"])
def accept_terms(request, donation_token):
    """Show terms and record acceptance."""
    donation = _get_donation(donation_token)
    if request.method == 'POST':
        donation.terms_accepted_at = timezone.now()
        donation.save()
        return redirect('donation-landing', donation_token=donation_token)
    return render(request, 'donations/terms.html', {
        'donation': donation,
        'donation_token': donation_token,
    })


def authorize(request, donation_token):
    """Redirect to OAuth URL. Requires terms accepted."""
    donation = _get_donation(donation_token)
    if not donation.terms_accepted_at:
        return redirect('accept-terms', donation_token=donation_token)
    auth_url = donation.get_auth_url(request)
    return redirect(auth_url)


def data_preview(request, donation_token):
    """Paginated data preview with filtering."""
    donation = _get_donation(donation_token)
    data_types = donation.get_data_types()
    selected_type = request.GET.get('data_type', data_types[0] if data_types else '')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    page_number = request.GET.get('page', 1)

    rows = []
    total_count = 0
    columns = []
    if selected_type and selected_type in data_types:
        total_count = donation.count_rows(selected_type, start_date=start_date, end_date=end_date)
        all_rows = donation.fetch_data(selected_type, limit=10000, start_date=start_date, end_date=end_date)
        if all_rows:
            columns = list(all_rows[0].keys())
        paginator = Paginator(all_rows, 50)
        page_obj = paginator.get_page(page_number)
        rows = page_obj
    else:
        paginator = Paginator([], 50)
        page_obj = paginator.get_page(1)
        rows = page_obj

    return render(request, 'donations/data_preview.html', {
        'donation': donation,
        'donation_token': donation_token,
        'data_types': data_types,
        'selected_type': selected_type,
        'start_date': start_date or '',
        'end_date': end_date or '',
        'rows': rows,
        'columns': columns,
        'total_count': total_count,
        'page_obj': page_obj if 'page_obj' in dir() else rows,
    })


@require_http_methods(["GET", "POST"])
def revoke_donation(request, donation_token):
    """Confirm and revoke a donation."""
    donation = _get_donation(donation_token)
    if request.method == 'POST':
        if hasattr(donation, 'revoke_before_delete'):
            donation.revoke_before_delete()
        donation.delete()
        return render(request, 'donations/revoked.html')
    return render(request, 'donations/revoke_confirm.html', {
        'donation': donation,
        'donation_token': donation_token,
    })


def google_auth_callback(request):
    """Handle Google OAuth callback via oauth_state lookup."""
    state = request.GET.get('state')
    if not state:
        raise Http404("Missing state parameter")
    donation = get_object_or_404(GoogleDonation, oauth_state=state)
    success, message = donation.handle_auth_callback(request)
    if success:
        return redirect('donation-landing', donation_token=donation.token)
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'donation_token': donation.token,
        'error': message,
    })


def tiktok_auth_callback(request):
    """Handle TikTok OAuth callback via oauth_state lookup."""
    state = request.GET.get('state')
    if not state:
        raise Http404("Missing state parameter")
    donation = get_object_or_404(TikTokDonation, oauth_state=state)
    success, message = donation.handle_auth_callback(request)
    if success:
        return redirect('donation-landing', donation_token=donation.token)
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'donation_token': donation.token,
        'error': message,
    })


def participant_home(request, token):
    """Show all donations for a participant."""
    participant = get_object_or_404(Participant, token=token)
    donations = participant.donations.order_by('-created_at')
    # Resolve each donation to its most specific subclass
    resolved_donations = []
    for d in donations:
        try:
            resolved_donations.append(d.googledonation)
        except GoogleDonation.DoesNotExist:
            try:
                resolved_donations.append(d.tiktokdonation)
            except TikTokDonation.DoesNotExist:
                resolved_donations.append(d)
    return render(request, 'donations/participant_home.html', {
        'participant': participant,
        'donations': resolved_donations,
    })
