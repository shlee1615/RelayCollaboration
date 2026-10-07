# 套件內容

Relay Collaboration 0.5.0 包含本機 Python 來源、HTML/CSS/JavaScript GUI、設定範本、操作文件、驗收測試與 Windows 腳本。Windows x64 執行檔發行包另包含 Relay.exe 與嵌入式 Python runtime；來源 ZIP 不包含執行檔或 runtime。

兩種分享包均不內附 Claude CLI、模型、登入資訊、使用者私有 runtime 或外部服務資料。GUI 使用系統字體，沒有 CDN 依賴。各檔案的 SHA-256 與大小列在 MANIFEST.json；ZIP 本體 SHA-256 列在同名外部 manifest。

EXE 使用 PyInstaller 打包；發行包內 THIRD_PARTY_NOTICES.txt 保留 Python 與 PyInstaller bootloader 的授權文件，BUILD_INFO.json 記錄建置版本。建置方法與 onefile 子程序／DLL 搜尋處理依據 [PyInstaller 官方說明](https://pyinstaller.org/en/v6.15.0/common-issues-and-pitfalls.html)。只有開發端需要 PyInstaller；使用 EXE 的電腦不需要另外安裝 Python 或 PyInstaller。

開發移轉來源的原始清單與工作歷史保留在開發目錄，未納入分享包。封裝腳本只建立本機產物；GitHub 上傳為另行執行的發布步驟，不會自動部署服務。

## Current source preparation

The source snapshot also includes English / Traditional Chinese GUI copy and a
shared Agent Guide. See docs/SHARING.md for the allowlisted source archive and
future publication steps. No project license has been selected; this notice is
not an open-source license grant. Original executable notices remain with their
respective builds. Private runtime, login, local config and development histories
are excluded from the distributable source archive.
