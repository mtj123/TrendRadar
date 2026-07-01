# 飞书多维表格自动同步配置

这份文档对应当前仓库里新增的飞书 Base 自动同步能力。

目标：
- `TrendRadar` 抓取完成后
- 自动把最新热榜 / RSS 数据写入飞书多维表格主表 `AI行业动态表`

## 你需要准备的 4 个值

填到 `.env.local` 或环境变量里：

```bash
FEISHU_BASE_ENABLED=true
FEISHU_BASE_APP_ID=cli_xxx
FEISHU_BASE_APP_SECRET=xxx
FEISHU_BASE_APP_TOKEN=bascn_xxx
FEISHU_BASE_MAIN_TABLE_NAME=AI行业动态表
```

注意：
- 上面 `cli_xxx / xxx / bascn_xxx` 只是示例格式，不可直接使用
- 如果 `.env.local` 里仍然保留这些占位值，本地脚本和 `--check-feishu-base` 会直接拦截

可选：

```bash
FEISHU_BASE_MAIN_TABLE_ID=tblxxxx
FEISHU_BASE_AUTO_INIT_TABLE=true
```

含义：
- `FEISHU_BASE_APP_ID`: 飞书自建应用 App ID
- `FEISHU_BASE_APP_SECRET`: 飞书自建应用 App Secret
- `FEISHU_BASE_APP_TOKEN`: 多维表格 app token，通常形如 `bascn_xxx`
- `FEISHU_BASE_MAIN_TABLE_ID`: 具体数据表 ID，通常形如 `tblxxxx`

## 第一步：创建飞书自建应用

1. 打开飞书开放平台  
   [https://open.feishu.cn/](https://open.feishu.cn/)
2. 进入开发者后台
3. 创建应用
4. 选择企业自建应用
5. 创建后进入应用详情页
6. 记下：
   - `App ID`
   - `App Secret`

这两个值分别填到：
- `FEISHU_BASE_APP_ID`
- `FEISHU_BASE_APP_SECRET`

## 第二步：给应用开多维表格权限

在应用后台的权限管理里，给应用添加多维表格相关权限。

你至少需要让它具备这些能力：
- 读取多维表格
- 创建数据表
- 读取字段
- 创建字段
- 读取记录
- 新增记录
- 更新记录

如果你的 Base 在知识库 / Wiki 下面，额外给它开：
- 读取知识库节点信息

注意：
- 权限名在飞书后台里可能会按中文展示
- 不同版本后台文案会略有差异，但核心就是“多维表格读写 + 字段/记录管理”

开完权限后，通常还需要：
- 发布应用版本
- 如果是企业环境，完成管理员授权

## 第三步：拿到 FEISHU_BASE_APP_TOKEN

你现在飞书 Base 的页面链接类似这样：

```text
https://xxx.feishu.cn/wiki/FldIwk78uiHgW1kADQecglZynqd?table=tblpUwuIOXFEEl6b&view=vew672bLbz
```

这个 URL 里：
- `table=tblpUwuIOXFEEl6b` 是 table id
- 但 `app token` 不一定直接出现在当前 wiki URL 里

拿 `app token` 最稳的方式：

1. 打开这份多维表格
2. 找到“更多 / API / 开放平台 / 开发者信息”一类入口
3. 在多维表格详情里查看：
   - `App Token`
   - `Table ID`

通常：
- `App Token` 形如：`bascn_xxx`
- `Table ID` 形如：`tblxxxx`

分别填到：
- `FEISHU_BASE_APP_TOKEN`
- `FEISHU_BASE_MAIN_TABLE_ID`

如果你暂时只拿到了 `App Token`，没拿到 `Table ID` 也可以：
- 保留 `FEISHU_BASE_MAIN_TABLE_NAME=AI行业动态表`
- 同步器会按表名查找
- 找不到时可自动创建

## 第四步：本地写入 .env.local

在项目目录：

```bash
cd /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar
cp .env.local.example .env.local
```

然后编辑 `.env.local`，填入真实值。

## 第五步：初始化主表

先做一次配置检查：

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/check-feishu-base-local.sh
```

也可以先预览当前会同步什么：

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/preview-feishu-base-local.sh
```

如果你要直接把预览结果导出成文件：

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/export-feishu-base-preview-local.sh
```

如果你要看“GitHub AI / AI RSS”专用预览，而不是当前通用配置，可以运行：

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/preview-github-ai-feishu-base-local.sh
```

它会使用：

- [config/config.github-ai.yaml](/Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/config/config.github-ai.yaml)

这份预设会：
- 关闭热榜平台抓取
- 只保留 GitHub Blog / OpenAI / Hugging Face / Hacker News 这类更接近 GitHub AI 事件的数据源

同一套专用入口还有：

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/check-github-ai-feishu-base-local.sh
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/export-github-ai-feishu-base-local.sh
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/init-github-ai-feishu-base-local.sh
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/sync-github-ai-feishu-base-local.sh
```

如果检查通过，再执行初始化：

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/init-feishu-base-local.sh
```

作用：
- 校验飞书凭证
- 定位或创建主表 `AI行业动态表`
- 自动补齐同步所需字段

## 第六步：同步最新抓取结果

```bash
bash /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/sync-feishu-base-local.sh
```

作用：
- 从本地最新抓取结果读取：
  - 热榜数据
  - RSS 数据
- Upsert 到飞书多维表格

## 第七步：正常运行时自动同步

只要 `.env.local` 已配置好，后续正常运行：

```bash
cd /Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar
../../.venv/bin/python -m trendradar
```

抓取完成后会自动同步到飞书 Base。

## GitHub Actions 自动同步

如果你是通过 GitHub Actions 跑 `crawler.yml`，把下面这些 secrets 配到仓库里：

```text
FEISHU_BASE_ENABLED
FEISHU_BASE_APP_ID
FEISHU_BASE_APP_SECRET
FEISHU_BASE_APP_TOKEN
FEISHU_BASE_MAIN_TABLE_ID
FEISHU_BASE_MAIN_TABLE_NAME
FEISHU_BASE_AUTO_INIT_TABLE
```

最少需要：
- `FEISHU_BASE_ENABLED=true`
- `FEISHU_BASE_APP_ID`
- `FEISHU_BASE_APP_SECRET`
- `FEISHU_BASE_APP_TOKEN`

这样工作流跑完 `python -m trendradar` 后，就会自动尝试同步飞书 Base。

## GitHub AI 专用自动同步工作流

仓库里还新增了一条专用工作流：

- [.github/workflows/github-ai-feishu-base.yml](/Users/mtjljx/Documents/Codex/2026-06-18/github-ai/work/TrendRadar/.github/workflows/github-ai-feishu-base.yml)

这条工作流和通用 `crawler.yml` 的区别是：
- 固定使用 `config/config.github-ai.yaml`
- 目标是 `GitHub AI + AI RSS -> 飞书 Base`
- 会先预览，再导出 JSON / CSV artifact，最后执行同步

需要的 secrets：

```text
FEISHU_BASE_ENABLED
FEISHU_BASE_APP_ID
FEISHU_BASE_APP_SECRET
FEISHU_BASE_APP_TOKEN
FEISHU_BASE_MAIN_TABLE_ID
FEISHU_BASE_MAIN_TABLE_NAME
FEISHU_BASE_AUTO_INIT_TABLE
GITHUB_TOKEN_FOR_SEARCH
```

说明：
- `GITHUB_TOKEN_FOR_SEARCH` 建议配置，用于提升 GitHub Search API 配额
- 如果不配置，匿名请求也能跑，但更容易撞限流

## 当前同步策略说明

当前版本是单表主视图策略，主表默认：
- `AI行业动态表`

同步内容包括：
- 热榜标题
- RSS 标题
- 来源
- 来源类型
- 链接
- 排名
- 抓取时间
- 首次/最后出现时间
- 抓取次数
- 摘要/作者（RSS）

还没有做到的部分：
- 根据飞书现有复杂视图自动建完整筛选视图
- 双表关联模型（事件表 / 来源表）
- GitHub stars / forks 的专门 enrich

这些可以在现有基础上继续往前扩。

## 如果初始化失败，优先检查

1. `App ID / App Secret` 是否正确
2. 应用是否已发布并授权
3. 是否开了多维表格读写权限
4. `App Token` 是否真的是目标 Base 的 token
5. 如果 Base 挂在知识库下，是否开了知识库节点读取权限
