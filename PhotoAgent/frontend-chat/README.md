# frontend-chat

對話模式的 UI（Preact + Vite + TypeScript），服務於 `/chat/`。

## Build

build 輸出到 `PhotoAgent/frontend/chat/`。這個資料夾是 build 產物，**不進 git**（列在 `.gitignore`）。

- **部署／啟動 backend**：`run_backend.py` 啟動前會自動 build，不用另外做事。build 失敗就不會啟動；
  要沿用現有的 bundle，加 `--skip-chat-build`。
- **開發中**：改完前端、不想重啟 backend 時，手動跑：

  ```bash
  PhotoAgent/scripts/build_chat_bundle.sh           # build
  PhotoAgent/scripts/build_chat_bundle.sh --check   # 先 typecheck 再 build
  ```

需要 Node.js 20 以上。第一次 build 時，如果沒有 `node_modules`，腳本會自動跑 `npm ci`。
之後 `package.json` 的依賴有變動的話，要自己再跑一次 `npm ci`。

## 切到舊的 branch 或 commit 時

還沒合進這個改動的 branch 和舊 commit 仍然在追蹤 `frontend/chat/`。從現在的 branch 切過去
（checkout、merge、`pull --rebase`）時，git 會擋下來：

```
error: The following untracked working tree files would be overwritten by checkout:
	PhotoAgent/frontend/chat/index.html
	...
```

先刪掉本機 build 出來的檔案，切完再 build 回來：

```bash
rm -rf PhotoAgent/frontend/chat
git checkout <branch>        # 或 merge / pull --rebase
PhotoAgent/scripts/build_chat_bundle.sh
```

如果 merge 舊 branch 時在 `frontend/chat/` 出現 modify/delete 衝突，保留刪除就好：

```bash
git rm -r --cached PhotoAgent/frontend/chat
```
