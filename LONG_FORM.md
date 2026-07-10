# Chế độ video dài của videoautomation

Chế độ video dài giúp tạo video 30 phút trở lên bằng cách chia nhỏ kịch bản, chia nhỏ TTS và ghép nhanh bằng ffmpeg.

## Khi nào nên bật

- Video dài hơn 30 phút.
- Kịch bản dài, dễ vượt giới hạn token của LLM.
- Audio dài, cần chia nhỏ để TTS ổn định hơn.

## Gợi ý

- Chuẩn bị nhiều clip local trong `material_directory`.
- Dùng voice tiếng Việt nếu nội dung hướng tới người Việt.
- Kiểm tra dung lượng ổ đĩa trước khi tạo video dài.
