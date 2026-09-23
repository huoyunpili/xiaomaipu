from celery import shared_task

from .exports import cleanup_images


@shared_task
def cleanup_exports():
    return cleanup_images()
