# MCP_INTEGRATION.md · 麦当劳 MCP 集成说明（麦麦阿姨 · 班底 Zecrew 数字员工）

「麦麦阿姨」（技能名 mcd-auntie）基于**麦当劳中国官方 MCP Server**（`https://mcp.mcd.cn`，Streamable HTTP 协议，Token 在 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) 申请）构建。**我们没有一行自研后端——阿姨的全部能力来自官方 MCP 工具的编排**，不做本地数据缓存，菜单/价格/活动一律实时查询。

## 一、官方工具全景与集成状态

> 本节为**参赛编写时点的申报快照**。麦当劳 MCP 持续迭代（版本日志见官方文档），工具以 `tools/list` 实时返回为准；本技能的编排逻辑不变，新工具出现即按职责自然接入。

### 🍔 点餐（编写时点 12 个，全部已集成）

| 工具 | 用途 | 阿姨职责 |
|---|---|---|
| `query-nearby-stores` | 到店/得来速门店搜索 | 点餐·定门店 |
| `query-meals` | 门店菜单（到店/外送/预约时段两套菜单） | 点餐·产品课与推荐 |
| `query-meal-detail` | 餐品组成、可换组件、特制选项 | 点餐·换着推 |
| `list-nutrition-foods` | 全量营养成分库（能量/蛋白/脂肪/碳水/钠/钙） | 点餐·营养分析 |
| `calculate-price` | 组合算价（含优惠）+ 返回取餐方式列表（堂食/外带） | 点餐·性价比定稿 |
| `create-order` | 创建订单（支持券/特制/随单购/备注/餐具/预约） | 点餐·执行 |
| `cancel-order` | 取消订单（6 种原因码） | 点餐·兜底 |
| `query-order` | 订单详情（状态/取餐码/备注/支付信息） | 点餐·支付核实与交付 |
| `order-list` | 历史订单（门店/餐品明细/实付/取餐方式） | **上岗建档·画像数据源** |
| `query-store-coupons` | 指定门店+订单类型可用券 | 点餐·用券算价 |
| `query-meal-assistance` | 团餐助餐服务 | 备用（团餐场景） |
| `query-promotions` | 团餐满减/满折规则 | 备用（团餐场景） |

### 🚚 外送（编写时点 3 个，全部已集成）

| 工具 | 用途 |
|---|---|
| `delivery-query-addresses` | 配送地址簿（外送下单前置） |
| `delivery-create-address` | 新增配送地址 |
| `delivery-query-stores` | 按地址查可配送门店 |

### 💰 资产·积分与券（编写时点 7 个，全部已集成）

| 工具 | 用途 | 阿姨职责 |
|---|---|---|
| `query-my-account` | 积分余额、本月/下月将过期积分 | 盘家底 |
| `query-my-coupons` | 卡包券清单与到期时间 | 盘家底/主动关怀 |
| `available-coupons` | 当前可领未领的券 | 薅羊毛 |
| `auto-bind-coupons` | 一键领取全部可领券（用户确认后执行） | 薅羊毛·执行 |
| `query-lottery-info` | 抽奖活动信息、消耗规则、奖品列表 | 算积分 |
| `draw-lottery` | 积分抽奖（用户确认后执行） | 算积分·执行 |
| `query-my-prizes` | 抽奖奖品资产 | 盘家底 |

### 🛒 积分商城（编写时点 5 个，全部已集成）

| 工具 | 用途 | 阿姨职责 |
|---|---|---|
| `mall-points-products` | 可兑换商品列表（商品券/实物/周边） | 算积分 |
| `mall-product-detail` | 兑换商品规格详情 | 算积分 |
| `mall-create-order` | 积分兑换下单（用户确认后执行） | 算积分·执行 |
| `mall-order-list` / `mall-order-detail` | 商城订单查询 | 算积分·售后 |

### 📅 活动与通用（编写时点 3 个，全部已集成）

| 工具 | 用途 | 阿姨职责 |
|---|---|---|
| `campaign-calendar` | 当月营销活动日历 | 薅羊毛/主动关怀 |
| `query-survey-coupon` | 订单满意度关联奖券 | 薅羊毛 |
| `now-time-info` | 服务器时间（判早餐/正餐时段） | 点餐·时段判断 |

### 🏢 团餐与派对（编写时点 5 个，备用不进默认流程）

`query-party-city` / `query-party-store` / `query-party-store-date` / `query-party-store-session` / `party-order-create`——生日派对、主题派对、麦麦体验营、品鉴会、读书会的查询与预订全链。用户主动提才走。

## 二、核心调用流程

### 流程 A：上岗建档（首次，只读）

```
order-list ──→ (重点订单) query-order ──→ 生成 10 维用户画像
   │                                            （门店/消费带/品类/搭配/时段/
   │                                             取餐方式/备注习惯/餐具/场景/人数）
   └── 确认后存本地画像文件，成为后续服务默认上下文
```

### 流程 B：点餐主流程（读 → 确认 → 支付 → 交付）

```
now-time-info(判时段) → 门店(画像默认/nearby/delivery)
  → query-meals(拉当期菜单) → 按画像筛选推荐(品类→肉类→搭配→价格带)
  → query-store-coupons + calculate-price(用券算价,含取餐方式)
  → 报方案(订单信息+金额+取餐方式) → 用户确认
  → create-order(takeWayCode堂食/外带 | addressId外送, remark, 用券)
  → 支付链接交用户(扫码付,不开App) → 用户回复 → query-order 核实
  → 取餐码/取餐柜二维码交付用户
  ↘ 反悔: cancel-order(原因码)  ↘ 超时未付: 自动失效
```

### 流程 C：盘家底与主动关怀（只读 + 确认后领券）

```
query-my-account + query-my-coupons + available-coupons + query-my-prizes
  → 麦麦资产负债表(将过期积分/3天内到期券置顶)
  → 定时任务(如每日11:00)主动问餐 + 券到期提醒
  → 用户同意 → auto-bind-coupons
```

### 流程 D：积分最优兑换（读 → 确认 → 写）

```
query-my-account → mall-points-products → mall-product-detail
  → 每积分价值排行 + query-lottery-info 抽奖期望对比
  → 建议 → 用户确认 → mall-create-order / draw-lottery
```

## 三、业务价值

1. **资产防流失**：积分按月过期、券短有效期，多数用户无感知。阿姨统一成一张"账本"，把过期预警前置到还能用的时刻。
2. **决策降本**：同一单餐品，用券/积分/随单购价差可观。全链路算价找出最优路径，省下的都是净收益。
3. **省下开 App 的时间**：从"说一句话"到"点链接付款、收取餐码"，全程不打开麦当劳 App——这是数字员工相对工具的价值。
4. **懂你比懂菜单更重要**：历史订单构建 10 维画像（含备注习惯主动继承），推荐锁定消费带，贴合不越界。
5. **安全自动化**：所有动用真实资产的操作（领券/下单/抽奖/兑换）采用"方案展示 → 用户确认 → 执行"三步协议，AI 只做参谋，支付由用户亲手完成。

## 四、配置示例

见 [`mcp-config.example.json`](./mcp-config.example.json)（Token 一律使用环境变量 `MCD_MCP_TOKEN` 占位，请勿硬编码）。
