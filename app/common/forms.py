import uuid
from decimal import Decimal

from django import forms


class SubmissionForm(forms.Form):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label_suffix", "")
        super().__init__(*args, **kwargs)
        self.fields["submission_key"].initial = uuid.uuid4()


def money_field(label, *, initial=None, required=True):
    return forms.DecimalField(
        label=label,
        min_value=0,
        max_value=Decimal("10000000000.00"),
        max_digits=13,
        decimal_places=2,
        initial=initial,
        required=required,
    )


def to_fen(value):
    return int(value * 100)
