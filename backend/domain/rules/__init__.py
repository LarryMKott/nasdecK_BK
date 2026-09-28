# 纯领域业务规则：无 IO、无外部调用、无全局可变状态。
# 与存量实现的对应关系（阶段 1 为纯新增副本，阶段 2 换线后存量副本删除）：
#   health_judge.grade_disk_health  ← backend/health/diskhealth.py::_grade_disk_health
#   fan_rule.*                      ← backend/fans/rules.py（曲线/PWM/决策的纯计算核）
#   alert_rule.evaluate_alerts      ← backend/health/alerts.py::_evaluate_alerts
# 忠实性约束：判定逻辑逐行等价（含默认值/文案），差异仅限——全局滞回态改为随值返回（FanDecision.engaged）、
# IO 结果（全闲置判定/告警配置/阵列卡数据/主板温度）改为参数传入。
