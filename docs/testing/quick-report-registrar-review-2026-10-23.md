# Quick Report: registrar, contact và chuỗi gọi — 2026-10-23

## Phạm vi

Review source đang có và package python-whois cài trong backend/.venv.
Không tra domain thật, không xác minh tính cập nhật của các địa chỉ/form trong
bảng tĩnh. Không sửa logic ứng dụng hoặc restart dịch vụ.

## Chuỗi thực thi

POST `/api/v1/quick-reports/checks` chỉ tạo job. Worker gọi
`QuickReportWorker._process_check` → Redis cache theo owner/ngày/engine/URL.
Cache hit dùng kết quả lưu, không gọi lại các hàm mạng dưới đây. Cache miss:

```text
DomainQuickReportAdapter.run(target, config)
  run_cdn_check(target, config)
    normalize_domain(target)
    get_whois_info(domain)
      whois_query_domain(domain)
      whois.whois(query_domain)
    is_cloudflare(who.name_servers)
    detect_cdn(domain)
      đối chiếu suffix local
      dns.resolver.resolve(domain, "CNAME")
      requests.head("http://{domain}/", timeout=5, allow_redirects=False)
      requests.head("https://{domain}/", timeout=5, allow_redirects=False)
    _static_registry_lookup(domain)
    resolve_quick_report_recipients(domain, who, registry, rdap={})
      WHOIS emails → bảng email tĩnh nếu chưa có email
      registrar thuộc WEB_FORM_REGISTRARS thì không chọn email registrar
    nếu chưa có email registrar và WHOIS không nhận diện registrar dùng form:
      get_rdap_abuse_email(domain)
        _load_rdap_bootstrap()
          GET https://data.iana.org/rdap/dns.json, timeout=8
        GET {rdap_base}/domain/{domain}, timeout=10
        tìm entity registrar, rồi entity con abuse/vcard email
      resolve_quick_report_recipients(..., rdap=rdap_result)
    run_cloaking_check(target, mode="full", cfg=config)
      cloaking_detector.probe_http_cloaking(...)
    trả domain/cloudflare/cdn/registrar/registry/recipients/cloaking
  sanitize_quick_report_result → lưu Check/cache
```

Các bước trên chạy tuần tự tại run_cdn_check; detector cloaking có cơ chế riêng.
Registry lookup ở đây chỉ đọc bảng local, không gọi lookup_registry_contact hoặc
raw registry WHOIS. Không chạy get_ip_whois, scan_common_subdomains, kiểm tra
certificate, VirusTotal hoặc gửi report trong nhánh này.

Không shell ra `whois`, `nslookup` hoặc `curl`. Package whois cài hiện tại mặc
định command=False, dùng NICClient/socket WHOIS và timeout 10 giây mỗi socket;
get_whois_info không truyền timeout riêng. Referral có thể phát sinh nhiều lượt,
không có deadline chung 10 giây cho cả pipeline. CNAME dùng resolver mặc định,
không truyền timeout/lifetime riêng. HEAD CDN có verify=False; RDAP requests.get
giữ mặc định TLS verification. Các chi tiết package chỉ xác nhận cho venv local.

API response gọi report_forms/available_providers từ dữ liệu lưu và bảng local;
không chạy WHOIS/RDAP thêm lúc polling. FE không trực tiếp tìm registrar.

## R01 — Cao: mất registrar lấy được từ RDAP

run_cdn_check gán registrar từ WHOIS ở dòng 2266 và trả lại biến đó. Khi fallback
RDAP thành công, chỉ truyền RDAP vào resolver recipient, không cập nhật registrar
trong result. report_forms lại chỉ đọc result.registrar.

Fixture WHOIS lỗi, RDAP trả GoDaddy: resolver biết registrar cần form nên không
trả email; result.registrar vẫn None và API không trả form registrar. Form registry
.com vẫn có — không được nhầm với form registrar bị thiếu.

## R02 — Cao: email WHOIS không phân vai được coi là contact registrar

get_whois_info chỉ lấy w.emails; resolver gộp tất cả email sau lọc privacy/blocklist
thành contact registrar. Parser python-whois local có trường emails dùng regex
email chung; không bảo đảm mọi email là Registrar Abuse Contact. Một email
registrant/admin có thể được chọn và làm run_cdn_check bỏ qua RDAP.

Fixture registrant@owner.example được trả ở channel registrar, RDAP không gọi.
Chuỗi not-an-email cũng được chấp nhận, chặn fallback tương tự. Đây là lỗi định
tuyến/hiển thị contact; bấm Kiểm tra không tự gửi SMTP.

## R03 — Vừa: bảng tĩnh có thể chặn RDAP trước khi RDAP được hỏi

Resolver lần đầu có rdap={} nhưng vẫn chạy lookup_registrar_abuse_email. Nếu có
entry tĩnh, run_cdn_check thấy đã có recipient nên không gọi RDAP nữa.

Thực tế với registrar không dùng form: WHOIS emails → static table → RDAP nếu
cả hai thiếu. Khi RDAP được gọi, resolver thứ hai ưu tiên WHOIS → RDAP → static.
Fixture Namecheap không có emails xác nhận RDAP call_count=0. Cần quy định rõ
nguồn ưu tiên; chưa kiểm tra bảng tĩnh có còn đúng với provider hôm nay hay không.

## R04 — Vừa: RDAP fallback truy vấn nguyên subdomain

normalize_domain giữ login.fixture.com. get_rdap_abuse_email dựng endpoint
/domain/login.fixture.com, không đưa về domain đăng ký. python-whois local có
extract_domain riêng nên WHOIS và RDAP không nhất quán. Fixture xác nhận URL
request này; với server chỉ có object domain đăng ký, fallback có thể trả 404.
Cần phân biệt hostname website với domain dùng tra đăng ký bằng suffix policy.

## R05 — Vừa: parser RDAP chỉ đọc một dạng cây

Chỉ tìm abuse trực tiếp bên dưới entity registrar; bỏ entity abuse ở cấp ngoài
hoặc sâu hơn. Thấy registrar name là dừng vòng lặp, không tìm entity khác nữa.
Fixture entity registrar và abuse là hai sibling trả tên mà không có email.
Đây là giới hạn parser đã tái hiện, chưa đo tần suất ở nhà cung cấp thật.

## R06 — Vừa: lỗi bootstrap tạm thời bị giữ hết vòng đời process

_load_rdap_bootstrap đặt _rdap_bootstrap_loaded=True cả khi timeout/non-200.
Cache rỗng sau lần lỗi đầu khiến mọi fallback sau báo không có RDAP server, không
thử lại trong cùng process. Mock timeout rồi gọi hai lần xác nhận chỉ một GET.
Nên cache thành công và dùng retry/backoff có giới hạn cho lỗi tạm thời.

## Hành vi đúng đã xác nhận

- Registrar WHOIS đã nhận diện là dùng form: không chọn mail registrar, không
  gọi RDAP không cần thiết và report_forms trả form registrar tương ứng.
- Lọc các privacy domain đã liệt kê và inbox blocklist; registry/contact tách
  channel. Các bảng và form routing không gọi mạng.
- Cache hit không probe lại; cache có thể giữ cả kết quả thiếu registrar/contact
  vì pipeline trả dict và vẫn succeeded. WHOIS/RDAP error không được lưu thành
  chẩn đoán riêng của từng nguồn trong result Quick Report hiện tại.

## Kiểm thử

- 9 tình huống mock/local: thứ tự gọi; registrar RDAP bị mất; email WHOIS không
  phân vai; email sai định dạng; static chặn RDAP; form đúng; subdomain RDAP;
  parser sibling abuse; bootstrap timeout. Cả 9 hành vi đã xác nhận.
- Lượt script đầu: 7 đạt, 2 assertion fail do đếm cả form registry .com thay vì
  chỉ channel registrar. Sửa assertion phân biệt channel và chạy lại 2/2 đạt;
  không đổi source để làm test qua.
- test_quick_report_routes.py: 4 đạt, 1 skip do thiếu DB test PostgreSQL cô lập.
- Không chạy full suite/build vì lượt này chỉ thêm báo cáo. Không có mạng thật,
  secret, SMTP/IMAP, deployment hoặc worker restart.

Ưu tiên sửa R01/R02; sau đó thống nhất nguồn ưu tiên và domain tra cứu, mở rộng
parser, rồi xử lý bootstrap retry và chẩn đoán nguồn. Thay đổi engine chung cần
test cả Quick Report và các workflow đang dùng cùng helper.
