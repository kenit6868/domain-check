"""Shared Quick Report provider/export helpers; no config or runtime imports."""
from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from xml.sax.saxutils import escape

NETCRAFT_ENDPOINT = "https://report.netcraft.com/api/nightly/report/urls"
NICTOP_HEADERS = ("Domain", "Phishing URL", "Description", "Brand Impersonated", "Date Detected")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def submit_netcraft(payload: dict) -> dict:
    """One verified-TLS request to the fixed provider; never retry or follow redirects."""
    request = urllib.request.Request(
        NETCRAFT_ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            code = response.status
            # Provider response bodies can contain submitted data. Do not persist them.
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
    except Exception:
        return {"state": "UNKNOWN", "code": "connection_outcome_unknown"}
    if code in (200, 201, 204):
        return {"state": "SUBMITTED", "code": "accepted", "http_status": code}
    if code in (400, 401, 403, 404, 405, 413, 415, 422, 429):
        return {"state": "FAILED", "code": "provider_rejected", "http_status": code}
    return {"state": "UNKNOWN", "code": "provider_outcome_unknown", "http_status": code}


def build_nictop_workbook(targets: list[tuple[str, str]], *, brand_name: str, detected_date: str) -> bytes:
    """Five-column XLSX, full URLs retained, all values stored as literal strings."""
    if not 1 <= len(targets) <= 200:
        raise ValueError("nic.top export requires 1 to 200 targets")
    if any(not domain.lower().endswith(".top") for domain, _ in targets):
        raise ValueError("nic.top export accepts only .top domains")
    rows = [NICTOP_HEADERS]
    for domain, url in targets:
        rows.append((domain, url, "Suspected phishing and brand impersonation; please investigate this URL.", brand_name or "[BRAND NAME]", detected_date))

    def cell(value: str, ref: str, style: int) -> str:
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)[:32767]
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'

    sheet_rows = "".join(
        f'<row r="{index}" ht="{30 if index == 1 else 48}" customHeight="1">'
        + "".join(cell(value, f"{chr(65 + col)}{index}", 1 if index == 1 else 0) for col, value in enumerate(row))
        + '</row>' for index, row in enumerate(rows, 1)
    )
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    files = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="Phishing Domains" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{rel}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F497D"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf><xf fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{ns}"><dimension ref="A1:E{len(rows)}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="1" width="35" customWidth="1"/><col min="2" max="2" width="55" customWidth="1"/><col min="3" max="3" width="65" customWidth="1"/><col min="4" max="4" width="28" customWidth="1"/><col min="5" max="5" width="18" customWidth="1"/></cols><sheetData>{sheet_rows}</sheetData><autoFilter ref="A1:E{len(rows)}"/></worksheet>',
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + content)
    return output.getvalue()
