# 领域实体：业务对象（frozen dataclass）。to_dict() 的键序与既有 API 响应逐字节一致，
# 供 infrastructure 适配器原样透传（API 契约冻结，见 docs/整洁架构重构开发计划.md 铁律 1）。
