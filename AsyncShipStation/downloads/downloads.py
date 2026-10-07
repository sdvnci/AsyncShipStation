import io
from asyncio import gather
from io import BytesIO
from typing import cast
from urllib.parse import urlparse

from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from ..common import (
    Endpoints,
    ErrorResponse,
    LabelFormats,
    ShipStationClient,
    ShipStationConnection,
)
from ..labels import Label, LabelPortal
from ._types import DownloadError


def create_packing_slip_page(order_id: str | None) -> bytes:
    """
    Generate a single-page PDF containing ``Order #<order_id>`` as
    selectable vector text.

    The page is sized to match a 4×6 shipping label so it prints
    consistently on the same label stock.  The text is centred on the
    page in a large, readable font.

    Returns:
        Raw PDF bytes for the single-page packing-slip document.
    """
    # ShipStation 4×6 label size (in points).  Labels are produced in
    # portrait orientation (4 in wide × 6 in tall).
    LABEL_WIDTH = 4 * inch  # 288 pt
    LABEL_HEIGHT = 6 * inch  # 432 pt
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(LABEL_WIDTH, LABEL_HEIGHT))

    # ── Draw "Order #<id>" centred on the page ──────────────────────
    text = f"Order #{order_id if order_id else '#00000000000'}"

    # Use Helvetica-Bold at a size that fits comfortably on a 4×6 label.
    font_name = "Helvetica-Bold"
    font_size = 28

    # Shrink the font if the text is unusually long so it doesn't
    # overflow the page width (leave 0.25 in margins on each side).
    max_text_width = LABEL_WIDTH - 0.5 * inch
    while font_size > 10:
        text_width = c.stringWidth(text, font_name, font_size)
        if text_width <= max_text_width:
            break
        font_size -= 1

    c.setFont(font_name, font_size)

    # Centre horizontally and vertically.
    x = LABEL_WIDTH / 2
    y = LABEL_HEIGHT / 2

    c.drawCentredString(x, y, text)

    # Add a subtle divider line above and below for visual clarity
    # when someone fans through the printed stack.
    line_margin = 0.5 * inch
    line_y_top = y + font_size + 12
    line_y_bot = y - 18
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.5)
    c.line(line_margin, line_y_top, LABEL_WIDTH - line_margin, line_y_top)
    c.line(line_margin, line_y_bot, LABEL_WIDTH - line_margin, line_y_bot)

    # A small footer so the page is clearly identifiable as a separator.
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawCentredString(LABEL_WIDTH / 2, 0.4 * inch, "— Packing Slip Separator —")

    c.showPage()
    c.save()
    return buf.getvalue()


class DownloadPortal(ShipStationClient):
    @classmethod
    async def download_file(
        cls: type["DownloadPortal"],
        connection: ShipStationConnection,
        dir: str,
        subdir: str,
        filename: str,
        download: str = "string",
        rotation: int = 0,
        identity: bool = False,
    ) -> tuple[int, bytes | ErrorResponse]:
        endpoint = f"{connection.v2_endpoint}/{Endpoints.DOWNLOADS.value}/{dir}/{subdir}/{filename}"
        params = {
            "download": download,
            "rotation": rotation,
        }

        try:
            res = await connection.request(
                "GET",
                endpoint,
                params=params,  # type: ignore[arg-type]
                headers={"content-type": "application/pdf"},
            )
            if res.status_code != 200:
                status, error = cls.validate_response(res, (200,), bytes)
                return (status, cast(ErrorResponse, error))

        except Exception as e:
            return cls.parse_unknown_exception(e)

        return (res.status_code, res.content)

    @classmethod
    async def download_packing_slip(
        cls: type["DownloadPortal"],
        connection: ShipStationConnection,
        label: Label,
        dtype: LabelFormats = "pdf",
        identity: bool = False,
    ) -> tuple[int, bytes | ErrorResponse]:

        links = label["label_download"]
        pdf_href = links.get(dtype, links.get("href", None))

        try:
            if not pdf_href:
                raise Exception("Label download not found")

            parsed = urlparse(pdf_href)
            path_parts = parsed.path.split("/")
            filename_part = path_parts[-1]
            subdir_part = path_parts[-2]
            dir_part = path_parts[-3]

        except Exception as e:
            return cls.parse_unknown_exception(e)

        return await cls.download_file(
            connection,
            dir_part,
            subdir_part,
            filename_part,
            identity=identity,
        )

    @classmethod
    async def download_packing_slips(
        cls: type["DownloadPortal"],
        connection: ShipStationConnection,
        labels: list[Label],
        dtype: LabelFormats = "pdf",
        include_dummy_slips: bool = True,
        timeout: int | None = None,
        interval: int = 2,
        identity: bool = False,
    ) -> tuple[int, tuple[bytes, list[DownloadError]]]:

        try:
            timeout = timeout or max(5 * len(labels), 60)
            available, _ = await LabelPortal.poll_labels_until_ready(
                connection,
                [label["label_id"] for label in labels],
                timeout=timeout,
                interval=interval,
            )

            slips = await gather(
                *[
                    cls.download_packing_slip(
                        connection,
                        label,
                        dtype,
                        identity=identity,
                    )
                    for label in labels
                ]
            )

            writer = PdfWriter()
            errors: list[DownloadError] = []
            for index, (stat, slip) in enumerate(slips):
                if stat not in (200, 201, 207):
                    if isinstance(slip, dict):
                        errors.append(
                            cast(
                                DownloadError,
                                {
                                    "shipment_id": labels[index].get("shipment_id"),
                                    "external_shipment_id": labels[index].get(
                                        "external_shipment_id"
                                    ),
                                    "error": slip,
                                },
                            )
                        )
                    continue
                if not isinstance(slip, bytes):
                    errors.append(
                        cast(
                            DownloadError,
                            {
                                "shipment_id": labels[index].get("shipment_id"),
                                "external_shipment_id": labels[index].get(
                                    "external_shipment_id"
                                ),
                            },
                        )
                    )
                    continue

                slip_pdf = PdfReader(BytesIO(slip))
                for page in slip_pdf.pages:
                    writer.add_page(page)

                # If there was only one page, we optionally include a dummy packing slip after it to act as a separator when printing.
                if include_dummy_slips and len(slip_pdf.pages) == 1:
                    dummy_slip_bytes = create_packing_slip_page(
                        labels[index]["external_shipment_id"]
                    )
                    dummy_pdf = PdfReader(BytesIO(dummy_slip_bytes))
                    writer.add_page(dummy_pdf.pages[0])

            out = BytesIO()
            writer.write(out)

            if out.tell() == 0:
                raise ValueError("No packing slips were downloaded")

            return 200, (out.getvalue(), errors)

        except Exception as e:
            stat, err = cls.parse_unknown_exception(e)
            return stat, (
                b"",
                [
                    cast(
                        DownloadError,
                        {
                            "error": err,
                        },
                    )
                ],
            )
