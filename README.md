# 新闻简报 · 个性化新闻简报 Agent

按你的兴趣收集新闻，用 LangChain Agent 生成有来源的中文简报。支持前端、CLI、后台调度和邮件推送。所有业务数据使用本地 JSON 文件，不使用数据库。

![新闻简报前端预览，图中为明确标注的演示简报](docs/preview.jpg)

## 能力

- **通用订阅**：话题、关注关键词、排除关键词、中文/英文新闻、时间范围、条数；AI 是默认模板，也可以订阅财经、汽车、体育等。
- **免费新闻发现**：默认使用 Google News RSS 获取中文新闻，DDGS 补充搜索（DuckDuckGo → Yahoo）。Bing News RSS、GDELT 和自定义 RSS 可选，无需新闻搜索 API Key。
- **后台采集**：每小时或每两小时运行，订阅变更后补采；每天回补近期内容，避免仅按整点窗口抓取造成遗漏。
- **正文提取**：Trafilatura 本地提取，保留摘要和提取状态。不能获取正文时不会凭标题编造。
- **LangChain 工具 Agent**：实际注册并使用 `list_dir`、`read_file`、`search_content`、`write_file`、`bash` 五个工具。
- **JSON 存储**：原子替换、文件锁、URL 去重、损坏文件隔离、任务快照、过期清理。
- **定时简报**：按北京时间生成，每日幂等；重启后在当日计划时间起 6 小时内补跑。
- **邮件**：Resend，保存投递结果；失败重试复用已生成简报。
- **前端**：偏好、简报历史、新闻搜索、采集设置、任务状态、Markdown 导出。

## 环境

- Python **3.11–3.14**，建议 **3.12**（锁定依赖在此版本验证）。
- Node.js **20.19+ 或 22+**，npm。
- macOS / Linux；`bash` 工具依赖 `/bin/bash`。Windows 建议使用 WSL2 或 Docker。
- 正式生成需要支持工具调用的 OpenAI 兼容模型。演示、偏好设置、新闻抓取不需要模型密钥。

## 快速启动

在项目根目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -c requirements.lock -e '.[dev]'
cp .env.example .env
newsbrief init

npm --prefix frontend ci
npm --prefix frontend run build

newsbrief serve
```

打开 http://127.0.0.1:8000。接口文档：http://127.0.0.1:8000/docs。

`newsbrief serve` 同时启动网页/API和后台调度，启动后会采集默认订阅。**每日自动生成和邮件推送默认关闭**，在“订阅偏好”中开启。后台任务需要进程持续运行；笔记本休眠期间不能执行。

如果系统只有 `python3`，先检查 `python3 --version`，确认是受支持的稳定版本，再替换第一条命令。

### 先看演示

```bash
newsbrief demo
newsbrief history
newsbrief serve --no-scheduler
```

也可在空白简报页点击“先看一份演示”。演示内容明确标为虚构，不写入新闻资料库，不调用模型、不发送邮件。`--no-scheduler` 仅适合演示和调试，正式使用请去掉。

### 使用真实新闻与模型

编辑 `.env`：

```dotenv
OPENAI_API_KEY=你的模型密钥
OPENAI_MODEL=gpt-4.1-mini
# 其他兼容供应商按需设置，模型必须支持 tool calling
# OPENAI_BASE_URL=https://your-provider.example/v1
```

修改环境变量后需要重启服务。示例模型名称可替换为你账户实际可用、支持工具调用的模型。

```bash
newsbrief prefs --name "小李" --topics "新能源汽车" --keywords "比亚迪" "动力电池" --exclude "招聘" "二手车"
newsbrief collect
newsbrief generate
newsbrief history
```

`collect` 联网抓取；`generate` **只读取本地新闻，不临时搜索互联网**。已有新闻但缺少模型配置时，任务明确失败；没有符合要求的新闻时，保存一份真实的“暂无内容”简报。

## CLI

全局参数放在子命令之前：

```bash
newsbrief --data-dir ./data --json status
```

| 命令 | 用途 |
| --- | --- |
| `newsbrief init` | 初始化默认配置，不覆盖已有数据 |
| `newsbrief prefs` | 查看完整偏好 |
| `newsbrief prefs --file preferences.json` | 校验并导入完整偏好 |
| `newsbrief prefs --topics "财经" --keywords "半导体" --items 8 --hours 48` | 修改订阅 |
| `newsbrief prefs --gdelt-keywords "semiconductor" "BYD"` | 为 DDGS / GDELT 补充英文检索词 |
| `newsbrief prefs --email "you@example.com" --daily on --mail on --time 08:00` | 开启每日生成与邮件推送 |
| `newsbrief prefs --daily off --mail off` | 暂停每日生成与推送 |
| `newsbrief collect` | 立即补采近期新闻 |
| `newsbrief news --keyword "汽车" --limit 20` | 搜索本地新闻 |
| `newsbrief generate` | 从已有新闻生成简报，默认不发邮件 |
| `newsbrief generate --send` | 生成并发送到本次偏好中的邮箱 |
| `newsbrief history` | 查看历史简报 ID |
| `newsbrief show <简报ID>` | 阅读 Markdown 简报 |
| `newsbrief --json show <简报ID>` | 读取简报完整 JSON |
| `newsbrief send <简报ID>` | 发送或重试同一份正式简报 |
| `newsbrief status` | 新闻数量、模型/邮件配置和来源状态 |
| `newsbrief cleanup` | 立即执行保留期限清理 |
| `newsbrief serve` | 启动网页和调度器 |
| `newsbrief worker` | 仅启动后台调度器 |
| `newsbrief demo` | 创建独立的虚构演示简报 |

用 `--topics` 或 `--keywords` 不带后续值可以清空该列表。命令失败返回非零退出码，适合 bash 脚本编排。

前端、CLI 和后台任务读取同一个 `NEWS_BRIEF_DATA_DIR`。从不同工作目录运行时，建议使用绝对路径，避免误创建另一套数据。