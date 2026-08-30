"""Pure helpers for preparing defensive cloaking/sneaky-redirect reports."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit


STRONG_SIGNALS = ("desktop_benign_mobile_abuse", "direct_benign_google_abuse")

EVIDENCE_LABELS = {
    "pc_screenshot": "Ảnh PC thấy rõ URL",
    "mobile_screenshot": "Ảnh mobile thấy rõ URL",
    "google_screenshot": "Ảnh truy cập từ kết quả Google",
    "f12_details": "Thông số F12/DevTools",
    "curl_details": "Kết quả curl đối chiếu",
    "html_evidence": "HTML và chứng từ liên quan",
    "strong_comparison": "Đối chứng mạnh PC/mobile hoặc direct/Google",
    "reproduction_notes": "Mô tả cách tái hiện",
}


def evidence_readiness(evidence: dict[str, bool]) -> tuple[bool, list[str]]:
    """Return whether every mandatory cloaking evidence group is present."""
    missing = [label for key, label in EVIDENCE_LABELS.items() if not evidence.get(key)]
    return not missing, missing


def normalize_report_url(value: str) -> str:
    """Return a normalized HTTP(S) URL, rejecting unsafe or incomplete input."""
    value = (value or "").strip()
    if not value:
        raise ValueError("URL vi phạm không được để trống.")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL phải dùng http/https và có tên miền hợp lệ.")
    return parsed.geturl()


def classify_cloaking(signals: dict[str, bool]) -> tuple[str, str]:
    """Classify user-supplied observations without overstating certainty."""
    if any(signals.get(name) for name in STRONG_SIGNALS):
        return (
            "strong",
            "Có đối chứng mạnh về cloaking hoặc chuyển hướng có điều kiện; "
            "bên tiếp nhận vẫn cần xác minh độc lập.",
        )
    if signals.get("conditional_difference") or signals.get("alternate_path_abuse"):
        return (
            "suspected",
            "Nghi cloaking/chuyển hướng có điều kiện; cần thêm ảnh đối chứng cùng URL và thời điểm gần nhau.",
        )
    if signals.get("game_only") or signals.get("keyword_mismatch"):
        return (
            "insufficient",
            "Chưa đủ bằng chứng cloaking. Nội dung game hoặc kết quả không liên quan chỉ là tín hiệu spam để đề nghị kiểm tra thêm.",
        )
    return "insufficient", "Chưa có cặp quan sát đủ để lập luận về cloaking."


def generate_cloaking_report(
    *,
    reported_url: str,
    brand: str,
    official_url: str = "",
    keyword: str = "",
    discovered_on: date | str | None = None,
    signals: dict[str, bool] | None = None,
    evidence_names: list[str] | None = None,
    f12_details: str = "",
    curl_details: str = "",
    notes: str = "",
) -> str:
    """Generate a reviewable Vietnamese report draft; never sends or saves it."""
    url = normalize_report_url(reported_url)
    signals = signals or {}
    level, assessment = classify_cloaking(signals)
    discovered = (
        discovered_on.strftime("%d/%m/%Y")
        if isinstance(discovered_on, date)
        else str(discovered_on or "Chưa ghi nhận")
    )
    labels = {
        "desktop_benign_mobile_abuse": "Cùng URL: máy tính hiện trang bình phong, điện thoại hiện nội dung vi phạm.",
        "direct_benign_google_abuse": "Cùng URL: truy cập trực tiếp hiện trang bình phong/404, bấm từ Google hiện nội dung vi phạm.",
        "conditional_difference": "Nội dung hoặc URL đích thay đổi theo thiết bị, referrer, mạng hoặc phiên truy cập.",
        "alternate_path_abuse": "Một đường dẫn con đã quan sát được hiện nội dung vi phạm trong khi trang gốc không hiện.",
        "game_only": "Chỉ quan sát được trang game; chưa quan sát được nội dung cờ bạc hoặc phiên bản dành cho crawler.",
        "keyword_mismatch": "Trang xuất hiện cho từ khóa thương hiệu nhưng nội dung người dùng thấy không liên quan.",
    }
    observations = [f"- {label}" for key, label in labels.items() if signals.get(key)]
    observations = observations or ["- Chưa nhập quan sát đối chứng."]
    evidence = [f"- {name}" for name in (evidence_names or []) if name]
    evidence = evidence or ["- Chưa đính kèm. Cần ảnh thấy rõ thanh địa chỉ, URL và thời điểm kiểm tra."]
    conclusion = {
        "strong": "Nghi vấn mạnh về cloaking hoặc sneaky redirect.",
        "suspected": "Nghi cloaking hoặc chuyển hướng có điều kiện.",
        "insufficient": "Chưa đủ bằng chứng để kết luận cloaking; đề nghị kiểm tra thêm.",
    }[level]
    return "\n".join([
        "BÁO CÁO NGHI CLOAKING / SNEAKY REDIRECT", "",
        f"1. URL cần kiểm tra: {url}",
        f"2. Thương hiệu/từ khóa liên quan: {brand.strip() or 'Chưa xác định'}",
        f"3. Trang chính thức: {official_url.strip() or 'Không áp dụng/chưa xác định'}",
        f"4. Từ khóa tìm kiếm: {keyword.strip() or 'Không ghi nhận'}",
        f"5. Ngày phát hiện: {discovered}", "",
        "6. Quan sát có thể tái hiện:", *observations, "",
        "7. Đánh giá:", f"{conclusion} {assessment}", "",
        "8. Evidence đính kèm:", *evidence, "",
        "9. Thông số F12/DevTools:", f12_details.strip() or "Chưa cung cấp.", "",
        "10. Kết quả curl đối chiếu:", curl_details.strip() or "Chưa cung cấp.", "",
        "11. Ghi chú tái hiện:", notes.strip() or "Không có.", "",
        "12. Đề nghị:",
        "Vui lòng kiểm tra URL bằng cả truy cập trực tiếp và truy cập từ kết quả tìm kiếm, "
        "đồng thời đối chiếu desktop/mobile. Nếu xác nhận hành vi đánh lừa người dùng hoặc "
        "công cụ tìm kiếm, đề nghị áp dụng biện pháp phù hợp theo chính sách của quý đơn vị.",
    ])
