# UpdateBot - CSSU 网站通知自动更新机器人

自动接收钉钉群消息，通过大模型识别和分类通知，写入[学生会网站](https://github.com/ZJU-CSSU-Dev/home)仓库，每日定时推送。

## 工作流程

```
钉钉群消息 → 手机接收 → SMSForwarder 转发 → Webhook → LLM 分类
                                                          ↓
Fork 仓库 ← 每日 22:30 推送 ← 写入通知文档 ← 是通知？
```

1. SMSForwarder 安装在手机上，监控钉钉群消息并转发到本服务
2. 收到消息后，调用 LLM 判断是否为通知
3. 若是通知，按分类写入 `docs/Notification/` 对应文件
4. 每日定时（默认 22:30）将更新推送到 fork 仓库的 main 分支

## 快速开始

### 前置要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)（Python 包管理器）
- GitHub 账号（需 fork [源仓库](https://github.com/ZJU-CSSU-Dev/home)）
- LLM API Key（任何兼容 OpenAI 接口的服务均可）

### 1. 安装

```bash
cd updatebot
./scripts/install.sh
```

该脚本会：
- 检测/安装 uv
- 创建虚拟环境并安装依赖
- 生成 `.env` 模板文件

### 2. 配置

**编辑 `.env` 文件**（API 密钥）：

```bash
vim .env
```

```ini
LLM_API_KEY=sk-your-api-key-here
GITHUB_PAT=ghp_your-personal-access-token
```

- `LLM_API_KEY`: 大模型 API 密钥
- `GITHUB_PAT`: GitHub Personal Access Token，需勾选 `repo` 权限

**编辑 `config.yaml`**（应用配置）：

```yaml
model:
  url: https://api.openai.com/v1    # LLM API 地址
  name: gpt-4o                       # 模型名称

repo:
  fork_url: https://github.com/YOUR_USERNAME/home.git  # 你的 fork 地址
  upstream_url: https://github.com/ZJU-CSSU-Dev/home.git

schedule:
  update_days: [1, 2, 3, 4, 5]      # 周一至周五更新
  push_time: "22:30"                 # 每日推送时间

paths:
  work_dir: /path/to/home            # 仓库本地路径
  env_file: /path/to/.env            # .env 文件路径
  python_path: python3               # Python 解释器

webhook:
  host: "0.0.0.0"
  port: 8080
  secret: ""                         # SMSForwarder 签名密钥（可选）
```

### 3. 启动

```bash
./scripts/run.sh
```

或指定配置文件：

```bash
./scripts/run.sh --config /path/to/custom-config.yaml
```

### 4. 配置 SMSForwarder

在手机 SMSForwarder App 中：

1. **发送通道**：添加 Webhook 通道
   - WebServer: `http://<服务器IP>:8080/webhook`
   - 请求方式: POST
   - webParams: 留空（使用默认表单）

2. **转发规则**：添加应用通知转发规则
   - 匹配模式: 应用包名 包含 `com.alibaba.android.rimet`（钉钉）
   - 发送通道: 选择上面创建的 Webhook 通道

## 配置说明

### 模型配置

兼容任何 OpenAI API 格式的 LLM 服务，例如：
- OpenAI: `url: https://api.openai.com/v1`
- Azure OpenAI: `url: https://your-resource.openai.azure.com`
- 本地模型: `url: http://localhost:8000/v1`
- 其他第三方: 任何兼容 `/v1/chat/completions` 的接口

### 通知分类

默认 5 个分类，对应网站 `docs/Notification/` 目录下的文件：

| 分类 Key | 名称 | 文件 |
|---------|------|------|
| Academic | 教学事务 | Academic/Academic.md |
| Awards | 评优评先和资助 | Awards/Awards.md |
| Growth | 形策二课 | Growth/PolicyAndSecondCourse.md |
| Research | 学业科研 | Research/SchoolworkResearch.md |
| Career | 就业发展 | Career/Career.md |

可在 `config.yaml` 的 `categories` 段自定义。

### 更新日设置

`schedule.update_days` 使用 ISO 星期编号：
- 1 = 周一
- 2 = 周二
- ...
- 7 = 周日

### 环境变量

所有密钥通过 `.env` 文件配置（已在 `.gitignore` 中忽略）：

| 变量 | 说明 | 必填 |
|------|------|------|
| LLM_API_KEY | 大模型 API 密钥 | 是 |
| GITHUB_PAT | GitHub 个人访问令牌 | 是 |

## 迁移部署

### 迁移到其他服务器

1. 复制整个 `updatebot/` 目录到新服务器
2. 运行 `./scripts/install.sh` 一键配置环境
3. 修改 `.env` 和 `config.yaml` 中的路径和密钥
4. 启动 `./scripts/run.sh`

### 使用 systemd 持久化运行

创建服务文件：

```bash
sudo vim /etc/systemd/system/updatebot.service
```

```ini
[Unit]
Description=UpdateBot - CSSU Notification Auto Updater
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/updatebot
Environment="PATH=/home/your-user/.cargo/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/path/to/updatebot/scripts/run.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable updatebot
sudo systemctl start updatebot
sudo systemctl status updatebot
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 服务信息 |
| `/health` | GET | 健康检查 |
| `/webhook` | POST | SMSForwarder webhook 接收 |
| `/webhook/form` | POST | 表单格式备用端点 |

### 健康检查示例

```bash
curl http://localhost:8080/health
# {"status":"ok","today_synced":"2026-05-13","update_day":true}
```

## 通知卡片格式

写入的通知卡片遵循网站现有格式：

```yaml
cards:
  - ddl: 2026-05-20         # 可选：截止日期
    title: 通知标题
    detail: 通知摘要
    href: https://...        # 链接
    tags:                    # 可选：标签
      - text: 分类标签
        class: tag-category
      - text: 重要
        class: tag-priority
```

## 安全说明

- `.env` 文件已在 `.gitignore` 中排除，不会提交到版本控制
- 建议将服务器防火墙限制为仅允许手机 IP 访问 webhook 端口
- 可选配置 `webhook.secret` 启用 SMSForwarder 签名验证
- GitHub PAT 建议仅授予 `repo` 权限，并定期轮换
