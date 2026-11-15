# 注册、计费与微信接入

本版本支持外部用户自助注册、独立项目空间、按次扣点、兑换码与微信支付。
内部账号（超级管理员、投标经理、只读成员中 `is_external=false` 的成员）不计费。

## 计费规则

- 每个外部用户注册赠送 `BIDCRAFT_SIGNUP_CREDITS` 点（默认 5）。
- 生成一章扣 `BIDCRAFT_CHAPTER_COST` 点（默认 1）；失败或未写出的章节自动退回。
- 查看、编辑、人工改写、核对、DOCX/PDF/XLSX 导出均免费；只有调用 AI 生成扣点。
- 项目按 `owner_id` 隔离：外部用户只能看到自己的项目；超级管理员可见全部。
- 余额不足时生成接口返回 `402`，前端提示“点数不足，去充值”。

点数变更全部写入 `credit_transactions`，包括注册赠送、充值、兑换、生成消耗、
失败退回与管理员调整，便于对账。

## 注册方式

### 用户名密码

`/register` 开放注册，创建 `manager` 角色、`is_external=true` 的账号并自动登录。
用户名 3–40 位，允许字母、数字、`_ . -`；密码至少 8 位。

### 微信扫码（开放平台网站应用）

1. 在微信开放平台创建“网站应用”，通过审核，取得 AppID / AppSecret。
2. 配置授权回调域，例如 `bid.example.com`；回调地址填
   `https://bid.example.com/auth/wechat/callback`。
3. 设置环境变量：

```bash
export BIDCRAFT_WECHAT_APP_ID=wx...
export BIDCRAFT_WECHAT_APP_SECRET=...
export BIDCRAFT_WECHAT_REDIRECT_URI=https://bid.example.com/auth/wechat/callback
```

登录页“微信扫码登录”会跳转微信 `qrconnect` 页面；首次扫码自动创建账号，
用户名形如 `wx_xxxxxxxxxxxx`，并赠送注册点数。重复扫码复用同一账号。

本地演示可启用 `BIDCRAFT_WECHAT_MOCK=1`，登录页会出现模拟扫码入口
`/auth/wechat/mock`，不会访问微信。

## 充值

### 套餐与兑换码

超级管理员在 `/admin` → **计费与充值** 配置：

- 注册赠送点数、每章消耗点数；
- 充值套餐（名称、点数、价格）；
- 批量生成兑换码（点数、面值、有效期），可线下售卖或发放；
- 查看订单、营收、流通点数，手动调整用户点数。

默认套餐：10 点 ¥9.90、50 点 ¥39.90、120 点 ¥79.90。

### 微信支付 Native

1. 微信支付商户平台开通 Native 支付，获取商户号、API v3 密钥、商户 API 证书
   序列号与私钥 `apiclient_key.pem`，下载平台证书。
2. 配置：

```bash
export BIDCRAFT_WECHAT_PAY_MCH_ID=1900000000
export BIDCRAFT_WECHAT_PAY_SERIAL_NO=商户证书序列号
export BIDCRAFT_WECHAT_PAY_PRIVATE_KEY=/data/certs/apiclient_key.pem
export BIDCRAFT_WECHAT_PAY_PLATFORM_CERT=/data/certs/wechatpay_platform.pem
export BIDCRAFT_WECHAT_PAY_APIV3_KEY=32位APIv3密钥
export BIDCRAFT_WECHAT_PAY_NOTIFY_URL=https://bid.example.com/billing/wechat/notify
```

3. 前端创建订单后展示 `code_url` 二维码并轮询订单状态；微信回调经平台证书
   验签、AES-GCM 解密后入账，按 `out_trade_no` 幂等处理。

本地演示可启用 `BIDCRAFT_WECHAT_PAY_MOCK=1`，订单页出现“模拟支付”，
不会调用微信支付。

## 环境变量汇总

| 变量 | 默认 | 说明 |
|---|---|---|
| `BIDCRAFT_SIGNUP_CREDITS` | 5 | 注册赠送点数 |
| `BIDCRAFT_CHAPTER_COST` | 1 | 每章生成消耗点数 |
| `BIDCRAFT_WECHAT_APP_ID` / `_SECRET` / `_REDIRECT_URI` | 空 | 微信网站应用 |
| `BIDCRAFT_WECHAT_MOCK` | 0 | 模拟微信扫码登录 |
| `BIDCRAFT_WECHAT_PAY_MCH_ID` / `_SERIAL_NO` | 空 | 微信支付商户 |
| `BIDCRAFT_WECHAT_PAY_PRIVATE_KEY` / `_PLATFORM_CERT` | 空 | 证书文件路径 |
| `BIDCRAFT_WECHAT_PAY_APIV3_KEY` / `_NOTIFY_URL` | 空 | API v3 密钥与回调 |
| `BIDCRAFT_WECHAT_PAY_MOCK` | 0 | 模拟支付 |

## 安全与运维

- 密码使用 scrypt；会话 Cookie 为 HttpOnly + SameSite=Lax，生产建议 HTTPS 并设置
  `BIDCRAFT_SECURE_COOKIE=1`。
- 微信 AppSecret、支付私钥、API v3 密钥只从环境/文件读取；模型与知识库密钥
  使用 `BIDCRAFT_HOME/secret.key` 加密入库，备份时一并保存。
- 支付回调必须公网 HTTPS 可达；若置于反向代理后，确保原始请求体与
  `Wechatpay-*` 头透传，不要改写 body。
- 订单与流水表是财务对账依据，不建议直接改库；点数调整请走管理控制台。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 用户名密码注册并登录 |
| GET | `/auth/wechat/start` | 跳转微信扫码 |
| GET | `/auth/wechat/callback` | 微信回调，登录/注册 |
| GET | `/auth/wechat/mock` | 模拟扫码（仅 mock） |
| GET | `/billing/summary` | 余额、套餐、流水、订单、开关 |
| POST | `/billing/orders` | 创建充值订单 |
| POST | `/billing/orders/{id}/mock-pay` | 模拟支付（仅 mock） |
| GET | `/billing/orders/{id}/qr` | 订单二维码 PNG |
| POST | `/billing/redeem` | 兑换码充值 |
| POST | `/billing/wechat/notify` | 微信支付回调 |
| GET/PUT | `/admin/billing` | 计费设置与统计 |
| POST | `/admin/redeem-codes` | 批量生成兑换码 |
| POST | `/admin/users/{id}/credits` | 管理员调整点数 |
