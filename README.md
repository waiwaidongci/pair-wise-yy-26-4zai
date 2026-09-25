# 开源漏洞披露协作

这是使用 Python 标准库、SQLite 和 `http.server` 实现的保密漏洞协作后台。系统支持报告人、协调员、维护者三种角色，管理受影响产品版本、私密证明材料、保密期限、修复计划、逐版本修复核对、状态历史、延期、通知和公开公告。

## 启动

```bash
python app.py
```

默认端口 `8113`，页面为 <http://127.0.0.1:8113>。首次启动创建示例网关漏洞。可用环境变量 `PORT` 和 `VULN_DB` 调整端口及数据库位置。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖：创建报告、加入维护者、分级、提交修复计划、逐版本核对、解决、阻止提前披露、到期披露并读取公告；同时验证外部用户无权查看、相同产品版本会触发重复报告，以及维护者看不到协调员专用材料。

## 接口

- `POST /api/users`、`POST /api/products`、`POST /api/reports`
- `GET /api/duplicates?product_id=...&version=...`
- `POST /api/members`、`POST /api/evidence`
- `POST /api/fixes`、`POST /api/extensions`
- `POST /api/version-checks`、`GET /api/reports/{id}/verification?user_id=...`
- `POST /api/reports/{id}/scope`
- `POST /api/reports/{id}/status`
- `POST /api/advisories`、`GET /api/reports/{id}/advisory?user_id=...`
- `POST /api/reports/{id}/publish`
- `GET /api/reports/{id}?user_id=...`
- `GET /api/reports/{id}/notifications`

状态流转限制为 `new -> triaged -> fixing -> resolved -> published`，拒绝或回到修复中也有显式规则。披露日期早于保密期限时请求会失败，不会只修改显示状态。

## 逐版本修复核对

维护者按受影响版本登记处理说明、结果（`pass`/`fail`）与时间；再次复测追加记录，旧结论保留可查。存在失败项时报告留在修复中并列出失败版本，已解决报告出现失败项会自动退回修复中；全部版本通过后协调员才能推进到已解决，否则请求被拒并列出未通过版本。摘要、受影响版本或修复计划任一变更都会使既有结论作废（按内容指纹判定），需按新内容重新登记。存储（`database.py`）、判定（`verification.py`，纯函数）与页面（`static/index.html`）分离，页面展示每版进度、最近结果与阻塞项。
