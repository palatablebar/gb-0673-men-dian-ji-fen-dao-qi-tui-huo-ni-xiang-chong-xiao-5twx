# 深度登记 门店积分到期与退货逆向冲销

规模：中型。使用者：连锁零售会员运营专员。核心规则：消费先用最早到期批次；退款按原消费分配逆向恢复，已过期部分不得复活。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 事件采信 | 执行并复核事件采信 | `POST /api/intake` | `app/modules/intake/rules.py` | `tests/test_intake.py` |
| M2 权益规则 | 执行并复核权益规则 | `POST /api/policy` | `app/modules/policy/rules.py` | `tests/test_policy.py` |
| M3 积分归批 | 执行并复核积分归批 | `POST /api/lots` | `app/modules/lots/rules.py` | `tests/test_lots.py` |
| M4 消费到期 | 执行并复核消费到期 | `POST /api/consumption` | `app/modules/consumption/rules.py` | `tests/test_consumption.py` |
| M5 退货回拨 | 执行并复核退货回拨 | `POST /api/refund` | `app/modules/refund/rules.py` | `tests/test_refund.py` |
| M6 余额对账 | 执行并复核余额对账 | `POST /api/reconcile` | `app/modules/reconcile/rules.py` | `tests/test_reconcile.py` |
| M7 期末归档 | 执行并复核期末归档 | `POST /api/archive` | `app/modules/archive/rules.py` | `tests/test_archive.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | GB18030权益中文 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_gb18030_real` |
| TRAP-02 | BOM表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_bom_real` |
| TRAP-08 | 会员前导零 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_member_leading_zero` |
| TRAP-04 | 重复表头 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_repeated_header` |
| TRAP-05 | 缺列记录 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_column_bad` |
| TRAP-14 | 编号冲突 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_duplicate_conflict` |
| TRAP-18 | 空态 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/intake/rules.py` | `tests/test_intake.py::test_empty_three` |
| TRAP-07 | 哨兵 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_sentinel_real` |
| TRAP-11 | UTC归一 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_utc_real` |
| TRAP-06 | 全角积分 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_fullwidth_points` |
| TRAP-23 | 权益半开窗 | 静默错算或证据丢失 | 以可复核规则显式处理 | `app/modules/policy/rules.py` | `tests/test_policy.py::test_rule_end_exclusive` |
| TRAP-25 | 超保留期会员标识暴露 | 凭证原样暴露会员号 | 确定性伪名替代，复核链不断 | `app/modules/archive/rules.py` | `tests/test_voucher.py::test_retention_masks_beyond_period` |
| TRAP-26 | 原始捕获覆盖后断链 | 凭证引用外部证据悬空 | 原消费与当时规则内嵌自包含 | `app/modules/archive/rules.py` | `tests/test_voucher.py::test_rebuild_after_snapshot_overwritten` |
| TRAP-27 | 发布失败丢旧版 | 发布中途覆盖旧凭证 | 事务化发布，同内容幂等 | `app/store.py` | `tests/test_voucher.py::test_publish_failure_keeps_old_version` |

## 对账规则

原始逻辑输入=采纳行+隔离行+空行+重复表头+同值重复。业务计算按 DATA-DICTIONARY.md 分别核验，不能把上级与下级重复加总。所有隔离保留编号、原值、原始行号和稳定原因码。

## 已知不覆盖

数据来自仓内样例或用户粘贴文件，不连接外部业务生产系统。平台不执行实际资金划转或行政审批。
