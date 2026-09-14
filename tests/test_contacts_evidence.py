import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from app.common.business import BusinessError
from app.contacts.models import Customer
from app.contacts.views import save_customer
from app.evidence.models import EvidenceVideo
from app.evidence.views import save_video
from tests.test_business import action, draft, goods

pytestmark = pytest.mark.django_db


def test_customer_risk_waits_for_active_transaction(admin_user, shop):
    result = save_customer(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        name="客户甲",
        suggestion="PENDING",
        risk_reason="历史争议记录",
    )
    customer = Customer.objects.get(pk=result["customer_id"])
    sku, lot = goods(admin_user)
    order = draft(admin_user, sku, customer_id=customer.pk)
    assert customer.has_open_business
    assert "暂不提醒" in customer.suggestion_label
    action(admin_user, order, "cancel", reason="取消")
    assert not customer.has_open_business
    assert "自行决定" in customer.suggestion_label


def test_private_video_upload_replay_range_and_replacement(
    admin_user, shop, client, settings, tmp_path
):
    settings.PRIVATE_MEDIA_ROOT = tmp_path
    sku, lot = goods(admin_user)
    order = draft(admin_user, sku)
    payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * 128
    key = uuid.uuid4()

    def upload(key):
        return save_video(
            actor=admin_user,
            submission_key=key,
            order_id=order.pk,
            video=SimpleUploadedFile("video.mp4", payload, content_type="video/mp4"),
        )

    result = upload(key)
    assert upload(key) == result
    assert EvidenceVideo.objects.count() == 1
    url = f"/evidence/{result['video_id']}/"
    assert client.get(url).status_code == 302
    client.force_login(admin_user)
    response = client.get(url, HTTP_RANGE="bytes=4-7")
    assert response.status_code == 206
    assert b"".join(response.streaming_content) == b"ftyp"
    assert client.get(url, HTTP_RANGE="bytes=900-1000").status_code == 416
    response = client.get(url + "?download=1")
    assert b"".join(response.streaming_content) == payload
    assert "attachment" in response["Content-Disposition"]
    assert client.get(f"/orders/{order.pk}/evidence/qr/").status_code == 200
    upload(uuid.uuid4())
    assert EvidenceVideo.objects.count() == 2
    assert EvidenceVideo.objects.filter(active=True).count() == 1
    with pytest.raises(BusinessError):
        save_video(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            order_id=order.pk,
            video=SimpleUploadedFile("bad.mp4", b"not video"),
        )
