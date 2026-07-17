# 發佈前的清單

1. 結論是：公開 repo 可行，且適合目前免費雲端打包 IPA 的目的；但先做一次公開前 secrets/config audit，不能把「只用內網」當作可公開所有檔案的理由。
2. 執行 `powershell -File scripts/audit_public_repo.ps1`，結果必須為 `PASS`。
3. 確認沒有 Tailscale auth key、OAuth secret、Tailnet 私人 DNS 名稱或 ACL 匯出檔。
4. 確認沒有 ZEGO App ID、App Sign、Server Secret 或舊版 runtime config。
5. 確認沒有 Apple `.p12`、`.p8`、`.mobileprovision`、簽署密碼或開發者帳戶資料。
6. 確認沒有 `host/runtime`、MediaMTX binary、log、影片、截圖或遊戲帳戶資料。
7. GitHub Actions 只建置未簽署 IPA，不加入 Tailscale、Apple 或 ZEGO secrets。
8. 下載 Actions artifact 後才在本機 Windows 簽署，不把已簽 IPA 回傳公開 repo。

