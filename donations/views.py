"""Views for participant-facing donation flow."""
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from donations.models import Donation, GoogleDonation, TikTokDonation


def _get_donation(participant_token):
    """Get the most specific donation subclass for a participant token."""
    donation = get_object_or_404(Donation, participant_token=participant_token)
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


def donation_landing(request, participant_token):
    """Status overview page with links based on donation status."""
    donation = _get_donation(participant_token)
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'participant_token': participant_token,
    })


@require_http_methods(["GET", "POST"])
def accept_terms(request, participant_token):
    """Show terms and record acceptance."""
    donation = _get_donation(participant_token)
    if request.method == 'POST':
        donation.terms_accepted_at = timezone.now()
        donation.save()
        return redirect('donation-landing', participant_token=participant_token)
    return render(request, 'donations/terms.html', {
        'donation': donation,
        'participant_token': participant_token,
    })


def authorize(request, participant_token):
    """Redirect to OAuth URL. Requires terms accepted."""
    donation = _get_donation(participant_token)
    if not donation.terms_accepted_at:
        return redirect('accept-terms', participant_token=participant_token)
    auth_url = donation.get_auth_url(request)
    return redirect(auth_url)


def data_preview(request, participant_token):
    """Paginated data preview with filtering."""
    donation = _get_donation(participant_token)
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
        'participant_token': participant_token,
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
def revoke_donation(request, participant_token):
    """Confirm and revoke a donation."""
    donation = _get_donation(participant_token)
    if request.method == 'POST':
        if hasattr(donation, 'revoke_before_delete'):
            donation.revoke_before_delete()
        donation.delete()
        return render(request, 'donations/revoked.html')
    return render(request, 'donations/revoke_confirm.html', {
        'donation': donation,
        'participant_token': participant_token,
    })


def google_auth_callback(request):
    """Handle Google OAuth callback via oauth_state lookup."""
    state = request.GET.get('state')
    if not state:
        raise Http404("Missing state parameter")
    donation = get_object_or_404(GoogleDonation, oauth_state=state)
    success, message = donation.handle_auth_callback(request)
    if success:
        return redirect('donation-landing', participant_token=donation.participant_token)
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'participant_token': donation.participant_token,
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
        return redirect('donation-landing', participant_token=donation.participant_token)
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'participant_token': donation.participant_token,
        'error': message,
    })
