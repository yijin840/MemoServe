# FAQ

## 对接流程？怎么使用 API?

**API 使用步骤：**

1. 在 [https://caas.native.financial/](https://caas.native.financial/) 注册机构账户，如无法访问请提供 IP 地址。
2. 审核通过后，机构方可登录。
3. 机构登录后查看钱包地址，充值（支持 USDT、USDC 等）。
4. 创建 Appkey 和 Secret，可选配置 Webhook 回调地址。
5. 调用 API 进行 KYC、开卡、激活卡、充值等操作，状态变更通过回调通知。

**机构 Dashboard：**

| 环境 | 地址 |
|------|------|
| 测试环境（有IP白名单限制） | https://caas-sandbox.native.financial/ |
| 生产环境（有IP白名单限制） | https://caas.native.financial/ |

**API 域名：**

| 环境 | 地址 |
|------|------|
| 测试环境 | https://api-sandbox.paycrypto.com/ 或 https://api-sandbox.native.financial/ |
| 生产环境 | https://api.paycrypto.com/ 或 https://api.native.financial/ |

**代码参考：** [https://github.com/pay-crypto001/paycrypto-sdk-java](https://github.com/pay-crypto001/paycrypto-sdk-java)

---

## 如何给机构账户充值？资金流程？

- 查看钱包地址后直接转账，自动到账，**建议先小额测试，再大额操作**。
- 每家机构设有一个钱包资金账户，为客户卡片充值时从该账户扣除。
- **办卡手续费** 从「办卡资金池」扣除；**卡片充值金额** 从「卡片充值资金池」扣除。
- 两个资金池之间的划转目前仅平台方可操作。

**流程示例：**
1. 用户发起充值 → 机构向用户收取资金并存入企业钱包。
2. 机构调用接口执行充值操作（账户余额须充足）。
3. 建议提前向钱包账户存入一定金额，用于未来数日业务使用。

---

## 发行卡片需要完成哪一级 KYC？审核时间？实体卡物流时间？

| KYC 级别 | 所需材料 | 审核时长 |
|----------|----------|----------|
| **无 KYC** | 姓名、邮箱、手机号、地址（审核宽松） | 最快 **3 分钟** |
| **标准 KYC** | 基础信息 + 护照文件 | 不超过 **3 分钟** |
| **加强 KYC** | 基础信息 + 护照上传 + 活体认证 | **8 ~ 48 小时** |

---

## Native 卡和 UQ 卡的限额？

### 卡类型对比

| 功能 | Native 虚拟卡 | Native 实体卡 | UQ 虚拟卡 Option 1 | UQ 虚拟卡 Option 2 |
|------|:---:|:---:|:---:|:---:|
| **Apple Pay** | ✅ | ✅ | ❌ | ✅ |
| **KYC（护照）** | ✅ 必须 | ✅ 必须 | ❌ 不需要 | ✅ 必须 |
| **活体认证** | ❌（需自拍持护照） | ❌（需自拍持护照） | ❌ | ✅ 必须 |
| **支付宝（中国大陆用户）** | ❌ | ❌ | ✅ | ✅ |

### Native 卡限额

- 支持 Apple Pay 和 Google Pay
- **消费限额：**
  - 单笔最低：$0.00
  - 单笔最高：$30,000
  - 日限额：$80,000
  - 月限额：$2,000,000
  - 累计限额：$50,000,000
- **ATM 取款限额：**
  - 单次：$2,000
  - 日限：$10,000
  - 累计：$50,000
  - 手续费：4% + $0.99

### UQ 卡限额

- 单笔限额：$20,000
- 日交易次数：100 次
- 日限额：$250,000
- 月限额：$1,000,000
- **ATM 取款：**
  - 单次：$1,500
  - 日限：$1,500（每日最多 6 次）
  - 月限：$15,000
  - 手续费：2%（最低 $1）

### JDB 卡限额

- ATM 日取款总限额：$3,000
- 取款手续费：0.75%
- 单次取款上限：取决于当地 ATM 限制
- ATM 每日最多交易次数：10 次
- 银行消费、POS、线上交易无限额（单笔不超过 $25,000，大额需特殊处理）
- **申请费：$103**（含第一年年费）

---

## 支持哪些主流网站/服务？

以下热门服务均已支持：

> Netflix · Spotify · YouTube · Disney+ · Twitch · Amazon · Google · Discord · Apple.com · OpenAI/ChatGPT · Anthropic Claude · Midjourney · Perplexity.AI · Canva · Notion · Figma · Adobe · Microsoft · Dropbox · Zoom · GitHub · Grab · Cursor

---

## 卡申请资金 vs 卡充值资金账户的区别？

| 账户类型 | 用途 |
|----------|------|
| **卡申请资金（Card Application Fund）** | 扣除办卡手续费 |
| **卡充值资金（Card Top Up Fund）** | 扣除卡片充值金额 |

> ⚠️ 两个账户之间的划转操作，目前**仅平台方**可执行。

---

## 已验证过用户 KYC，还需要重新提交吗？

- **无 KYC / 标准 KYC** 的卡：只需提交凭证（使用参数 `kyc_info`）。
- **加强 KYC** 的卡：**必须重新提交**。

---

## 参数 `acct_no` 是每个用户的唯一编号吗？

**是的。**

---

## 虚拟卡和实体卡的申请流程一样吗？

- 流程基本相同，有细微差别。
- **实体卡**申请时需携带卡号参数；**虚拟卡**不需要。
- 激活 API 两者相同。

---

## 为什么 `card_no` 带星号（`****`）？

| 返回格式 | 含义 |
|----------|------|
| `17182986860745039429`（完整明文） | 卡片**已激活** |
| `1718298686074503****`（脱敏） | 卡片**未激活**，激活后 `****` 替换为末四位 |

---

## 怎么查卡敏感信息？

参考代码：
[BankTest.java#L90](https://github.com/pay-crypto001/paycrypto-sdk-java/blob/main/src/test/java/com/paycrypto/open/api/test/BankTest.java#L90)

---

## 支持 API 冻结、解冻、注销卡吗？

- **冻结 / 解冻**：✅ 支持 API 操作
- **注销**：❌ 必须人工处理

---

## 还没签合同，可以先注册 Sandbox 账户试用吗？

**不可以**，但平台可提供产品截图供参考。

---

## 美元卡和欧元卡的 KYC 参数区别？

EUR 卡的 `country`、`state`、`city`、`address` 字段须填写**欧洲国家地址**。

**示例参数：**

```json
{
    "acct_no": "0513000013",
    "acct_name": "HANS",
    "card_type_id": "60000002",
    "first_name": "SUNILKUMAR",
    "last_name": "HANSRAJ",
    "gender": "male",
    "birthday": "1996-11-01",
    "city": "Stockholm",
    "state": "AHMEDABAD,GUJARAT",
    "country": "SE",
    "nationality": "SE",
    "doc_no": "K1988247",
    "doc_type": "passport",
    "country_code": "86",
    "mobile": "+8615821703553",
    "mail": "test@email.com",
    "address": "123",
    "zipcode": "100028",
    "maiden_name": "zhang xiaohong",
    "cust_tx_id": "202020420",
    "front_doc": "{base64 image}",
    "mix_doc": "{base64 image}"
}
```

> **关键区别：**
> - `country` / `nationality`：EUR 卡填欧洲国家代码（如 `SE`），USD 卡填对应国家（如 `Australia`）
> - `front_doc` 图片大小：USD 卡限制 **2MB**，EUR 卡限制 **1MB**
