from __future__ import annotations

# nuitka-project: --standalone
# nuitka-project: --output-dir=build/nuitka_sidecar_external_node_safe_probe
# nuitka-project: --include-package=app
# nuitka-project: --nofollow-import-to=moviepy
# nuitka-project: --nofollow-import-to=imageio
# nuitka-project: --nofollow-import-to=imageio_ffmpeg
# nuitka-project: --nofollow-import-to=numpy
# nuitka-project: --nofollow-import-to=PIL.AvifImagePlugin
# nuitka-project: --nofollow-import-to=PIL.BlpImagePlugin
# nuitka-project: --nofollow-import-to=PIL.BufrStubImagePlugin
# nuitka-project: --nofollow-import-to=PIL.CurImagePlugin
# nuitka-project: --nofollow-import-to=PIL.DcxImagePlugin
# nuitka-project: --nofollow-import-to=PIL.DdsImagePlugin
# nuitka-project: --nofollow-import-to=PIL.EpsImagePlugin
# nuitka-project: --nofollow-import-to=PIL.FitsImagePlugin
# nuitka-project: --nofollow-import-to=PIL.FliImagePlugin
# nuitka-project: --nofollow-import-to=PIL.FpxImagePlugin
# nuitka-project: --nofollow-import-to=PIL.FtexImagePlugin
# nuitka-project: --nofollow-import-to=PIL.GbrImagePlugin
# nuitka-project: --nofollow-import-to=PIL.GribStubImagePlugin
# nuitka-project: --nofollow-import-to=PIL.Hdf5StubImagePlugin
# nuitka-project: --nofollow-import-to=PIL.IcnsImagePlugin
# nuitka-project: --nofollow-import-to=PIL.IcoImagePlugin
# nuitka-project: --nofollow-import-to=PIL.ImageCms
# nuitka-project: --nofollow-import-to=PIL.ImageShow
# nuitka-project: --nofollow-import-to=PIL.ImImagePlugin
# nuitka-project: --nofollow-import-to=PIL.IptcImagePlugin
# nuitka-project: --nofollow-import-to=PIL.Jpeg2KImagePlugin
# nuitka-project: --nofollow-import-to=PIL.MicImagePlugin
# nuitka-project: --nofollow-import-to=PIL.MpegImagePlugin
# nuitka-project: --nofollow-import-to=PIL.MpoImagePlugin
# nuitka-project: --nofollow-import-to=PIL.MspImagePlugin
# nuitka-project: --nofollow-import-to=PIL.PalmImagePlugin
# nuitka-project: --nofollow-import-to=PIL.PcdImagePlugin
# nuitka-project: --nofollow-import-to=PIL.PcxImagePlugin
# nuitka-project: --nofollow-import-to=PIL.PdfImagePlugin
# nuitka-project: --nofollow-import-to=PIL.PdfParser
# nuitka-project: --nofollow-import-to=PIL.PixarImagePlugin
# nuitka-project: --nofollow-import-to=PIL.PsdImagePlugin
# nuitka-project: --nofollow-import-to=PIL.QoiImagePlugin
# nuitka-project: --nofollow-import-to=PIL.SgiImagePlugin
# nuitka-project: --nofollow-import-to=PIL.SpiderImagePlugin
# nuitka-project: --nofollow-import-to=PIL.SunImagePlugin
# nuitka-project: --nofollow-import-to=PIL.TgaImagePlugin
# nuitka-project: --nofollow-import-to=PIL.WebPImagePlugin
# nuitka-project: --nofollow-import-to=PIL.WmfImagePlugin
# nuitka-project: --nofollow-import-to=PIL.XbmImagePlugin
# nuitka-project: --nofollow-import-to=PIL.XpmImagePlugin
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/.venv/Lib/site-packages/tiktok_uploader/config.toml=tiktok_uploader/config.toml
# nuitka-project: --noinclude-data-files=googleapiclient/discovery_cache/documents/*.json
# nuitka-project: --noinclude-data-files=playwright/driver/node.exe
# nuitka-project: --noinclude-data-files=playwright/driver/node
# nuitka-project: --assume-yes-for-downloads

# pyrefly: ignore [missing-import]
import uvicorn

from app.config import (
    OMNIPUBLISHER_HOST,
    OMNIPUBLISHER_PORT,
    configure_external_playwright_node,
)


def main() -> None:
    configure_external_playwright_node()
    uvicorn.run(
        "app.main:app",
        host=OMNIPUBLISHER_HOST,
        port=OMNIPUBLISHER_PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
