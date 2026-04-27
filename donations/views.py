"""Views for participant-facing donation flow."""
import uuid

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from donations.models import Donation, GoogleDonation, TikTokDonation, Participant
from donations.tasks import process_donation


SESSION_DONATION_KEY = 'donation_token'
SESSION_PARTICIPANT_KEY = 'participant_token'


def _get_session_donation(request):
    """Return the donation referenced by the session, or 404."""
    token = request.session.get(SESSION_DONATION_KEY)
    if not token:
        raise Http404("No donation in session.")
    donation = Donation.objects.filter(token=token).first()
    if donation is None:
        request.session.pop(SESSION_DONATION_KEY, None)
        raise Http404("Donation not found.")
    return donation.get_subclass()


def _get_session_participant(request):
    """Return the participant referenced by the session, or 404."""
    token = request.session.get(SESSION_PARTICIPANT_KEY)
    if not token:
        raise Http404("No participant in session.")
    participant = Participant.objects.filter(token=token).first()
    if participant is None:
        request.session.pop(SESSION_PARTICIPANT_KEY, None)
        raise Http404("Participant not found.")
    return participant


@require_http_methods(["GET"])
def donation_entry(request, donation_token):
    """Consume a donation token from the URL, store it in the session, redirect."""
    get_object_or_404(Donation, token=donation_token)
    request.session[SESSION_DONATION_KEY] = str(donation_token)
    return redirect('donation-landing')


@require_http_methods(["GET"])
def participant_entry(request, token):
    """Consume a participant token from the URL, store it in the session, redirect."""
    get_object_or_404(Participant, token=token)
    request.session[SESSION_PARTICIPANT_KEY] = str(token)
    return redirect('participant-home')


@require_http_methods(["GET"])
def select_donation(request, donation_token):
    """Switch the active donation in session to one owned by the current participant."""
    participant = _get_session_participant(request)
    donation = get_object_or_404(Donation, token=donation_token, participant=participant)
    request.session[SESSION_DONATION_KEY] = str(donation.token)
    if request.GET.get('next') == 'data':
        return redirect('data-preview')
    return redirect('donation-landing')


@require_http_methods(["GET"])
def switch_to_participant(request):
    """Switch to the participant linked to the current donation."""
    donation = _get_session_donation(request)
    if not donation.participant:
        raise Http404("Donation has no linked participant.")
    request.session[SESSION_PARTICIPANT_KEY] = str(donation.participant.token)
    return redirect('participant-home')


@require_http_methods(["GET", "POST"])
def donation_landing(request):
    """Status overview page with participant token handling."""
    donation = _get_session_donation(request)
    token_error = None

    if request.method == 'POST':
        token_input = request.POST.get('participant_token_input', '')
        try:
            token_uuid = uuid.UUID(token_input)
        except (ValueError, AttributeError):
            token_error = 'Please enter a valid token.'
        else:
            participant, _ = Participant.objects.get_or_create(token=token_uuid)
            donation.participant = participant
            donation.save()
            return redirect('donation-landing')

    if donation.participant:
        prepopulated_token = str(donation.participant.token)
    else:
        prepopulated_token = str(donation.suggested_participant_token)

    return render(request, 'donations/landing.html', {
        'donation': donation,
        'prepopulated_token': prepopulated_token,
        'token_error': token_error,
    })


@require_http_methods(["GET", "POST"])
def accept_terms(request):
    """Show terms and record acceptance."""
    donation = _get_session_donation(request)
    if request.method == 'POST':
        donation.terms_accepted_at = timezone.now()
        donation.save()
        return redirect('donation-landing')
    return render(request, 'donations/terms.html', {'donation': donation})


def authorize(request):
    """Redirect to OAuth URL. Requires terms accepted."""
    donation = _get_session_donation(request)
    if not donation.terms_accepted_at:
        return redirect('accept-terms')
    auth_url = donation.get_auth_url(request)
    return redirect(auth_url)


def data_preview(request):
    """Paginated data preview with filtering."""
    donation = _get_session_donation(request)
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
def revoke_donation(request):
    """Confirm and revoke a donation."""
    donation = _get_session_donation(request)
    if request.method == 'POST':
        if hasattr(donation, 'revoke'):
            success, message = donation.revoke()
            if not success:
                return render(request, 'donations/revoke_confirm.html', {
                    'donation': donation,
                    'error': message,
                })
        donation.delete()
        request.session.pop(SESSION_DONATION_KEY, None)
        return render(request, 'donations/revoked.html')
    return render(request, 'donations/revoke_confirm.html', {'donation': donation})


def google_auth_callback(request):
    """Handle Google OAuth callback via oauth_state lookup."""
    state = request.GET.get('state')
    if not state:
        raise Http404("Missing state parameter")
    donation = get_object_or_404(GoogleDonation, oauth_state=state)
    success, message = donation.handle_auth_callback(request)
    donation.oauth_state = None
    donation.save(update_fields=['oauth_state'])
    request.session[SESSION_DONATION_KEY] = str(donation.token)
    if success:
        if not donation.participant:
            participant, _ = Participant.objects.get_or_create(
                token=donation.suggested_participant_token)
            donation.participant = participant
            donation.save(update_fields=['participant'])
        process_donation.delay(donation.pk)
        return redirect('donation-landing')
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'error': message,
    })


def tiktok_auth_callback(request):
    """Handle TikTok OAuth callback via oauth_state lookup."""
    state = request.GET.get('state')
    if not state:
        raise Http404("Missing state parameter")
    donation = get_object_or_404(TikTokDonation, oauth_state=state)
    success, message = donation.handle_auth_callback(request)
    donation.oauth_state = None
    donation.save(update_fields=['oauth_state'])
    request.session[SESSION_DONATION_KEY] = str(donation.token)
    if success:
        if not donation.participant:
            participant, _ = Participant.objects.get_or_create(
                token=donation.suggested_participant_token)
            donation.participant = participant
            donation.save(update_fields=['participant'])
        process_donation.delay(donation.pk)
        return redirect('donation-landing')
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'error': message,
    })


def participant_home(request):
    """Show all donations for a participant."""
    participant = _get_session_participant(request)
    donations = participant.donations.order_by('-created_at')
    resolved_donations = [d.get_subclass() for d in donations]
    return render(request, 'donations/participant_home.html', {
        'participant': participant,
        'donations': resolved_donations,
    })
