# Pantryfifo · 冰箱临期先吃

分批入库 → FEFO 扣减 → 过期下架。

| 服务 | 端口 |
| --- | --- |
| 前端 | 5300 |
| API | 10300 |

0-1：`shopping_list` / `recipe_suggest` / `temp_zone`。

## 开封剩余小时

- `GET /api/lots/{id}/open-preview`：只报确认后将钉住的剩余小时（默认开封小时 ∩ 日历到期），不动余量。
- `POST /api/lots/{id}/open`：确认开封，`opened_at` + `open_hours` 钉在批上，`qty_remain` 不变。
- 顶条 `/api/alerts` 与层页角标 `/api/fridge` 共用 `engines/assess.py` 同一次判定：原因字为「日历到期」或「开封超时」。
- 开封 / 消费 / 过期下架三路写入都走 `BEGIN IMMEDIATE` 事务串行化，一批只留一种余量、status、开封标记。
- `PUT /api/settings` 改 `open_hours_default` 只影响之后开封的批；已开封批按钉住的小时判定，已超时待下架的批仍会被下架。
- 未开封批只走日历到期。
