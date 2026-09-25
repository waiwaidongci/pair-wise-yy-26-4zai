# 开源漏洞披露协作

这是使用 Python 标准库、SQLite 和 `http.server` 实现的保密漏洞协作后台。系统支持报告人、协调员、维护者三种角色，管理受影响产品版本、私密证明材料、保密期限、修复计划、状态历史、延期、通知和公开公告。

## 启动

```bash
python app.py
```

默认端口 `8113`，页面为 <http://127.0.0.1:8113>。首次启动创建示例网关漏洞。可用环境变量 `PORT` 和 `VULN_DB` 调整端口及数据库位置。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖：创建报告、加入维护者、分级、提交修复计划、逐版本复测登记（失败阻断、全通过放行、再次复测保留历史、变更作废重登记、定稿冻结）、解决、阻止提前披露、到期披露并读取公告；同时验证外部用户无权查看、相同产品版本会触发重复报告，以及维护者看不到协调员专用材料。

## 逐版本修复核对

协调员不能只凭一句修复说明结案，必须按受影响版本核对：

- 维护者调用 `POST /api/version-fixes` 按版本登记处理说明、结果（`pass`/`fail`）和复测时间；同一版本再次复测只追加记录，旧结论保留为历史，以最新结果为准。
- 判定规则（`verification.py`）：任一版本失败或未登记时，`fixing -> resolved` 被拒绝并返回失败版本与待复测/作废版本，报告留在修复中；全部版本在当前轮次通过后协调员才能推进到已解决。
- `POST /api/reports/{id}/revise`（协调员或报告人）修改摘要或受影响版本；修复计划通过 `POST /api/fixes` 更新。摘要、版本或计划内容实际变化时轮次 `fix_revision +1`，上一轮所有结论作废，必须按新内容重新登记；无变化的重复提交不触发作废。
- 已解决（及已披露）后计划/摘要/版本冻结，确需调整时协调员先把状态退回 `fixing`。
- `GET /api/reports/{id}?user_id=...` 的 `version_fixes` 字段给出每版状态、最近结果、阻塞项、作废结论和完整历史；页面逐版本展示进度、最近结果和阻塞项。

分层：存储原语在 `database.py`（`version_fixes` 表、`reports.fix_revision`），判定逻辑在 `verification.py`（`evaluate` 纯函数 + `FixVerification` 服务），接口在 `app.py`，展示在 `static/index.html`。

## 接口

- `POST /api/users`、`POST /api/products`、`POST /api/reports`
- `GET /api/duplicates?product_id=...&version=...`
- `POST /api/members`、`POST /api/evidence`
- `POST /api/fixes`、`POST /api/version-fixes`、`POST /api/reports/{id}/revise`
- `POST /api/extensions`
- `POST /api/reports/{id}/status`
- `POST /api/advisories`、`GET /api/reports/{id}/advisory?user_id=...`
- `POST /api/reports/{id}/publish`
- `GET /api/reports/{id}?user_id=...`
- `GET /api/reports/{id}/notifications`

状态流转限制为 `new -> triaged -> fixing -> resolved -> published`，拒绝或回到修复中也有显式规则。披露日期早于保密期限时请求会失败，不会只修改显示状态。
