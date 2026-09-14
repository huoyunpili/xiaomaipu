from django import forms


class ShopSettingsForm(forms.Form):
    name = forms.CharField(label="店铺名称", max_length=100)
    version = forms.IntegerField(widget=forms.HiddenInput)
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
