---
name: inventory-check
description: 查询跨境电商库存信息，包括库存数量、低库存预警
user-invocable: true
---

当用户询问库存相关问题时（如"库存情况"、"哪些商品缺货"、"库存查询"等），使用此技能。

## 调用方式

使用 `http_get` 工具调用数据中台 API：

```
URL: {{DATA_HUB_URL}}/api/data/inventory
方法: GET
请求头:
  - X-Internal-Token: {{DATA_HUB_TOKEN}}   # 内部令牌，必填
参数:
  - platform: 平台标识，如 tiktok_shop / shopee（可选，不填则跨平台汇总）
  - country: 国家/地区，如 ID / GLOBAL（可选）
  - shop_id: 店铺ID（可选）
  - low_stock_threshold: 低库存阈值，默认10（可选）
```

## 响应格式

```json
{
  "items": [
    {
      "sku_id": "SKU001",
      "product_id": "P001",
      "product_name": "商品名称",
      "sku_name": "SKU名称",
      "available_stock": 50,
      "reserved_stock": 5,
      "warehouse_id": "WH001"
    }
  ],
  "total": 100,
  "low_stock_items": [...]
}
```

## 回复规范

1. 如果有低库存商品（`low_stock_items` 不为空），优先提醒用户
2. 用表格或列表展示库存概况
3. 对低库存商品标注 ⚠️ 警告
4. 如果用户问特定商品，在结果中筛选相关 SKU

## 示例回复

**用户**: 查一下库存

**回复**:
📊 库存概况（共 150 个 SKU）

⚠️ **低库存预警**（3 个 SKU）:
| SKU | 商品名 | 可用库存 |
|-----|--------|---------|
| SKU001 | xxx | 3 |
| SKU002 | yyy | 7 |

✅ 其他商品库存正常。
