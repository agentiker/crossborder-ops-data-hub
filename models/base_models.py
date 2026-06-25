"""ORM database models for platform sync and business metrics."""

from sqlalchemy import Column, String, Integer, DateTime, UniqueConstraint, Text
from sqlalchemy import Boolean, Date, JSON, Numeric
from sqlalchemy.sql import func
from core.db import Base


class Inventory(Base):
    """Latest inventory snapshot scoped by platform account."""

    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    sku_id = Column(String(64), nullable=False, index=True)
    product_id = Column(String(64), nullable=False, index=True)
    product_name = Column(String(500))
    sku_name = Column(String(500))
    available_stock = Column(Integer, default=0)
    reserved_stock = Column(Integer, default=0)
    warehouse_id = Column(String(64))
    source_updated_at = Column(DateTime)
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "country",
            "shop_id",
            "seller_id",
            "account_id",
            "sku_id",
            "warehouse_id",
            name="uq_inventory_scope_sku_warehouse",
        ),
    )

    def __repr__(self):
        return f"<Inventory(sku_id={self.sku_id}, stock={self.available_stock})>"


class Product(Base):
    """Lightweight product master scoped by platform account.

    数据来自 POST /product/202309/products/search 已返回的字段（枚举库存时顺手入库，
    零额外 API 调用）。仅存 key properties；品牌/类目/详情需另调 Get Product，本期不做。
    """

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    product_id = Column(String(64), nullable=False, index=True)
    title = Column(String(500))
    status = Column(String(32), index=True)  # ACTIVATE / SELLER_DEACTIVATED / DRAFT ...
    sales_regions = Column(JSON)  # 销售地区列表，如 ["GB", "US"]
    sku_count = Column(Integer, default=0)
    min_price = Column(Numeric(18, 4))  # 该商品 SKU 最低售价（概览用）
    currency = Column(String(8))
    source_create_time = Column(DateTime)  # 商品在平台的创建时间
    source_update_time = Column(DateTime)  # 商品在平台的最后更新时间
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "country",
            "shop_id",
            "seller_id",
            "account_id",
            "product_id",
            name="uq_product_scope_product_id",
        ),
    )

    def __repr__(self):
        return f"<Product(product_id={self.product_id}, status={self.status})>"


class BusinessScope(Base):
    """A named set of shops used as a query view (业务范围/视图).

    单客户、单租户阶段只切 平台/国家/店铺 三个维度，不引入 tenant_id（见 plan/09）。
    本期只做显式列举型 scope（single_shop / shop_group）；规则型（country_platform
    自动展开某平台某国全部店）依赖店铺主数据，放到 plan/08。
    """

    __tablename__ = "business_scopes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 多租户（飞书 app 维度）：scope 属于哪个 account。scope_key 不再全局唯一，
    # 改为 (account_id, scope_key) 联合唯一——各租户独立命名空间（见 plan/09 Phase 3）。
    account_id = Column(
        String(64), nullable=False, default="ecom-app", server_default="ecom-app", index=True
    )
    scope_key = Column(String(64), nullable=False, index=True)  # 稳定 slug，如 tts-id-all
    scope_name = Column(String(200), nullable=False)  # 展示名，如 印尼TikTok全部店
    scope_type = Column(String(32), nullable=False, default="shop_group")  # single_shop / shop_group
    platform = Column(String(32))  # 集合跨平台时为空
    country = Column(String(16))  # 集合跨国时为空
    shop_ids = Column(JSON, nullable=False, default=list)  # 字符串数组
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("account_id", "scope_key", name="uq_business_scope_account_key"),
    )

    def __repr__(self):
        return f"<BusinessScope(account={self.account_id}, scope_key={self.scope_key}, type={self.scope_type})>"


class ConversationScopeBinding(Base):
    """会话级"默认查询范围"持久绑定（飞书菜单切换后，跨会话记住上次选的范围）。

    用户点菜单（08a 文字模式）切换范围时，agent 调 ops_set_scope_binding 写本表；
    之后不带范围词的查询，数据端点凭 open_id 自动读本表注入默认范围（服务端兜底，无读工具）。
    主键 (channel, account_id, open_id)：open_id 取自 openclaw 注入 system prompt 的
    trusted metadata（sender_id=ou_xxx）。当前 channel/account_id 走服务端默认（feishu/ecom-app），
    账号隔离靠 open_id 的 per-app 唯一性；account_id 列保留作多 app 真隔离的扩展位（plan/09）。
    scope_key 为空表示"显式全量"（与"未设置"靠是否有行区分）。单租户阶段不引入 tenant_id（plan/09）。
    """

    __tablename__ = "conversation_scope_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(16), nullable=False, default="feishu", index=True)
    account_id = Column(String(64), nullable=False, index=True)  # ecom-app / ecom-app-gtl
    open_id = Column(String(64), nullable=False, index=True)  # 飞书用户 ou_xxx
    scope_key = Column(String(64), nullable=True)  # None = 显式全量
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "channel", "account_id", "open_id", name="uq_conv_scope_binding"
        ),
    )

    def __repr__(self):
        return (
            f"<ConversationScopeBinding(open_id={self.open_id}, "
            f"scope_key={self.scope_key})>"
        )


class UserRole(Base):
    """单租户内分角色的硬权限上限（plan/14 方案 B 的数据层统一权限闸真相源）。

    open_id→role + allowed_scope_key：boss 看全部数据；operator 被钉死在
    allowed_scope_key 且**不可越界**（区别于 ConversationScopeBinding 的"默认范围记忆"
    可越界）。本表是 services/user_authz 的唯一真相，覆盖三处：看板网站 /board、
    对话侧 web/routes/data.py::_resolve_scope（所有 ops_* 工具）、主动推送（按收件人
    open_id 的 allowed_scope 裁内容）。越界拦截复用 scope_resolution.resolve_filters。

    open_id 是 per-app 的，以选定飞书 app（建议 ecom-app）为准。主键
    (channel, account_id, open_id) 与 ConversationScopeBinding 对齐，account_id 列保留
    作多 app 真隔离的扩展位（plan/09）。先单 allowed_scope_key（YAGNI），将来多范围
    上限再扩成 JSON 数组。boss 忽略 allowed_scope_key；单租户阶段不引入 tenant_id。
    """

    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(16), nullable=False, default="feishu", index=True)
    account_id = Column(String(64), nullable=False, index=True)  # ecom-app / ecom-app-gtl
    open_id = Column(String(64), nullable=False, index=True)  # 飞书用户 ou_xxx
    role = Column(String(16), nullable=False)  # boss / operator
    allowed_scope_key = Column(String(64), nullable=True)  # operator 的硬上限；boss 忽略
    note = Column(String(200), nullable=True)  # 备注（如姓名/岗位），运维可读
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("channel", "account_id", "open_id", name="uq_user_role"),
    )

    def __repr__(self):
        return (
            f"<UserRole(open_id={self.open_id}, role={self.role}, "
            f"allowed_scope_key={self.allowed_scope_key})>"
        )


class WebConversation(Base):
    """Web 对话端的会话（plan/15 Phase A）：左侧会话列表的一项。

    归属人 open_id（飞书 OAuth 登录身份），权限/隔离都以它为准——查询/列表只返回
    自己的会话。与飞书侧 openclaw 对话是两套 runtime，但共用同一权限闸 user_authz +
    同一批 ops_* 取数端点（见 plan/15「两套 runtime 共用数据+权限底座」）。
    """

    __tablename__ = "web_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    open_id = Column(String(64), nullable=False, index=True)  # 归属人，飞书 ou_xxx
    title = Column(String(200), nullable=False, default="新会话")  # 列表展示名（首条消息自动生成/可改）
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), index=True)

    def __repr__(self):
        return f"<WebConversation(id={self.id}, open_id={self.open_id})>"


class WebMessage(Base):
    """Web 对话端的单条消息（plan/15 Phase A）。

    role=user/assistant 存文本；tool_calls_json 记本轮调了哪些 ops_* 工具+参数+结果摘要，
    用于刷新重放「AI 当时查了哪些数据」与排查口径。tool 角色的中间结果不单独落库
    （并入 assistant 的 tool_calls_json），保持会话历史精简。
    """

    __tablename__ = "web_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False, index=True)  # → web_conversations.id
    role = Column(String(16), nullable=False)  # user / assistant
    content = Column(Text, nullable=False, default="")
    tool_calls_json = Column(JSON, nullable=True)  # [{name, arguments, ok}], 可空
    created_at = Column(DateTime, server_default=func.now(), index=True)

    def __repr__(self):
        return f"<WebMessage(conv={self.conversation_id}, role={self.role})>"


class PlatformToken(Base):
    """Platform token persisted per platform account scope."""

    __tablename__ = "platform_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expire_at = Column(DateTime)
    refresh_token_expire_at = Column(DateTime)
    shop_cipher = Column(String(128), index=True)
    token_payload = Column(JSON)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<PlatformToken(scope_key={self.scope_key})>"


class RawAPIResponse(Base):
    """Raw platform API payload for audit, replay, and backfill."""

    __tablename__ = "raw_api_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, index=True)
    resource = Column(String(64), nullable=False, index=True)
    method = Column(String(16), nullable=False)
    path = Column(String(500), nullable=False)
    request_params = Column(JSON)
    request_body = Column(JSON)
    response_payload = Column(JSON)
    http_status = Column(Integer)
    business_code = Column(String(64))
    error = Column(Text)
    fetched_at = Column(DateTime, server_default=func.now(), index=True)

    def __repr__(self):
        return f"<RawAPIResponse(platform={self.platform}, resource={self.resource})>"


class SyncCursor(Base):
    """Incremental sync state per platform account and resource."""

    __tablename__ = "sync_cursors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    resource = Column(String(64), nullable=False, index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    cursor = Column(String(500))
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    last_synced_at = Column(DateTime)
    extra = Column(JSON)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<SyncCursor(scope_key={self.scope_key})>"


class DailyProfit(Base):
    """Deterministic daily profit facts consumed by BI and AI tools."""

    __tablename__ = "fact_profit_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_date = Column(Date, nullable=False, index=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    internal_sku = Column(String(128), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    order_count = Column(Integer, default=0)
    units_sold = Column(Integer, default=0)
    gmv = Column(Numeric(18, 4), nullable=False, default=0)
    product_cost = Column(Numeric(18, 4), nullable=False, default=0)
    ad_cost = Column(Numeric(18, 4), nullable=False, default=0)
    logistics_cost = Column(Numeric(18, 4), nullable=False, default=0)
    commission_fee = Column(Numeric(18, 4), nullable=False, default=0)
    tax_fee = Column(Numeric(18, 4), nullable=False, default=0)
    refund_amount = Column(Numeric(18, 4), nullable=False, default=0)
    other_cost = Column(Numeric(18, 4), nullable=False, default=0)
    gross_profit = Column(Numeric(18, 4), nullable=False, default=0)
    # 阶段3a：展示币种（MVP 各金额已折 CNY）+ 口径区分。profit_kind 入 scope_key，
    # estimated=今早预估利润 / settled=结算后真实利润回填(3b)，同日同店两行并存。
    currency = Column(String(8), nullable=False, server_default="CNY")
    profit_kind = Column(String(16), nullable=False, server_default="estimated", index=True)
    calculated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<DailyProfit(scope_key={self.scope_key}, gross_profit={self.gross_profit})>"


class FactAdSpendDaily(Base):
    """每日广告消耗事实表（结算口径，三项广告费拆分 + 总额）。

    数据源：TikTok Shop Finance 结算交易（202501 statement_transactions）的
    fee_tax_breakdown.fee 三项广告费，按交易 order_create_time 归印尼业务日累加。
    与 fact_profit_daily 解耦：避免和未来利润 flow 抢同一 scope_key 行。
    """

    __tablename__ = "fact_ad_spend_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_date = Column(Date, nullable=False, index=True)  # 印尼业务日（UTC+7 归日）
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    currency = Column(String(8))
    gmv_max_fee = Column(Numeric(18, 4), nullable=False, default=0)  # GMV Max 广告费
    tap_commission = Column(Numeric(18, 4), nullable=False, default=0)  # TAP 达人广告佣金
    affiliate_commission = Column(Numeric(18, 4), nullable=False, default=0)  # 联盟广告佣金
    total_ad_spend = Column(Numeric(18, 4), nullable=False, default=0)  # 三项之和
    transaction_count = Column(Integer, default=0)
    raw_response_id = Column(Integer, nullable=True)
    calculated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<FactAdSpendDaily(scope_key={self.scope_key}, total_ad_spend={self.total_ad_spend})>"


class FactFinanceTransaction(Base):
    """交易级结算费用拆项事实表（TTS Finance 202501 statement_transactions 全字段）。

    粒度 = 单笔结算交易（含结算/调整 adjustment/预留 reserve），按交易 `id` 幂等。一个
    order 可对应多笔交易，故订单级/日级口径靠 GROUP BY order_id / metric_date 聚合得到，
    本表不做合并以免丢失交易粒度。与 fact_ad_spend_daily 解耦并存：日级广告费走旧表（现有
    日报/利润依赖不变），本表补全 51 项扣点子项 + 税拆项 + 交易级汇总，供 #4 扣点监控、#3
    利润的扣点项取数。

    提升列 = 高频查询的头部字段（结算/营收/总费税/运费/调整 + 主佣金/引荐费/交易手续费 +
    三项广告费）；其余 48 项 fee 子项与全部 tax 子项以非零值存入 fee_breakdown/tax_breakdown
    JSON，平台新增费种自动兜底入库、无需改表。所有金额单位 = 行 currency（IDR 等），换算留
    阶段3 汇率服务。
    """

    __tablename__ = "fact_finance_transaction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    # 交易级标识：transaction_id 全局唯一（幂等键来源）；order_id 用于聚合到订单/日
    transaction_id = Column(String(64), nullable=False, index=True)
    order_id = Column(String(64), index=True)
    adjustment_id = Column(String(64), index=True)  # 调整类交易才有
    metric_date = Column(Date, nullable=False, index=True)  # order_create_time 归印尼业务日
    currency = Column(String(8))
    # ── 交易级汇总（fee_tax_amount = 全部费税合计；settlement = 实际结算额）──
    settlement_amount = Column(Numeric(18, 4), nullable=False, default=0)
    revenue_amount = Column(Numeric(18, 4), nullable=False, default=0)
    fee_tax_amount = Column(Numeric(18, 4), nullable=False, default=0)
    shipping_cost_amount = Column(Numeric(18, 4), nullable=False, default=0)
    adjustment_amount = Column(Numeric(18, 4), nullable=False, default=0)
    # ── 头部扣点（#4 扣点率/告警直接取）──
    platform_commission_amount = Column(Numeric(18, 4), nullable=False, default=0)  # 主佣金
    referral_fee_amount = Column(Numeric(18, 4), nullable=False, default=0)  # 引荐费
    transaction_fee_amount = Column(Numeric(18, 4), nullable=False, default=0)  # 交易手续费
    # ── 三项广告费（与 ad_spend_daily 同源，便于利润 join）──
    gmv_max_fee = Column(Numeric(18, 4), nullable=False, default=0)
    tap_commission = Column(Numeric(18, 4), nullable=False, default=0)
    affiliate_commission = Column(Numeric(18, 4), nullable=False, default=0)
    # ── 全量兜底：fee/tax 的所有非零子项原样存 JSON（string→string，保真）──
    fee_breakdown = Column(JSON)
    tax_breakdown = Column(JSON)
    raw_response_id = Column(Integer, nullable=True)
    calculated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return (
            f"<FactFinanceTransaction(transaction_id={self.transaction_id}, "
            f"order_id={self.order_id}, settlement={self.settlement_amount})>"
        )


class FactUnsettledFee(Base):
    """未结算订单的 TikTok 官方**预估**费用（GET /finance/202507/orders/unsettled）。

    与 fact_finance_transaction 同构但语义不同：本表是「结算前预估额」（subject to change），
    反映 TikTok 当前费率政策，用于「今早出昨日预估利润」与（3b）及时费率监控。订单一旦结算
    即从该接口消失 → 采集用**全量替换**（每店每业务日先 DELETE 当日旧行再插全量，见
    services/unsettled_fee_store.replace_unsettled_for_day），预估行随结算自然消退、无需过期任务。
    3b 校准：按 order_id JOIN fact_finance_transaction 做 estimated vs settled 差异分析。
    提升列对齐结算表（estimated_* 顶层汇总 + 三项广告费），其余 fee 子项入 fee_breakdown JSON。
    """

    __tablename__ = "fact_unsettled_fee"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    transaction_id = Column(String(64), nullable=False, index=True)  # 幂等键来源
    order_id = Column(String(64), index=True)  # 聚合到订单/日 + 3b 与结算表 JOIN
    metric_date = Column(Date, nullable=False, index=True)  # order_create_time 归印尼业务日
    currency = Column(String(8))
    # ── 顶层预估汇总（estimated_fee_amount = 预估总费税，不含运费）──
    estimated_fee_amount = Column(Numeric(18, 4), nullable=False, default=0)
    estimated_revenue_amount = Column(Numeric(18, 4), nullable=False, default=0)
    estimated_settlement_amount = Column(Numeric(18, 4), nullable=False, default=0)
    estimated_adjustment_amount = Column(Numeric(18, 4), nullable=False, default=0)
    # ── 三项广告费（从 fee_tax_breakdown.fee 提升，利润里广告费单列、避免与扣点双算）──
    gmv_max_fee = Column(Numeric(18, 4), nullable=False, default=0)
    tap_commission = Column(Numeric(18, 4), nullable=False, default=0)
    affiliate_commission = Column(Numeric(18, 4), nullable=False, default=0)
    # ── 全量兜底：fee 的所有非零子项原样存 JSON（平台新增费种自动入库）──
    fee_breakdown = Column(JSON)
    raw_response_id = Column(Integer, nullable=True)
    fetched_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return (
            f"<FactUnsettledFee(transaction_id={self.transaction_id}, "
            f"order_id={self.order_id}, estimated_fee={self.estimated_fee_amount})>"
        )


class ProductCost(Base):
    """SKU 级产品成本主数据（RMB，含国内头程运费），运营经 CSV 导入维护。

    利润公式的「产品成本」项数据源。MVP 用 seller_sku 关联（OrderLineItem.seller_sku 已有列）；
    成本以 RMB 录入、不折算（利润统一折 CNY 展示）。account_id 维度做多租户隔离。
    马帮开通后（阶段4）可由 stock-do-search-sku-list-new.defaultCost 同步替换 CSV。
    """

    __tablename__ = "product_costs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    account_id = Column(String(64), index=True)
    seller_sku = Column(String(128), nullable=False, index=True)
    unit_cost_rmb = Column(Numeric(18, 4), nullable=False, default=0)  # 含运费
    note = Column(String(500))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "account_id", "platform", "seller_sku", name="uq_product_cost_sku"
        ),
    )

    def __repr__(self):
        return f"<ProductCost(seller_sku={self.seller_sku}, unit_cost_rmb={self.unit_cost_rmb})>"


class ReturnRateConfig(Base):
    """预估退货率配置（三级：default / category / sku，运营可配）。

    阶段3a MVP 只用全店 default 一级（category/sku 列预留 3b 细化）。取数优先级
    sku > category > default(表) > settings.estimated_return_rate_default（见 services/return_rate）。
    预估退货 = 退货率 × 当期 GMV，避免真实退货滞后高估当期利润。
    """

    __tablename__ = "return_rate_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    account_id = Column(String(64), index=True)
    scope_level = Column(String(16), nullable=False, default="default")  # default|category|sku
    scope_value = Column(String(128), nullable=False, default="")  # category 名 / seller_sku；default 为空串
    return_rate = Column(Numeric(8, 6), nullable=False, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "account_id", "platform", "scope_level", "scope_value",
            name="uq_return_rate_scope",
        ),
    )

    def __repr__(self):
        return f"<ReturnRateConfig({self.scope_level}={self.scope_value}, rate={self.return_rate})>"


class Alert(Base):
    """Business alert generated from trusted metrics."""

    __tablename__ = "fact_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    metric_date = Column(Date, index=True)
    alert_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False, default="warning", index=True)
    title = Column(String(200), nullable=False)
    message = Column(Text)
    impact_scope = Column(String(200))
    status = Column(String(16), nullable=False, default="open", index=True)
    payload = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime)

    def __repr__(self):
        return f"<Alert(scope_key={self.scope_key}, type={self.alert_type})>"


class SkuVariant(Base):
    """SKU 级变体主数据（颜色/尺码），数据来自 Get Product 的 skus[].sales_attributes。

    Product 表只存 product 级 key properties（无变体属性，见 Product 注释）；补货采购单需
    「款号-颜色-尺码」，故本表按 sku 级存解析出的颜色/尺码。款号 = 商品（product_id/product_name）。
    快照式：sync_sku_variants 全量覆盖在售商品变体，下架/删除的变体由 prune 清退。
    color/size 从 sales_attributes 按属性名匹配解析；attributes 存全部属性原样兜底。
    """

    __tablename__ = "sku_variants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    sku_id = Column(String(64), nullable=False, index=True)
    product_id = Column(String(64), index=True)
    seller_sku = Column(String(128))
    product_name = Column(String(500))  # 款号（商品标题）
    color = Column(String(128))  # 从 sales_attributes 解析
    size = Column(String(128))   # 从 sales_attributes 解析
    attributes = Column(JSON)    # 全部 sales_attributes 原样 [{name,value_name}]
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<SkuVariant(sku_id={self.sku_id}, color={self.color}, size={self.size})>"


class OrderHeader(Base):
    """Order-level facts scoped by platform account (source for GMV/order count)."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    order_id = Column(String(64), nullable=False, index=True)
    order_status = Column(String(32), index=True)
    currency = Column(String(8))
    # 买家实付总额（payment.total_amount），已付款口径下的 GMV 来源
    total_amount = Column(Numeric(18, 4), nullable=False, default=0)
    is_cod = Column(Boolean, default=False)
    buyer_message = Column(Text)
    warehouse_id = Column(String(64))
    create_time = Column(DateTime, index=True)
    paid_time = Column(DateTime, index=True)
    update_time = Column(DateTime)
    source_updated_at = Column(DateTime)
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "country",
            "shop_id",
            "order_id",
            name="uq_order_scope_order_id",
        ),
    )

    def __repr__(self):
        return f"<OrderHeader(order_id={self.order_id}, total={self.total_amount})>"


class PendingFulfillment(Base):
    """待发货订单快照（order_status=AWAITING_SHIPMENT），按发货时效预警。

    快照式：每次同步全量拉当前所有待发货单覆盖本表，发货后的单下次快照不在结果里即被删除
    （见 services/fulfillment_store.replace_pending_fulfillments）。故本表行集合 = 平台当前
    待发货集合，天然无"幽灵单"（订单发走后 status 停留在 AWAITING_SHIPMENT 的问题）。
    与 orders 表解耦：orders 走 create_time 增量、留历史；本表只反映"此刻还没发的单"。
    """

    __tablename__ = "pending_fulfillments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    order_id = Column(String(64), nullable=False, index=True)
    order_status = Column(String(32), index=True)  # 快照口径恒为 AWAITING_SHIPMENT
    # 发货时效（SLA）：均为 naive UTC。tts=最晚揽收、rts=最晚发货、*_due_time=超时平台自动取消线。
    # 超时分桶判定字段集中在 services/fulfillment_metrics（默认 tts_sla_time，待真店核对可改）。
    tts_sla_time = Column(DateTime, index=True)
    rts_sla_time = Column(DateTime)
    shipping_due_time = Column(DateTime)
    collection_due_time = Column(DateTime)
    delivery_option_name = Column(String(64))  # Economy / Standard / Express
    is_cod = Column(Boolean, default=False)
    total_amount = Column(Numeric(18, 4), nullable=False, default=0)
    currency = Column(String(8))
    item_count = Column(Integer, default=0)  # 该单 line_item 条数（每条=一件）
    first_product_name = Column(String(500))  # 首个行项商品名，列表展示用
    warehouse_id = Column(String(64))
    create_time = Column(DateTime, index=True)
    paid_time = Column(DateTime)
    update_time = Column(DateTime)
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "country",
            "shop_id",
            "order_id",
            name="uq_pending_fulfillment_scope_order",
        ),
    )

    def __repr__(self):
        return f"<PendingFulfillment(order_id={self.order_id}, sla={self.tts_sla_time})>"


class OrderLineItem(Base):
    """Order line-level facts (source for per-SKU units sold and single-SKU GMV).

    202309 模型中每个 line_item 代表售出的一件商品（无 quantity 字段），
    因此某 SKU 的销量 = 该 SKU 的 line_item 条数。
    """

    __tablename__ = "order_line_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    line_item_id = Column(String(64), nullable=False, index=True)
    order_id = Column(String(64), nullable=False, index=True)
    sku_id = Column(String(64), index=True)
    seller_sku = Column(String(128))
    product_id = Column(String(64), index=True)
    product_name = Column(String(500))
    sku_name = Column(String(500))
    sale_price = Column(Numeric(18, 4), default=0)
    original_price = Column(Numeric(18, 4), default=0)
    currency = Column(String(8))
    display_status = Column(String(32), index=True)
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "country",
            "shop_id",
            "line_item_id",
            name="uq_order_line_scope_line_id",
        ),
    )

    def __repr__(self):
        return f"<OrderLineItem(line_item_id={self.line_item_id}, sku_id={self.sku_id})>"


class FulfillmentAlertState(Base):
    """待发货超时告警的去重状态（每「收件人 × 范围」一行，记上次已上报的超时单数）。

    监控巡检高频跑（默认每 30 分钟），但只在「超时单数较上次上报增加 / 从 0 变非 0」时才推送，
    避免同一批超时单反复刷屏（去重判定见 services/fulfillment_alerts.build_decision）。
    state_key = alert_type|account_id|scope_key（scope_key 空串=全量范围），一行一收件人范围。
    与 fact_alerts(Alert) 解耦：Alert 是「业务事实」沉淀，本表只是推送去重的轻量游标。
    """

    __tablename__ = "fulfillment_alert_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String(300), nullable=False, unique=True, index=True)
    alert_type = Column(String(64), nullable=False, default="fulfillment_overdue", index=True)
    account_id = Column(String(64), index=True)  # ecom-app / ecom-app-gtl
    scope_key = Column(String(64), nullable=True)  # None/空 = 全量范围
    last_reported_overdue = Column(Integer, nullable=False, default=0)  # 上次已推送的超时单数
    last_critical = Column(Integer, nullable=False, default=0)  # 上次推送时的临界单数（仅记录）
    last_sent_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return (
            f"<FulfillmentAlertState(state_key={self.state_key}, "
            f"last_reported_overdue={self.last_reported_overdue})>"
        )


class StockAlertState(Base):
    """低库存/断货告警的去重状态（每「收件人 × 范围」一行，记上次已上报的风险 SKU 集合）。

    监控巡检高频跑（默认每 30 分钟），但只在「有新 SKU 跌入风险」时才推送，避免同一批低库存
    SKU 反复刷屏（去重判定见 services/stock_alerts.build_decision）。reported_skus 存 JSON 数组，
    SKU 补货恢复后自动移出集合，将来再次跌入会重新提醒。
    state_key = alert_type|account_id|scope_key（scope_key 空串=全量范围），一行一收件人范围。
    """

    __tablename__ = "stock_alert_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String(300), nullable=False, unique=True, index=True)
    alert_type = Column(String(64), nullable=False, default="stock_low", index=True)
    account_id = Column(String(64), index=True)  # ecom-app / ecom-app-gtl
    scope_key = Column(String(64), nullable=True)  # None/空 = 全量范围
    reported_skus = Column(Text, nullable=False, default="[]")  # 上次已推送的风险 SKU（JSON 数组）
    last_sent_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<StockAlertState(state_key={self.state_key})>"


class FeeRateAlertState(Base):
    """扣点率异常告警的去重状态（每「收件人 × 范围」一行，记上次告警的评估窗口与费率）。

    巡检高频跑，但扣点率评估窗口按天推进，故「同一评估窗口」只告警一次（last_window_end 去重）：
    仅当本次评估窗口比上次更新（eval_window_end > last_window_end）且判异常时才推。费率回落到
    正常后再次异动会因窗口推进而重新提醒。state_key = alert_type|account_id|scope_key。
    """

    __tablename__ = "fee_rate_alert_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String(300), nullable=False, unique=True, index=True)
    alert_type = Column(String(64), nullable=False, default="fee_rate_anomaly", index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(64), nullable=True)
    last_window_end = Column(Date)  # 上次已告警的评估窗口结束业务日
    last_rate = Column(Numeric(8, 6))  # 上次告警的评估费率（审计/对比用）
    last_sent_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<FeeRateAlertState(state_key={self.state_key}, last_window_end={self.last_window_end})>"


class HotsellAlertState(Base):
    """爆单提醒的当日去重状态（每「收件人 × 范围」一行，记当天已报爆单的商品集合）。

    report_date 标记这批 reported_product_ids 属于哪个业务日；跨天后 report_date 不等于今天，
    去重集按空处理（新的一天重新计），同一商品当天只在首次破阈时报一次。
    """

    __tablename__ = "hotsell_alert_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String(300), nullable=False, unique=True, index=True)
    alert_type = Column(String(64), nullable=False, default="hotsell", index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(64), nullable=True)
    report_date = Column(Date)  # reported_product_ids 所属业务日
    reported_product_ids = Column(Text, nullable=False, default="[]")  # 当日已报商品（JSON 数组）
    last_sent_at = Column(DateTime)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<HotsellAlertState(state_key={self.state_key}, report_date={self.report_date})>"


class ReplenishmentConfig(Base):
    """补货系数配置（每「租户 × 范围」一行；缺行则用 settings 默认）。运营可改，不硬编码。

    config_key = account_id|scope_key（scope_key 空=租户级默认）。velocity_days/系数 覆盖
    settings.replenish_* 默认；超级爆品名单另见 SuperHotProduct 表。
    """

    __tablename__ = "replenishment_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(300), nullable=False, unique=True, index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(64), nullable=True)
    velocity_days = Column(Integer)  # None=用默认
    normal_multiplier = Column(Numeric(6, 3))
    superhot_multiplier = Column(Numeric(6, 3))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<ReplenishmentConfig(config_key={self.config_key})>"


class SuperHotProduct(Base):
    """超级爆品名单（人工标记的款，补货量用 superhot 系数 ×）。运营可配。

    一行 = 一个租户(account_id)下的一个商品(product_id)被标超级爆品。is_active=False 即撤标。
    超级爆品按「款(product)」标记 → 该款全部 SKU 补货都用 superhot 系数。
    """

    __tablename__ = "super_hot_products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(64), index=True)
    product_id = Column(String(64), nullable=False, index=True)
    mark_key = Column(String(300), nullable=False, unique=True, index=True)  # account_id|product_id
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    note = Column(String(500))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<SuperHotProduct(account_id={self.account_id}, product_id={self.product_id})>"


class AlertRecipient(Base):
    """主动告警收件人（监控巡检的投递对象）——RECIPIENTS 从代码迁 DB（plan/09 Phase 6）。

    每行 = 一个租户(account_id)下的一个飞书用户(open_id)收某范围(scope_key)的告警。
    scope_key=None → 本租户全量范围（resolve_filters 收口为本租户可见店并集）。
    告警 flow 按本表 is_active 行投递；扫描用 (account_id, scope_key) 隔离取数。
    主键 (channel, account_id, open_id) 与 user_roles / 去重表对齐。
    """

    __tablename__ = "alert_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel = Column(String(16), nullable=False, default="feishu", index=True)
    account_id = Column(String(64), nullable=False, index=True)  # ecom-app / ecom-app-gtl
    open_id = Column(String(64), nullable=False, index=True)  # 飞书用户 ou_xxx
    scope_key = Column(String(64), nullable=True)  # None = 本租户全量范围
    is_active = Column(Boolean, nullable=False, default=True)
    note = Column(String(200), nullable=True)  # 备注（如姓名/岗位）
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("channel", "account_id", "open_id", name="uq_alert_recipient"),
    )

    def __repr__(self):
        return f"<AlertRecipient(account={self.account_id}, open_id={self.open_id})>"
