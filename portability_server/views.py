from django.shortcuts import redirect, render
from django.contrib import messages
from django.http import HttpResponse

from donations.models import Donation, Participant


def home(request):
    """Home page view."""
    if request.method == 'POST':
        token_input = request.POST.get('token_input', '').strip()
        if token_input:
            # Try to find a matching donation or participant token.
            donation = Donation.get_by_raw_token(token_input)
            if donation:
                return redirect('donation-entry', donation_token=token_input)
            participant = Participant.get_by_raw_token(token_input)
            if participant:
                return redirect('participant-entry', token=token_input)
        # If no match found, show an error message.
        messages.error(request, 'Invalid token. Please try again.')
        return render(request, 'home.html')
    else:
        return render(request, 'home.html')


def rate_limited(request, exception=None):
    return HttpResponse("Too many requests. Please try again later.", status=429)
