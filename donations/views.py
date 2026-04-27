"""Views for participant-facing donation flow.

Donation and participant tokens are stored hashed in the database. The
**raw** token (UUID) is what lives in the session — never the hash. Lookups
hash on the fly. This means a DB read alone yields no usable session
credential.
"""
import uuid

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods


PARTICIPANT_TOKEN_MIN_LENGTH = 32

from donations.models import Donation, GoogleDonation, TikTokDonation, Participant, hash_token
from donations.tasks import process_donation


SESSION_DONATION_KEY = 'donation_token'        # raw UUID
SESSION_PARTICIPANT_KEY = 'participant_token'  # raw UUID


def _get_session_donation(request):
    """Return the donation referenced by the session (raw token), or 404."""
    raw = request.session.get(SESSION_DONATION_KEY)
    if not raw:
        raise Http404("No donation in session.")
    donation = Donation.get_by_raw_token(raw)
    if donation is None:
        request.session.pop(SESSION_DONATION_KEY, None)
        raise Http404("Donation not found.")
    return donation.get_subclass()


def _get_session_participant(request, fallback_via_donation=True):
    """Return the participant referenced by the session, or 404.

    If ``fallback_via_donation`` is true and no participant is in session,
    fall back to the participant linked to the session's donation.
    """
    raw = request.session.get(SESSION_PARTICIPANT_KEY)
    if raw:
        participant = Participant.get_by_raw_token(raw)
        if participant is not None:
            return participant
        request.session.pop(SESSION_PARTICIPANT_KEY, None)
    if fallback_via_donation:
        donation_raw = request.session.get(SESSION_DONATION_KEY)
        if donation_raw:
            donation = Donation.get_by_raw_token(donation_raw)
            if donation and donation.participant_id:
                return donation.participant
    raise Http404("No participant in session.")


def _set_donation_session(request, raw_token):
    request.session[SESSION_DONATION_KEY] = str(raw_token)


def _set_participant_session(request, raw_token):
    request.session[SESSION_PARTICIPANT_KEY] = str(raw_token)


def _ensure_donation_session(request, donation):
    """Ensure the session's raw donation token resolves to ``donation``.

    The session likely already has the right raw token (set at donation_entry).
    If it doesn't match (lost session, different donation), rotate the token
    so we have a raw form to store — the original raw is unrecoverable from
    the DB hash.
    """
    raw = request.session.get(SESSION_DONATION_KEY)
    if raw and hash_token(raw) == donation.token:
        return
    _set_donation_session(request, donation.regenerate_token())


@require_http_methods(["GET"])
def donation_entry(request, donation_token):
    """Consume a donation token from the URL, store it in the session, redirect."""
    donation = Donation.get_by_raw_token(donation_token)
    if donation is None:
        raise Http404("Donation not found.")
    _set_donation_session(request, donation_token)
    return redirect('donation-landing')


@require_http_methods(["GET"])
def participant_entry(request, token):
    """Consume a participant token from the URL, store it in the session, redirect."""
    participant = Participant.get_by_raw_token(token)
    if participant is None:
        raise Http404("Participant not found.")
    _set_participant_session(request, token)
    return redirect('participant-home')


@require_http_methods(["GET"])
def select_donation(request, donation_pk):
    """Switch the active donation in session to one owned by the current participant.

    Rotates the donation token because the raw form is unrecoverable from the DB.
    """
    participant = _get_session_participant(request)
    donation = get_object_or_404(Donation, pk=donation_pk, participant=participant)
    raw = donation.regenerate_token()
    _set_donation_session(request, raw)
    if request.GET.get('next') == 'data':
        return redirect('data-preview')
    return redirect('donation-landing')


@require_http_methods(["GET"])
def switch_to_participant(request):
    """Verify the current donation has a participant, redirect to participant home."""
    donation = _get_session_donation(request)
    if not donation.participant_id:
        raise Http404("Donation has no linked participant.")
    return redirect('participant-home')


def _participant_link_url(request):
    """Build the absolute participant URL if the session has a valid raw token.

    Returns ``None`` when no displayable raw token is available.
    """
    raw = request.session.get(SESSION_PARTICIPANT_KEY)
    if not raw or Participant.get_by_raw_token(raw) is None:
        return None
    return request.build_absolute_uri(
        reverse('participant-entry', kwargs={'token': raw}))


@require_http_methods(["POST"])
def generate_participant_token(request):
    """Link the current donation to the participant identified by the
    donation's ``suggested_participant_token`` and stash the raw token in the
    session for display. If the donation is already linked to a different
    participant, this re-links it (the original participant is unaffected;
    its other donations remain linked to it)."""
    donation = _get_session_donation(request)
    suggested = donation.suggested_participant_token
    suggested_hash = hash_token(suggested)
    if donation.participant_id is None or donation.participant.token != suggested_hash:
        participant, _ = Participant.objects.get_or_create(token=suggested_hash)
        donation.participant = participant
        donation.save()
    _set_participant_session(request, suggested)
    return redirect('donation-landing')


@require_http_methods(["GET", "POST"])
def donation_landing(request):
    """Status overview page with participant token handling."""
    donation = _get_session_donation(request)
    token_error = None

    if request.method == 'POST':
        token_input = request.POST.get('participant_token_input', '').strip()
        if not token_input:
            return redirect('donation-landing')
        if len(token_input) < PARTICIPANT_TOKEN_MIN_LENGTH:
            token_error = (
                f'Token is too short. Use a UUID '
                f'(at least {PARTICIPANT_TOKEN_MIN_LENGTH} characters).'
            )
        else:
            try:
                token_uuid = uuid.UUID(token_input)
            except (ValueError, AttributeError):
                token_error = 'Please enter a valid token (UUID format).'
            else:
                participant = Participant.get_by_raw_token(token_uuid)
                if participant is None:
                    participant = Participant(token=hash_token(token_uuid))
                    participant._raw_token = str(token_uuid)
                    participant.save()
                donation.participant = participant
                donation.save()
                _set_participant_session(request, token_uuid)
                return redirect('donation-landing')

    suggested_participant_token = None
    raw = request.session.get(SESSION_PARTICIPANT_KEY)
    if raw and Participant.get_by_raw_token(raw) is not None:
        suggested_participant_token = raw

    return render(request, 'donations/landing.html', {
        'donation': donation,
        'participant_link_url': _participant_link_url(request),
        'token_error': token_error,
        'suggested_participant_token': suggested_participant_token,
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


def _ensure_participant_for_donation(donation):
    """After OAuth, link a fresh participant if one isn't already attached."""
    if donation.participant:
        return
    suggested = donation.suggested_participant_token
    participant = Participant.get_by_raw_token(suggested)
    if participant is None:
        participant = Participant(token=hash_token(suggested))
        participant._raw_token = str(suggested)
        participant.save()
    donation.participant = participant
    donation.save(update_fields=['participant'])


def google_auth_callback(request):
    """Handle Google OAuth callback via oauth_state lookup."""
    state = request.GET.get('state')
    if not state:
        raise Http404("Missing state parameter")
    donation = get_object_or_404(GoogleDonation, oauth_state=state)
    success, message = donation.handle_auth_callback(request)
    donation.oauth_state = None
    donation.save(update_fields=['oauth_state'])
    _ensure_donation_session(request, donation)
    if success:
        _ensure_participant_for_donation(donation)
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
    _ensure_donation_session(request, donation)
    if success:
        _ensure_participant_for_donation(donation)
        process_donation.delay(donation.pk)
        return redirect('donation-landing')
    return render(request, 'donations/landing.html', {
        'donation': donation,
        'error': message,
    })


def participant_home(request):
    """Show all donations for a participant."""
    participant = _get_session_participant(request)
    raw = request.session.get(SESSION_PARTICIPANT_KEY)
    if raw and hash_token(raw) != participant.token:
        raw = None
    donations = participant.donations.order_by('-created_at')
    resolved_donations = [d.get_subclass() for d in donations]
    return render(request, 'donations/participant_home.html', {
        'participant': participant,
        'raw_participant_token': raw,
        'donations': resolved_donations,
    })
