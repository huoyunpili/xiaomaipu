# 闲管家只读 API 探针

该目录仅用于 M2A 可行性验证。当前只调用店铺查询、订单列表和订单详情等无副作用接口，不调用发货、改价、库存编辑或商品编辑接口。

## 安全约束

- AppSecret 默认通过隐藏输入读取，不写入脚本、文档或输出。
- 输出不包含姓名、手机号、地址、支付流水号或完整订单号。
- 如使用 `.env.local`，该文件已被根目录 `.gitignore` 排除；探针本身暂不主动读取文件，避免误传。

## 首次验证

```powershell
python scripts/xgj_probe/probe.py stores
python scripts/xgj_probe/probe.py orders
```

脚本会交互式询问 AppKey 和 AppSecret。`orders` 会先查询有效店铺，再以第一家有效店铺读取最近订单摘要。

