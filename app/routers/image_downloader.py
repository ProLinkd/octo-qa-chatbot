import logging
import shutil

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.core.authentication import verify_static_token
from app.utils.image_downloader import ImageRejectedError, download_image

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_static_token)])


class ImageDownloaderRequest(BaseModel):
    download_url: str


@router.post(
    "/image-downloader",
    status_code=200,
    response_class=FileResponse,
)
def image_downloader(request_body: ImageDownloaderRequest):
    try:
        image = download_image(request_body.download_url)
    except ImageRejectedError as exc:
        raise HTTPException(status_code=400, detail=f"/image-downloader Failed: {exc}")
    except Exception:
        logger.exception("Error in /image-downloader")
        raise HTTPException(status_code=500, detail="/image-downloader Failed: internal error")

    return FileResponse(
        image.path,
        media_type=image.media_type,
        filename=image.filename,
        headers={"X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(shutil.rmtree, image.workdir, ignore_errors=True),
    )
