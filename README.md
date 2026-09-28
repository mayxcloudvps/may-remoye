# mây remote
Xem + điều khiển PC từ xa để chơi game: video H.264, âm thanh, chuột/phím, bàn phím ảo, nhập chữ Unicode.

    server/           Windows server (GUI: server_gui.py, CLI: server.py)
    client_windows/   Windows client (GUI: client_gui.py, CLI: client.py)
    android/          Android client (project Gradle)
    .github/workflows/build.yml   tự build APK + 2 file .exe

## Build bằng GitHub (không cần Android Studio)
1. Tạo repo trên GitHub, đẩy TOÀN BỘ nội dung thư mục này lên (thư mục `.github` phải nằm ở gốc repo).
2. Vào tab **Actions** -> workflow **Build** chạy tự động (hoặc bấm *Run workflow*).
3. Xong (vài phút) mở lần chạy đó -> phần **Artifacts** tải:
   - `MayRemote-apk`  -> MayRemote.apk (cài vào điện thoại, cho phép "cài từ nguồn không rõ")
   - `MayRemoteServer-exe`, `MayRemoteClient-exe`
4. Muốn có trang Releases: `git tag v1.0 && git push --tags` -> file tự lên mục Releases.
APK là bản debug (ký sẵn bằng debug key) nên cài được luôn.

## Dùng
- Server: mở MayRemoteServer.exe -> chỉnh cài đặt -> **Bắt đầu server**. Bấm **Mở firewall** 1 lần. IP hiện sẵn trong cửa sổ.
- Client Windows: MayRemoteClient.exe -> nhập IP + token -> Kết nối. F8 bắt chuột, F11 toàn màn hình.
- Android: nhập IP/port/token. Chạm = chuột trái, 2 ngón = chuột phải, ⌨ = bàn phím ảo, Aa = ô nhập chữ (tiếng Việt OK).
- Cài đặt lưu ở %APPDATA%\MayRemote\.

## Chạy từ source (không cần build exe)
    pip install -r server/requirements.txt && python server/server_gui.py
    pip install -r client_windows/requirements.txt && python client_windows/client_gui.py

## Lưu ý
- Game để Borderless/Windowed. Điều khiển app chạy Admin thì chạy server bằng Admin.
- Exe không ký số nên Windows SmartScreen/antivirus có thể cảnh báo, chọn "Run anyway".
- Qua internet: dùng Tailscale/ZeroTier, đừng mở port thẳng (chưa mã hoá).
- Chưa có: tay cầm ảo (ViGEm), TLS, Opus/UDP.
- 
