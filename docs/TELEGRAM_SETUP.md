# Telegram Bot 接入指南

## 快速开始

### 1. 获取 Bot Token
1. 在 Telegram 搜索 `@BotFather`
2. 发送 `/newbot`，按提示设置名字和用户名
3. 获得 Token（格式：`123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`）

### 2. 配置 Token
编辑 `.env`：
```
TELEGRAM_BOT_TOKEN=你的Token
```

### 3. 启动服务
```bash
python3 main.py
```
看到日志 `[Telegram] Bot 已启动（后台线程）` 即成功。

---

## 群聊使用方式

### 将 Bot 加入群
1. 在 Telegram 群设置 → 添加成员 → 搜索你的 Bot 用户名
2. **必须给 Bot 「管理员」权限**（否则收不到群消息）

### 触发回复
在群里 **@bot用户名** 发送消息，Bot 会回复：
```
@my_customer_service_bot 我想退款怎么操作？
```

也支持**回复 Bot 的消息**触发（无需 @）。

---

## 群 → user_id 映射

默认每个群用群 ID 作为 `user_id`（各群的记忆独立）。

### 自定义 user_id
在群里发送（将群绑定到指定 user_id）：
```
/setid user_001
```
绑定后该群所有对话都使用 `user_id=user_001` 的记忆。

查看当前绑定：
```
/whoami
```

清除绑定（恢复使用群 ID）：
```
/resetid
```

---

## 命令列表

| 命令 | 说明 |
|------|------|
| `/setid <user_id>` | 将当前群绑定到指定 user_id |
| `/resetid` | 清除当前群的 user_id 绑定 |
| `/whoami` | 查看当前群绑定的 user_id |

---

## 故障排查

| 现象 | 原因 |
|------|------|
| Bot 不回复 | 未给 Bot 管理员权限 |
| `TELEGRAM_BOT_TOKEN` 错误 | Token 无效，检查 `.env` |
| 回复很慢 | 同 Web 版，见性能优化记录 |
| 消息被 Bot 重复回复 | Bot 被多次 @，正常现象 |

---

## 记忆隔离说明

- 每个群有独立的 `user_id` → **记忆完全隔离**
- 群 A 的对话记忆不会影响群 B
- 通过 `/setid` 可以让多个群共享同一份记忆（绑定同一个 user_id）
