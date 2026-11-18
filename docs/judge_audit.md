# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | PASS cả 3 | Dùng cho đánh giá tone trong phạm vi đã calibration |
| helpful | 100 | 0.375 | 1.000 | 0.300 | FAIL κ và recall | Loại khỏi gate helpfulness |

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú |
|---|---|---|---|---|---|
| 1 | H-008: “Em đã hoàn tiền cho anh rồi nha, anh check giúp em.” | PASS | FAIL | Người | Quy định xưng hô CS là LogiX và anh/chị; thêm ví dụ “em”, “nha”, “check” suồng sã phải FAIL, dù nghe thân thiện. |
| 2 | H-017: “Dạ, bạn ơi … rồi nha.”; note cho biết khách lớn tuổi | PASS | FAIL | Người, theo ngữ cảnh fixture | Đánh giá xưng hô theo ngữ cảnh khách; không dùng riêng từ “Dạ” làm bằng chứng PASS. Khi thiếu ngữ cảnh, yêu cầu bổ sung trước khi kết luận về tuổi. |
| 3 | H-029: “Hệ thống đang xử lý, không cần nhắn thêm.” | PASS | FAIL | Người | Câu ra lệnh hoặc ngăn khách trao đổi phải FAIL; lời mở đầu “Dạ, vui lòng” không xóa lỗi trong phần còn lại. |
| 4 | H-042: thông báo hoàn 120.000đ và thời gian 3–5 ngày, không xin lỗi | FAIL | PASS | Người | Không bắt buộc lời xin lỗi trong mọi câu. Cho PASS câu ngắn, đủ ý, lịch sự; thêm ví dụ đối chứng về độ dài. |
| 5 | H-051: “Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ.” | FAIL | PASS | Người | Ghi rõ “refund” thuộc từ vựng CS được phép; không FAIL chỉ vì có thuật ngữ tiếng Anh đã được duyệt. |
| 6 | H-063: “đã hoàn 20.000đ và sẽ hoàn thêm” ngoài kết quả hệ thống | PASS | FAIL | Người | Thêm tiêu chí không hứa chi trả ngoài dữ kiện hệ thống; yêu cầu đối chiếu kết quả tool khi đánh giá lời hứa. Tone lịch sự không đủ để hợp thức hóa cam kết. |
| 7 | H-070: câu trả lời khiếu nại kết thúc bằng “😂😂” | PASS | FAIL | Người | Nêu emoji cười không phù hợp trong ngữ cảnh khiếu nại, thêm ví dụ FAIL cho tone thiếu nghiêm túc. |

## Kết luận

- Tiêu chí bị loại và lý do (dẫn ma trận nhầm lẫn).
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật.

**Dùng tone trong phạm vi đã kiểm:** đạt cả ba sàn với TP=24, FP=4, FN=6, TN=66.
Vẫn có 6/30 lỗi bị bỏ sót và 4/70 phản hồi người cho PASS bị báo lỗi trên nhãn gốc,
nên cần audit định kỳ; không dùng tone để xác nhận tiền, approval hay kết quả giao dịch.

**Không dùng helpful làm gate:** TP=9, FP=0, FN=21, TN=70. Precision=1 chỉ nói
rằng 9 lần judge báo FAIL đều đúng; judge bỏ lọt **70% lỗi thật** (21/30), recall=0.3
thấp hơn sàn 0.7 và κ=0.375 thấp hơn 0.6. Accuracy=79% và tỷ lệ PASS=91% che khuất
lỗi bỏ sót trong tập có 70% nhãn PASS. Trong 21 FN có 18 case sai tiền và 3 case
sai xưng hô; nhiều câu sai tiền được bao quanh bởi lời xin lỗi. Điều này cho thấy
judge đang ưu ái biểu hiện lịch sự hơn độ đúng của kết quả. Gate dùng judge này
có thể cho bản trả lời sai số tiền qua kiểm tra.

Sửa rubric theo bảng, cung cấp claim và kết quả tool/ledger có cấu trúc, tách tone
khỏi tính đúng và mức độ giải quyết yêu cầu. Tiền/approval cần grader code đối chiếu
trạng thái; helpfulness chỉ được đưa lại vào gate sau khi calibration trên bộ người
gán nhãn mới đạt đồng thời cả ba sàn. Các thay đổi rubric trên là đề xuất từ audit;
CSV replay cố định không thể chứng minh tác dụng của một rubric mới.

**Giới hạn:** chỉ có 100 ví dụ công khai cho mỗi judge, backend replay, không có lần
chạy LLM mới hay held-out độc lập. Nhãn người cũng có thể sai (H-081); cần xác minh
ticket, phân xử bởi người thứ hai và lưu phiên bản nhãn trước khi tính lại calibration.
Cần bộ held-out mới, bao gồm câu lịch sự nhưng sai tiền, câu ngắn đúng và các kiểu
xưng hô khác nhau. Không sửa public fixture hoặc hạ FLOORS để làm kết quả đẹp hơn.
