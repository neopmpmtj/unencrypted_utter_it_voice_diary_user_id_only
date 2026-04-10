from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def microphone_test(request):
    return render(request, 'test_microphone/microphone_test.html')
